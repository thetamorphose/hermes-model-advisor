#!/usr/bin/env python3
"""query.py - советник по моделям Nous: подбор под задачу и обзор рынка (FR-3, FR-6, FR-7).

Читает локальные снапшоты (models.json, scores.json) и config.json; сеть не нужна.
Расчёты (чистые функции) отделены от вывода. Только stdlib, Python 3.10+.

Использование:
    python query.py recommend --task coding [--top N]
    python query.py models [--set flagships|spotlight]
    python query.py show <модель>
    python query.py compare <модель> <модель> [...]
    python query.py search <часть имени> [--limit N]

Одни и те же снапшоты и конфигурация дают побайтно одинаковый вывод. От текущего времени
зависит только возраст снапшотов в заголовке; для проверок его можно зафиксировать: --now.
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import hashlib
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
TRANSLATION_LABEL = "машинный перевод"
METRIC_LABELS = (("overall", "Общий рейтинг"), ("coding", "Программирование (coding)"),
                 ("agentic", "Агентные задачи (agentic)"), ("vision", "Мультимодальность (vision)"))
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


def load_optional(path: Path, what: str) -> tuple[dict, str | None]:
    """Необязательный файл (name_map.json, descriptions_ru.json): нет файла - пусто; битый - пусто и предупреждение."""
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, f"{what}: файл не удалось прочитать, он проигнорирован"
    if not isinstance(data, dict):
        return {}, f"{what}: неожиданный формат, файл проигнорирован"
    if data.get("schema_version") != SUPPORTED_SCHEMA:
        return {}, f"{what}: schema_version {data.get('schema_version')!r}, поддерживается {SUPPORTED_SCHEMA}; файл проигнорирован"
    return data, None


def load_translations(data_dir: Path) -> tuple[dict, str | None]:
    data, warning = load_optional(data_dir / "descriptions_ru.json", "descriptions_ru.json")
    entries = data.get("descriptions")
    return (entries if isinstance(entries, dict) else {}), warning


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


def model_keys(fam: dict) -> list[str]:
    ids = [fam["family_id"], fam.get("representative_id") or "", *fam.get("variant_ids", [])]
    return sorted({i.lstrip("~").lower() for i in ids if i})


def resolve_model(text: str, families: list[dict]) -> dict:
    """Семейство по id, id варианта или алиаса (с `~` и без), точному имени либо однозначной части имени (AC-13, AC-14)."""
    key = " ".join(text.lower().split()).lstrip("~")
    if not key:
        raise QueryError("Пустое имя модели.")
    exact = [f for f in families if key in model_keys(f)]
    if not exact:  # id без разработчика: `claude-fable-5`
        exact = [f for f in families if key in {k.split("/", 1)[-1] for k in model_keys(f)}]
    if not exact:
        exact = [f for f in families if f.get("name", "").lower() == key]
    if not exact:
        tokens = key.split()
        exact = [f for f in families
                 if all(t in " ".join([*model_keys(f), f.get("name", "").lower()]) for t in tokens)]
    if len(exact) == 1:
        return exact[0]
    ids = sorted(f["family_id"] for f in exact)
    if len(ids) > 1:
        shown = ", ".join(f"`{i}`" for i in ids[:10]) + (f" и ещё {len(ids) - 10}" if len(ids) > 10 else "")
        raise QueryError(f"Запросу {text!r} соответствует несколько моделей ({len(ids)}): {shown}. Уточните id.")
    close = difflib.get_close_matches(key, sorted({k for f in families for k in model_keys(f)}), n=5, cutoff=0.6)
    hint = f" Похожие: {', '.join(f'`{c}`' for c in close)}." if close else ""
    raise QueryError(f"Модель {text!r} не найдена в каталоге. Найти по части имени: search <текст>.{hint}")


def search_families(terms: list[str], families: list[dict]) -> list[dict]:
    tokens = [t for t in " ".join(terms).lower().lstrip("~").split() if t]
    if not tokens:
        raise QueryError("Пустой поисковый запрос.")
    hits = [f for f in families if all(t in " ".join([*model_keys(f), f.get("name", "").lower()]) for t in tokens)]
    return sorted(hits, key=lambda f: f["family_id"])


def reference_stats(families: list[dict], scores: dict, qcfg: dict) -> dict:
    """Эталонные распределения для правил плюсов и минусов: только чат-модели без алиасов (как в рекомендациях).

    -> {"metrics": {metric: {source: {top, median, count}}}, "output": {high, low, count}}
    """
    pcfg = qcfg["pros_cons"]
    metrics = {}
    for metric, _ in METRIC_LABELS:
        pool, _skipped = rated_pool(families, scores, metric)
        per_source = {}
        for source in SOURCE_ORDER:
            raws = [e["raw"] for e in pool if e["source"] == source]
            if raws:
                per_source[source] = {"top": percentile(raws, pcfg["top_percentile"]),
                                      "median": percentile(raws, pcfg["median_percentile"]), "count": len(raws)}
        metrics[metric] = per_source
    outs = [f["pricing_per_1m"]["completion"] for f in families
            if exclusion_reason(f) is None and f.get("pricing_per_1m", {}).get("completion") is not None]
    output = {"count": len(outs)}
    if outs:
        output["high"] = percentile(outs, pcfg["expensive_output_percentile"])
        output["low"] = percentile(outs, pcfg["cheap_output_percentile"])
    return {"metrics": metrics, "output": output}


def pros_cons(fam: dict, score_map: dict, refs: dict, qcfg: dict) -> tuple[list[str], list[str]]:
    """Плюсы и минусы - только детерминированные правила из config.json (pros_cons), без свободного текста (AC-14)."""
    pcfg = qcfg["pros_cons"]
    pros, cons = [], []
    own = score_map.get(fam["family_id"]) or {}
    if not any(own.get(m) for m, _ in METRIC_LABELS):
        cons.append("нет оценок ни в одном бенчмарке")
    for metric, _label in METRIC_LABELS:
        score = own.get(metric)
        ref = refs["metrics"][metric].get(score["source"]) if score else None
        if not ref or ref["count"] < pcfg["min_pool_size"]:
            continue
        src = SOURCE_LABELS.get(score["source"], score["source"])
        if score["value"] >= ref["top"]:
            pros.append(f"в верхних {100 - pcfg['top_percentile']}% по {score['metric']} ({src}): "
                        f"{fmt_num(score['value'])} при пороге {fmt_num(ref['top'])}")
        elif score["value"] < ref["median"]:
            cons.append(f"ниже медианы по {score['metric']} ({src}): "
                        f"{fmt_num(score['value'])} при медиане {fmt_num(ref['median'])}")
    if "image" in (fam.get("modalities") or {}).get("input", []):
        pros.append("принимает изображения на входе")
    else:
        cons.append("нет vision (нет image на входе)")
    ctx = fam.get("context_length")
    if ctx and ctx >= pcfg["long_context_tokens"]:
        pros.append(f"контекст ≥ {fmt_context(pcfg['long_context_tokens'])}: {fmt_context(ctx)}")
    elif ctx and ctx < pcfg["short_context_tokens"]:
        cons.append(f"короткий контекст (< {fmt_context(pcfg['short_context_tokens'])}): {fmt_context(ctx)}")
    out_ref, completion = refs["output"], (fam.get("pricing_per_1m") or {}).get("completion")
    if fam.get("chat", True) and completion is not None and out_ref["count"] >= pcfg["min_pool_size"]:
        if completion > out_ref["high"]:
            cons.append(f"дорогой output: {fmt_money(completion)} (выше {pcfg['expensive_output_percentile']}-го "
                        f"перцентиля каталога, {fmt_money(out_ref['high'])})")
        elif completion <= out_ref["low"]:
            pros.append(f"дешёвый output: {fmt_money(completion)} (в нижних {pcfg['cheap_output_percentile']}% каталога, "
                        f"порог {fmt_money(out_ref['low'])})")
    return pros, cons


def integrity(families: list[dict], name_map: dict, translations: dict) -> dict:
    """AC-16: id из name_map и descriptions_ru, которых нет в каталоге, помечаются устаревшими; файлы не меняются."""
    current = {f["family_id"] for f in families}
    mapped = set(name_map.get("matches") or {}) if isinstance(name_map.get("matches"), dict) else set()
    for c in name_map.get("candidates") or []:
        if isinstance(c, dict) and isinstance(c.get("family_id"), str):
            mapped.add(c["family_id"])
    by_id = {f["family_id"]: f for f in families}
    outdated = sorted(i for i in translations if i in by_id and valid_translation(translations[i])
                      and translations[i]["source_hash"] != description_hash(by_id[i].get("description")))
    return {"name_map": sorted(mapped - current), "descriptions": sorted(set(translations) - current),
            "outdated_translations": outdated}


# ----------------------------------------------------------------- форматирование
def fmt_num(x: float) -> str:
    text = f"{x:.2f}" if abs(x) >= 1 else f"{x:.4f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def fmt_money(x: float | None) -> str:
    return NO_DATA if x is None else "$" + fmt_num(x)


def fmt_context(n: int | None) -> str:
    if not n:
        return NO_DATA
    if n < 1000:
        return str(n)
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


def normalize_text(text: str | None) -> str:
    return " ".join((text or "").split())


def description_hash(text: str | None) -> str:
    """Хеш оригинала описания (sha256 по тексту с нормализованными пробелами) - ключ актуальности перевода (FR-5)."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()


def valid_translation(entry) -> bool:
    return (isinstance(entry, dict) and all(isinstance(entry.get(k), str) and entry[k].strip()
                                            for k in ("text", "source_hash", "translated_at")))


def translation_status(fam: dict, translations: dict) -> tuple[str, dict | None]:
    """-> ('ok' | 'stale' | 'none', запись). stale - оригинал изменился с момента перевода (AC-15)."""
    entry = translations.get(fam["family_id"])
    if not valid_translation(entry):
        return "none", None
    if entry["source_hash"] != description_hash(fam.get("description")):
        return "stale", entry
    return "ok", entry


def description_line(fam: dict, qcfg: dict, translations: dict | None = None, full: bool = False) -> str:
    """Описание: перевод с пометкой и датой, если он актуален; иначе оригинал (AC-15)."""
    original = normalize_text(fam.get("description"))
    if not original:
        return "описание отсутствует в каталоге"
    status, entry = translation_status(fam, translations or {})
    if status == "ok":
        text, marker = normalize_text(entry["text"]), f"[{TRANSLATION_LABEL}, {entry['translated_at']}]"
    else:
        text, marker = original, "[оригинал, перевод устарел]" if status == "stale" else "[оригинал]"
    limit = qcfg["description_max_chars"]
    if not full and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return f"{text} {marker}"


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


def model_block(i: int, e: dict, qcfg: dict, tags: list[str], translations: dict | None = None) -> list[str]:
    fam = e["family"]
    title = f"{i}. **{fam['name']}** (`{fam['family_id']}`)" + "".join(f" · {t}" for t in tags)
    notes = price_notes(fam)
    return [title,
            f"   Общий рейтинг: {score_text(e['score'])} · цена вх / исх: {price_pair(fam)} · value: {fmt_value(e)}"
            + (f" · {'; '.join(notes)}" if notes else ""),
            f"   Контекст: {fmt_context(fam.get('context_length'))} · {fmt_modalities(fam)} · релиз: {fmt_date(fam.get('created'))}",
            f"   {description_line(fam, qcfg, translations)}"]


def render_flagships(res: dict, qcfg: dict, spot_ids: set[str], translations: dict | None = None) -> list[str]:
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
        lines += model_block(i, e, qcfg, tags, translations)
    lines.append("")
    if res["no_data"]:
        lines.append(f"Нет данных для определения флагмана ({len(res['no_data'])}): {', '.join(res['no_data'])}.")
    extra = skipped_line(res["skipped"])
    if extra:
        lines.append(extra)
    return lines


def render_spotlight(res: dict, qcfg: dict, scores: dict, flag_ids: set[str], ref_date: str,
                     translations: dict | None = None) -> list[str]:
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
                  f"   {description_line(fam, qcfg, translations)}"]
    if res["fresh_total"] > len(res["fresh"]):
        lines.append(f"Показано {len(res['fresh'])} из {res['fresh_total']}.")
    lines += ["", f"### Необычно дёшево для своего уровня (рейтинг выше порога источника, "
                  f"цена в нижних {scfg['cheap_price_percentile']}% среди таких моделей)"]
    if not res["cheap"]:
        lines.append(f"{NO_DATA}: подходящих моделей нет.")
    for i, e in enumerate(res["cheap"], 1):
        tags = ["также флагман"] if e["family"]["family_id"] in flag_ids else []
        lines += model_block(i, e, qcfg, tags, translations)
    if res["cheap_total"] > len(res["cheap"]):
        lines.append(f"Показано {len(res['cheap'])} из {res['cheap_total']}.")
    return lines


def cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def model_flags(fam: dict) -> list[str]:
    flags = []
    if fam.get("alias"):
        flags.append("алиас: версия не определена")
    if not fam.get("chat", True):
        flags.append("не-чат модель")
    return flags


def render_integrity(report: dict, warnings: list[str]) -> list[str]:
    """Предупреждения о данных: устаревшие id и переводы помечаются, но не удаляются (AC-16)."""
    lines = [f"⚠ {w}" for w in warnings]

    def ids(items: list[str]) -> str:
        return ", ".join(f"`{i}`" for i in items[:5]) + (f" и ещё {len(items) - 5}" if len(items) > 5 else "")
    if report["name_map"]:
        lines.append(f"⚠ Устарели записи name_map.json (id нет в актуальном каталоге, не удалены): "
                     f"{len(report['name_map'])} — {ids(report['name_map'])}")
    if report["descriptions"]:
        lines.append(f"⚠ Устарели переводы descriptions_ru.json (id нет в актуальном каталоге, не удалены): "
                     f"{len(report['descriptions'])} — {ids(report['descriptions'])}")
    if report["outdated_translations"]:
        lines.append(f"⚠ Оригинал описания изменился после перевода: {len(report['outdated_translations'])} — "
                     f"{ids(report['outdated_translations'])}; показывается оригинал")
    return lines


def render_show(fam: dict, score_map: dict, refs: dict, qcfg: dict, translations: dict) -> list[str]:
    own = score_map.get(fam["family_id"]) or {}
    lines = [f"## Справка: {fam['name']} (`{fam['family_id']}`)"]
    for flag in model_flags(fam):
        lines.append(f"**{flag}**; в рекомендации, флагманы и spotlight не входит.")
    others = [v for v in fam.get("variant_ids", []) if v != fam.get("representative_id")]
    if others:
        lines.append(f"Представитель: `{fam.get('representative_id')}`; другие варианты: {', '.join(f'`{v}`' for v in others)}")
    lines += ["", f"- Релиз: {fmt_date(fam.get('created'))}",
              f"- Контекст: {fmt_context(fam.get('context_length'))}",
              f"- Модальности: {fmt_modalities(fam)}",
              f"- Цена вх / исх, $/1M: {price_pair(fam)}"]
    for note in price_notes(fam):
        lines.append(f"  - {note}")
    lines += ["", "### Оценки (внешние бенчмарки)", "| Метрика | Значение | Источник, дата |", "|---|---|---|"]
    for metric, label in METRIC_LABELS:
        s = own.get(metric)
        if not s:
            lines.append(f"| {label} | {NO_DATA} | — |")
            continue
        cfg = s.get("config")
        cfg_note = f", конфигурация: {cfg}" if cfg not in (None, "medium") else ""
        lines.append(f"| {label} | {fmt_num(s['value'])} ({s['metric']}) | "
                     f"{SOURCE_LABELS.get(s['source'], s['source'])}, {s['date']}{cfg_note} |")
    if len({own[m]["source"] for m, _ in METRIC_LABELS if own.get(m)}) > 1:
        lines.append("Значения разных источников (AA и Arena) напрямую несопоставимы.")
    pros, cons = pros_cons(fam, score_map, refs, qcfg)
    lines += ["", "### Плюсы"] + ([f"- {t}" for t in pros] or [f"- {NO_DATA}: ни одно правило не сработало"])
    lines += ["", "### Минусы"] + ([f"- {t}" for t in cons] or [f"- {NO_DATA}: ни одно правило не сработало"])
    lines.append("Плюсы и минусы выведены правилами из config.json (pros_cons), без свободного текста.")
    lines += ["", "### Описание", description_line(fam, qcfg, translations, full=True)]
    return lines


def render_compare(fams: list[dict], score_map: dict) -> list[str]:
    lines = ["## Сравнение моделей", ""]
    head = ["Параметр", *[cell(f"{f['name']} (`{f['family_id']}`)") for f in fams]]
    lines += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]

    def row(label: str, values: list[str]) -> None:
        lines.append("| " + " | ".join([label, *[cell(v) for v in values]]) + " |")
    row("Цена вх / исх, $/1M", [price_pair(f) for f in fams])
    row("Контекст", [fmt_context(f.get("context_length")) for f in fams])
    row("Модальности", [fmt_modalities(f) for f in fams])
    row("Релиз", [fmt_date(f.get("created")) for f in fams])
    mixed = False
    for metric, label in METRIC_LABELS:
        cells, sources = [], set()
        for f in fams:
            s = (score_map.get(f["family_id"]) or {}).get(metric)
            cells.append(score_text(s) if s else NO_DATA)
            if s:
                sources.add(s["source"])
        mixed = mixed or len(sources) > 1
        row(label, cells)
    row("Пометки", ["; ".join(model_flags(f) + price_notes(f)) or "—" for f in fams])
    if mixed:
        lines += ["", "**Источники смешаны:** значения AA и Arena в одной строке напрямую несопоставимы."]
    return lines


def render_search(query: str, hits: list[dict], limit: int, score_map: dict) -> list[str]:
    lines = [f"## Поиск: {query}"]
    if not hits:
        return lines + ["", f"{NO_DATA}: в каталоге нет моделей с таким именем или id."]
    lines += ["", "| Модель | Общий рейтинг | Цена вх / исх, $/1M | Контекст | Пометки |", "|---|---|---|---|---|"]
    for f in hits[:limit]:
        s = (score_map.get(f["family_id"]) or {}).get("overall")
        lines.append("| " + " | ".join(cell(c) for c in (
            f"{f['name']} (`{f['family_id']}`)", score_text(s) if s else NO_DATA, price_pair(f),
            fmt_context(f.get("context_length")), "; ".join(model_flags(f)) or "—")) + " |")
    lines += ["", f"Найдено: {len(hits)}, показано: {min(len(hits), limit)}."]
    if len(hits) > limit:
        lines.append("Уточните запрос или увеличьте --limit.")
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
    show = sub.add_parser("show", help="справка по одной модели")
    show.add_argument("model", help="id, алиас или часть имени модели")
    cmp_ = sub.add_parser("compare", help="сравнение двух и более моделей")
    cmp_.add_argument("models", nargs="+", help="id, алиасы или имена моделей")
    srch = sub.add_parser("search", help="поиск модели по части имени или id")
    srch.add_argument("text", nargs="+")
    srch.add_argument("--limit", type=int, help="сколько строк показать")
    return ap


def run(args: argparse.Namespace) -> list[str]:
    qcfg, models, scores = load_snapshots(args.data_dir, args.config)
    now = parse_time(args.now) if args.now else dt.datetime.now(dt.timezone.utc)
    families, score_map = models["families"], scores.get("models", {})
    translations, tr_warning = load_translations(args.data_dir)
    name_map, nm_warning = load_optional(args.data_dir / "name_map.json", "name_map.json")
    notices = render_integrity(integrity(families, name_map, translations),
                               [w for w in (tr_warning, nm_warning) if w])
    out = ["# Советник по моделям Nous", *header_lines(models, scores, qcfg, now), *notices, ""]
    if args.command == "show":
        fam = resolve_model(args.model, families)
        out += render_show(fam, score_map, reference_stats(families, score_map, qcfg), qcfg, translations)
    elif args.command == "compare":
        picked = []
        for text in args.models:
            fam = resolve_model(text, families)
            if fam["family_id"] not in {f["family_id"] for f in picked}:
                picked.append(fam)
        if len(picked) < 2:
            raise QueryError("Для сравнения нужны минимум две разные модели.")
        out += render_compare(picked, score_map)
    elif args.command == "search":
        limit = args.limit if args.limit is not None else qcfg["search_limit"]
        if limit < 1:
            raise QueryError("--limit должен быть больше нуля")
        out += render_search(" ".join(args.text), search_families(args.text, families), limit, score_map)
    elif args.command == "recommend":
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
            out += render_flagships(flag, qcfg, spot_ids, translations)
        if args.set_name is None:
            out.append("")
        if args.set_name in (None, "spotlight"):
            out += render_spotlight(spot, qcfg, score_map, flag_ids, ref_date, translations)
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
