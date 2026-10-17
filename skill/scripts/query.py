#!/usr/bin/env python3
"""query.py - советник по моделям Nous: подбор под задачу и обзор рынка (FR-3, FR-6, FR-7).

Читает локальные снапшоты (models.json, scores.json) и config.json; сеть не нужна.
Расчёты (чистые функции) отделены от вывода. Только stdlib, Python 3.10+.

Использование:
    python query.py recommend --task coding [--top N]
    python query.py models [--set flagships|spotlight]

Одни и те же снапшоты и конфигурация дают побайтно одинаковый вывод. От текущего времени
зависит только возраст снапшотов в заголовке; для проверок его можно зафиксировать: --now.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_DIR / "config.json"
DEFAULT_DATA = SKILL_DIR / "data"
SUPPORTED_SCHEMA = 1
SOURCE_LABELS = {"artificial_analysis": "AA", "lmarena": "Arena"}
SOURCE_ORDER = ("artificial_analysis", "lmarena")  # AA приоритетнее Arena
NO_DATA = "нет данных"
ATTRIBUTION = ("Оценки LMArena: лицензия CC-BY-4.0, датасет lmarena-ai/leaderboard-dataset "
               "(Hugging Face), https://lmarena.ai")


class QueryError(Exception):
    """Ошибка пользователя или данных: печатается без трассировки, код выхода 2."""


# ----------------------------------------------------------------- загрузка
def read_json(path: Path, what: str, hint: str) -> dict:
    if not path.exists():
        raise QueryError(f"Нет файла {what}: {path}\n{hint}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise QueryError(f"Не удалось прочитать {what}: {path} ({type(exc).__name__})") from exc
    if not isinstance(data, dict):
        raise QueryError(f"Неожиданный формат {what}: {path}")
    return data


def load_snapshots(data_dir: Path, config_path: Path) -> tuple[dict, dict, dict]:
    config = read_json(config_path, "конфигурации", "Файл config.json входит в скилл.")
    models = read_json(data_dir / "models.json", "снапшота каталога",
                       "Создайте его: python scripts/fetch_models.py")
    scores = read_json(data_dir / "scores.json", "снапшота оценок",
                       "Создайте его: python scripts/fetch_scores.py")
    for name, snap in (("models.json", models), ("scores.json", scores)):
        if snap.get("schema_version") != SUPPORTED_SCHEMA:
            raise QueryError(f"{name}: schema_version {snap.get('schema_version')!r}, "
                             f"поддерживается {SUPPORTED_SCHEMA}. Обновите снапшоты.")
    if "query" not in config:
        raise QueryError("В config.json нет раздела query.")
    return config["query"], models, scores


# ----------------------------------------------------------------- расчёты
def parse_time(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def developer(family: dict) -> str:
    return family["family_id"].split("/", 1)[0]


def exclusion_reason(family: dict) -> str | None:
    """Почему семейство не участвует в рекомендациях, флагманах и spotlight (AC-20, D2, D3)."""
    if family.get("alias"):
        return "alias"
    if not family.get("chat", True):
        return "non_chat"
    return None


def mixed_price(pricing: dict, ratio: dict) -> float | None:
    """prompt x in + completion x out (ТЗ §7). None, если цены нет."""
    p, c = pricing.get("prompt"), pricing.get("completion")
    if p is None or c is None:
        return None
    return p * ratio["in"] + c * ratio["out"]


def percentile(values: list[float], pct: float) -> float:
    """Перцентиль с линейной интерполяцией; для 50 равен медиане."""
    vs = sorted(values)
    if len(vs) == 1:
        return vs[0]
    pos = (len(vs) - 1) * pct / 100.0
    lo, hi = math.floor(pos), math.ceil(pos)
    return vs[lo] + (vs[hi] - vs[lo]) * (pos - lo)


def rated_pool(families: list[dict], scores: dict, metric: str, require_input: str | None = None) -> tuple[list[dict], dict]:
    """Семейства, пригодные для выдачи и имеющие значение метрики. -> (записи, счётчики исключённых)."""
    skipped = {"alias": 0, "non_chat": 0, "no_input": 0, "no_score": 0}
    pool = []
    for fam in families:
        reason = exclusion_reason(fam)
        if reason:
            skipped[reason] += 1
            continue
        if require_input and require_input not in fam.get("modalities", {}).get("input", []):
            skipped["no_input"] += 1
            continue
        value = (scores.get(fam["family_id"]) or {}).get(metric)
        if not value:
            skipped["no_score"] += 1
            continue
        pool.append({"family": fam, "score": value, "source": value["source"], "raw": value["value"]})
    return pool, skipped


def annotate_norm(pool: list[dict], pct: float) -> dict:
    """Нормализация 0-100 и порог качества отдельно по источнику. -> {source: {min,max,threshold,count}}."""
    stats = {}
    for source in {e["source"] for e in pool}:
        raws = [e["raw"] for e in pool if e["source"] == source]
        lo, hi = min(raws), max(raws)
        stats[source] = {"min": lo, "max": hi, "threshold": percentile(raws, pct), "count": len(raws)}
    for e in pool:
        st = stats[e["source"]]
        e["norm"] = 100.0 if st["max"] == st["min"] else (e["raw"] - st["min"]) / (st["max"] - st["min"]) * 100.0
        # «выше медианы» (ТЗ §7); если все значения равны, порог никого не отсекает
        e["passes"] = e["raw"] > st["threshold"] or st["min"] == st["max"]
    return stats


def attach_prices(pool: list[dict], ratio: dict) -> tuple[list[dict], list[dict]]:
    """Добавляет смешанную цену и value. -> (с ценой, без цены); у записей без цены mixed и value равны None."""
    priced, unpriced = [], []
    for e in pool:
        price = mixed_price(e["family"].get("pricing_per_1m", {}), ratio)
        e["mixed"] = price
        e["free"] = price == 0
        if price is None:
            e["value"] = None
            unpriced.append(e)
            continue
        e["value"] = math.inf if price == 0 else e["norm"] / price
        priced.append(e)
    return priced, unpriced


def rank_key(e: dict) -> tuple:
    return (-e["value"], -e["norm"], e["family"]["family_id"])


def price_key(e: dict) -> tuple:
    return (e["mixed"], -e["norm"], e["family"]["family_id"])


def recommend(task: str, families: list[dict], scores: dict, qcfg: dict, top_n: int | None = None) -> dict:
    cat = qcfg["categories"][task]
    metric = cat["metric"]
    direct = metric != "overall"
    by_price = cat.get("rank_by") == "price"
    pct = cat.get("quality_percentile", qcfg["quality_percentile"])
    pool, skipped = rated_pool(families, scores, metric, cat.get("require_input"))
    stats = annotate_norm(pool, pct)
    priced, unpriced = attach_prices(pool, cat["ratio"])
    passing = [e for e in priced if e["passes"]]
    rows = sorted(passing, key=price_key if by_price else rank_key)
    limit = top_n if top_n is not None else qcfg["top_n"]
    shown = rows[:limit]
    return {"task": task, "category": cat, "direct": direct, "by_price": by_price, "stats": stats,
            "pool_size": len(pool), "passing": len(passing), "unpriced": len(unpriced),
            "skipped": skipped, "shown": shown,
            "mixed_sources": len({e["source"] for e in shown}) > 1}


def flagships(families: list[dict], scores: dict, qcfg: dict) -> dict:
    """Флагман разработчика - максимальный общий рейтинг внутри источника, AA приоритетнее Arena (AC-10)."""
    pool, skipped = rated_pool(families, scores, "overall")
    annotate_norm(pool, qcfg["quality_percentile"])
    attach_prices(pool, qcfg["models_value"]["ratio"])
    by_dev: dict[str, list[dict]] = {}
    for e in pool:
        by_dev.setdefault(developer(e["family"]), []).append(e)
    chosen = []
    for dev in sorted(by_dev):
        entries = by_dev[dev]
        for source in SOURCE_ORDER:
            same = [e for e in entries if e["source"] == source]
            if same:
                chosen.append(sorted(same, key=lambda e: (-e["raw"], -e["family"].get("created", 0),
                                                           e["family"]["family_id"]))[0])
                break
    rank = {s: i for i, s in enumerate(SOURCE_ORDER)}
    chosen.sort(key=lambda e: (rank[e["source"]], -e["raw"], e["family"]["family_id"]))
    eligible_devs = {developer(f) for f in families if exclusion_reason(f) is None}
    no_data = sorted(eligible_devs - {developer(e["family"]) for e in chosen})
    return {"rows": chosen, "no_data": no_data, "skipped": skipped,
            "mixed_sources": len({e["source"] for e in chosen}) > 1}


def spotlight(families: list[dict], scores: dict, qcfg: dict, reference_ts: float) -> dict:
    """Свежие семейства (по дате релиза каталога) и необычно дешёвые для своего уровня (AC-11)."""
    scfg = qcfg["spotlight"]
    eligible = [f for f in families if exclusion_reason(f) is None]
    fresh_all = [f for f in eligible if (reference_ts - f.get("created", 0)) / 86400 <= scfg["fresh_days"]]
    fresh_all.sort(key=lambda f: (-f.get("created", 0), f["family_id"]))
    pool, _ = rated_pool(families, scores, "overall")
    annotate_norm(pool, qcfg["quality_percentile"])
    priced, _ = attach_prices(pool, qcfg["models_value"]["ratio"])
    above = [e for e in priced if e["passes"]]
    cheap = []
    if above:
        limit_price = percentile([e["mixed"] for e in above], scfg["cheap_price_percentile"])
        cheap = sorted((e for e in above if e["mixed"] <= limit_price), key=price_key)
    return {"fresh_total": len(fresh_all), "fresh": fresh_all[:scfg["fresh_limit"]],
            "cheap_total": len(cheap), "cheap": cheap[:scfg["cheap_limit"]]}


# ----------------------------------------------------------------- форматирование
def fmt_num(x: float) -> str:
    text = f"{x:.2f}" if abs(x) >= 1 else f"{x:.4f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_money(x: float | None) -> str:
    return NO_DATA if x is None else "$" + fmt_num(x)


def fmt_context(n: int | None) -> str:
    if not n:
        return NO_DATA
    return (f"{n / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M") if n >= 1_000_000 else f"{round(n / 1000)}K"


def fmt_modalities(fam: dict) -> str:
    m = fam.get("modalities") or {}
    return f"вход: {', '.join(m.get('input') or ['?'])} → выход: {', '.join(m.get('output') or ['?'])}"


def fmt_date(ts: int | None) -> str:
    return NO_DATA if not ts else dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def fmt_value(e: dict) -> str:
    if e["value"] is None:
        return NO_DATA
    return "бесплатно" if e["free"] else fmt_num(e["value"])


def score_text(score: dict) -> str:
    """Значение с метрикой, источником и датой; конфигурация - у значения, а не у семейства."""
    text = f"{fmt_num(score['value'])} ({score['metric']}, {SOURCE_LABELS.get(score['source'], score['source'])}, {score['date']})"
    cfg = score.get("config")
    if cfg not in (None, "medium"):
        text += f", конфигурация: {cfg}"
    return text


def price_notes(fam: dict) -> list[str]:
    notes = []
    pricing = fam.get("pricing_per_1m", {})
    for v in fam.get("price_variants") or []:
        label = v["id"].split(":", 1)[1] if ":" in v["id"] else v["id"]
        notes.append(f"вариант {label}: {fmt_money(v.get('prompt'))}/{fmt_money(v.get('completion'))}")
    lp = pricing.get("list_price") or {}
    if lp.get("prompt") and pricing.get("prompt") is not None and lp["prompt"] > pricing["prompt"]:
        notes.append(f"скидка {round((1 - pricing['prompt'] / lp['prompt']) * 100)}% "
                     f"(по прейскуранту {fmt_money(lp['prompt'])}/{fmt_money(lp.get('completion'))})")
    if pricing.get("tiers"):
        notes.append("ступенчатая цена: дороже на длинных запросах")
    return notes


def price_pair(fam: dict) -> str:
    p = fam.get("pricing_per_1m", {})
    return f"{fmt_money(p.get('prompt'))} / {fmt_money(p.get('completion'))}"


def description_line(fam: dict, qcfg: dict) -> str:
    text = " ".join((fam.get("description") or "").split())
    if not text:
        return "описание отсутствует в каталоге"
    limit = qcfg["description_max_chars"]
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def age_text(fetched: dt.datetime, now: dt.datetime) -> tuple[str, int]:
    days = max(0, (now - fetched).days)
    return ("сегодня" if days == 0 else f"{days} дн. назад"), days


def unavailable_text(status: dict, reasons: dict) -> str:
    code = status.get("reason") or ""
    if code in reasons:
        return reasons[code]
    return f"код причины: {code}" if re.fullmatch(r"[a-z_]{1,40}", code) else "причина не указана"


def header_lines(models: dict, scores: dict, qcfg: dict, now: dt.datetime) -> list[str]:
    """Заголовок свежести (AC-5, FR-7): даты и возраст обоих снапшотов, статус источников."""
    m_at, s_at = parse_time(models["fetched_at"]), parse_time(scores["fetched_at"])
    m_age, m_days = age_text(m_at, now)
    s_age, s_days = age_text(s_at, now)
    sources = scores.get("sources", {})
    aa, arena = sources.get("artificial_analysis", {}), sources.get("lmarena", {})
    lines = [f"Каталог: {m_at:%Y-%m-%d %H:%M} UTC ({m_age}), семейств: {models.get('family_count', len(models['families']))}",
             f"Оценки: {s_at:%Y-%m-%d %H:%M} UTC ({s_age}), режим: {scores.get('mode', '?')}"]
    if aa.get("status") == "ok":
        lines.append("AA: данные получены")
    else:
        lines.append(f"AA: {NO_DATA} ({unavailable_text(aa, qcfg['unavailable_reasons'])}); оценки — из LMArena")
    if arena.get("status") == "ok":
        lines.append(f"LMArena: данные получены, лидерборд {arena.get('leaderboard_date', '?')}")
    elif arena.get("status") == "partial":
        parts = "; ".join(f"{k}: {v}" for k, v in sorted((arena.get("incomplete") or {}).items()))
        lines.append(f"LMArena: данные неполные ({parts or 'причина не указана'}), лидерборд {arena.get('leaderboard_date', '?')}")
    else:
        lines.append(f"LMArena: {NO_DATA} ({unavailable_text(arena, qcfg['unavailable_reasons'])}); оценки — только из AA")
    limit = qcfg["max_snapshot_age_days"]
    if max(m_days, s_days) > limit:
        lines.append(f"⚠ Данные старше {limit} дн. Обновите: python scripts/fetch_models.py и python scripts/fetch_scores.py")
    return lines


def skipped_line(skipped: dict, unpriced: int = 0) -> str:
    parts = []
    if skipped.get("non_chat"):
        parts.append(f"не-чат моделей: {skipped['non_chat']}")
    if skipped.get("alias"):
        parts.append(f"алиасов «версия не определена»: {skipped['alias']}")
    if skipped.get("no_input"):
        parts.append(f"без image-входа: {skipped['no_input']}")
    if unpriced:
        parts.append(f"без цены: {unpriced}")
    return ("Не участвуют: " + ", ".join(parts) + ".") if parts else ""


# ----------------------------------------------------------------- вывод команд
def render_recommend(res: dict, qcfg: dict) -> list[str]:
    cat, direct = res["category"], res["direct"]
    col = "Качество" if direct else "Общий рейтинг"
    lines = [f"## Подбор под задачу: {res['task']} — {cat['title']}"]
    ratio = cat["ratio"]
    lines.append(f"Смешанная цена = вход × {ratio['in']} + выход × {ratio['out']} "
                 f"($ за {ratio['in']}M входных + {ratio['out']}M выходных токенов)")
    if res["by_price"]:
        lines.append("Общий рейтинг здесь — только порог отбора; прямого бенчмарка у категории нет.")
    elif not direct:
        lines.append("**Оценка по общему рейтингу:** прямого бенчмарка для этой категории нет, "
                     "качество не специфично для категории.")
    if res["by_price"]:
        lines.append("Ранжирование по смешанной цене среди моделей выше порога общего рейтинга; value не применяется.")
    else:
        lines.append("Ранжирование по value = нормированное качество / смешанная цена; "
                     "в нём только модели выше порога качества.")
    for source in SOURCE_ORDER:
        st = res["stats"].get(source)
        if st:
            lines.append(f"Порог ({SOURCE_LABELS[source]}): {fmt_num(st['threshold'])}, "
                         f"диапазон {fmt_num(st['min'])}–{fmt_num(st['max'])}, моделей с данными: {st['count']}")
    if res["mixed_sources"]:
        tail = "" if res["by_price"] else "; value разных источников сравнивается условно"
        lines.append("**Источники смешаны:** порог и нормализация посчитаны отдельно по AA и Arena" + tail + ".")
    lines.append("")
    if not res["shown"]:
        lines.append(f"{NO_DATA}: нет моделей, пригодных для ранжирования в этой категории.")
    else:
        head = ["#", "Модель", col, "Цена вх / исх, $/1M", "Смеш. цена"]
        if not res["by_price"]:
            head.append("Value")
        head.append("Пометки")
        lines.append("| " + " | ".join(head) + " |")
        lines.append("|" + "---|" * len(head))
        for i, e in enumerate(res["shown"], 1):
            fam = e["family"]
            notes = price_notes(fam)
            if e["free"]:
                notes.insert(0, "бесплатная цена в каталоге")
            if not direct and not res["by_price"]:
                notes.append("оценка по общему рейтингу")
            cells = [str(i), f"{fam['name']} (`{fam['family_id']}`)", score_text(e["score"]),
                     price_pair(fam), fmt_num(e["mixed"])]
            if not res["by_price"]:
                cells.append(fmt_value(e))
            cells.append("; ".join(notes) or "—")
            lines.append("| " + " | ".join(c.replace("|", "/") for c in cells) + " |")
    lines.append("")
    lines.append(f"Моделей с данными: {res['pool_size']}, выше порога: {res['passing']}, показано: {len(res['shown'])}.")
    extra = skipped_line(res["skipped"], res["unpriced"])
    if extra:
        lines.append(extra)
    if res["skipped"].get("no_score"):
        lines.append(f"Нет данных по метрике категории: {res['skipped']['no_score']} моделей.")
    return lines


def model_block(i: int, e: dict, qcfg: dict, tags: list[str]) -> list[str]:
    fam = e["family"]
    title = f"{i}. **{fam['name']}** (`{fam['family_id']}`)" + "".join(f" · {t}" for t in tags)
    notes = price_notes(fam)
    return [title,
            f"   Общий рейтинг: {score_text(e['score'])} · цена вх / исх: {price_pair(fam)} · value: {fmt_value(e)}"
            + (f" · {'; '.join(notes)}" if notes else ""),
            f"   Контекст: {fmt_context(fam.get('context_length'))} · {fmt_modalities(fam)} · релиз: {fmt_date(fam.get('created'))}",
            f"   {description_line(fam, qcfg)}"]


def render_flagships(res: dict, qcfg: dict, spot_ids: set[str]) -> list[str]:
    lines = ["## Флагманы разработчиков", f"Правило: {qcfg['flagship_rule']}.",
             f"Value здесь — нормированный общий рейтинг / (вход × {qcfg['models_value']['ratio']['in']} + "
             f"выход × {qcfg['models_value']['ratio']['out']}); пул нормализации — все модели с общим рейтингом. "
             "Рядом с флагманом всегда его цена."]
    if res["mixed_sources"]:
        lines.append("**Источники смешаны:** сначала флагманы с рейтингом AA, затем с Arena; "
                     "значения разных источников напрямую не сопоставимы.")
    lines.append("")
    if not res["rows"]:
        lines.append(f"{NO_DATA} для определения флагманов.")
    for i, e in enumerate(res["rows"], 1):
        tags = ["также в spotlight"] if e["family"]["family_id"] in spot_ids else []
        lines += model_block(i, e, qcfg, tags)
    lines.append("")
    if res["no_data"]:
        lines.append(f"Нет данных для определения флагмана ({len(res['no_data'])}): {', '.join(res['no_data'])}.")
    extra = skipped_line(res["skipped"])
    if extra:
        lines.append(extra)
    return lines


def render_spotlight(res: dict, qcfg: dict, scores: dict, flag_ids: set[str], ref_date: str) -> list[str]:
    scfg = qcfg["spotlight"]
    lines = ["## Spotlight: свежие и необычно дешёвые модели",
             "Только факты из каталога; без курации и пояснений «почему примечательна».", "",
             f"### Свежие релизы (за {scfg['fresh_days']} дн. до снапшота каталога {ref_date})"]
    if not res["fresh"]:
        lines.append(f"{NO_DATA}: свежих семейств нет.")
    for i, fam in enumerate(res["fresh"], 1):
        tags = ["также флагман"] if fam["family_id"] in flag_ids else []
        title = f"{i}. **{fam['name']}** (`{fam['family_id']}`)" + "".join(f" · {t}" for t in tags)
        overall = (scores.get(fam["family_id"]) or {}).get("overall")
        notes = price_notes(fam)
        lines += [title,
                  f"   Релиз: {fmt_date(fam.get('created'))} · цена вх / исх: {price_pair(fam)}"
                  + (f" · {'; '.join(notes)}" if notes else ""),
                  f"   Контекст: {fmt_context(fam.get('context_length'))} · {fmt_modalities(fam)}",
                  f"   Общий рейтинг: {score_text(overall) if overall else NO_DATA}",
                  f"   {description_line(fam, qcfg)}"]
    if res["fresh_total"] > len(res["fresh"]):
        lines.append(f"Показано {len(res['fresh'])} из {res['fresh_total']}.")
    lines += ["", f"### Необычно дёшево для своего уровня (рейтинг выше порога источника, "
                  f"цена в нижних {scfg['cheap_price_percentile']}% среди таких моделей)"]
    if not res["cheap"]:
        lines.append(f"{NO_DATA}: подходящих моделей нет.")
    for i, e in enumerate(res["cheap"], 1):
        tags = ["также флагман"] if e["family"]["family_id"] in flag_ids else []
        lines += model_block(i, e, qcfg, tags)
    if res["cheap_total"] > len(res["cheap"]):
        lines.append(f"Показано {len(res['cheap'])} из {res['cheap_total']}.")
    return lines


# ----------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Советник по моделям Nous")
    ap.add_argument("--data-dir", type=Path, default=DEFAULT_DATA, help="папка со снапшотами")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--now", help="зафиксировать текущее время (ISO 8601) для воспроизводимых проверок")
    sub = ap.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("recommend", help="подбор моделей под категорию задачи")
    rec.add_argument("--task", required=True)
    rec.add_argument("--top", type=int, help="сколько моделей показать")
    mod = sub.add_parser("models", help="обзор рынка: флагманы и spotlight")
    mod.add_argument("--set", dest="set_name", choices=["flagships", "spotlight"])
    return ap


def run(args: argparse.Namespace) -> list[str]:
    qcfg, models, scores = load_snapshots(args.data_dir, args.config)
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    families, score_map = models["families"], scores.get("models", {})
    out = ["# Советник по моделям Nous", *header_lines(models, scores, qcfg, now), ""]
    if args.command == "recommend":
        if args.task not in qcfg["categories"]:
            raise QueryError(f"Неизвестная категория {args.task!r}. Доступные: {', '.join(sorted(qcfg['categories']))}")
        if args.top is not None and args.top < 1:
            raise QueryError("--top должен быть больше нуля")
        out += render_recommend(recommend(args.task, families, score_map, qcfg, args.top), qcfg)
    else:
        ref_ts = parse_time(models["fetched_at"]).timestamp()
        flag = flagships(families, score_map, qcfg)
        spot = spotlight(families, score_map, qcfg, ref_ts)
        flag_ids = {e["family"]["family_id"] for e in flag["rows"]}
        spot_ids = {f["family_id"] for f in spot["fresh"]} | {e["family"]["family_id"] for e in spot["cheap"]}
        ref_date = parse_time(models["fetched_at"]).strftime("%Y-%m-%d")
        if args.set_name in (None, "flagships"):
            out += render_flagships(flag, qcfg, spot_ids)
        if args.set_name is None:
            out.append("")
        if args.set_name in (None, "spotlight"):
            out += render_spotlight(spot, qcfg, score_map, flag_ids, ref_date)
    if scores.get("sources", {}).get("lmarena", {}).get("status") != "unavailable":
        out += ["", ATTRIBUTION]
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", newline="\n")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        lines = run(args)
    except QueryError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
