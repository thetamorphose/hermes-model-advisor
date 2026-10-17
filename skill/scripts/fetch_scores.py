#!/usr/bin/env python3
"""fetch_scores.py - снапшот оценок: Artificial Analysis + LMArena (FR-2).

Пишет skill/data/scores.json и skill/data/name_map.json. Если AA недоступен (нет ключа,
401/403, 429, сеть, мусорный ответ), оценки целиком берутся из LMArena (резервный режим),
у AA в снапшоте status=unavailable и код причины. Ошибка одного источника не роняет другой.
Сопоставление имён - только точное равенство после нормализации; нечёткие совпадения
уходят в name_map.json как candidate и оценок не дают. Ключ AA нигде не печатается и не
записывается. Только stdlib, Python 3.10+.

Использование:
    python fetch_scores.py [--models PATH] [--output PATH] [--name-map PATH]
                           [--aa-file RAW.json] [--arena-file RAW.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_models import write_atomic  # noqa: E402

SCHEMA_VERSION = 1
SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parent
DEFAULT_CONFIG = SKILL_DIR / "config.json"
DEFAULT_MODELS = SKILL_DIR / "data" / "models.json"
DEFAULT_OUTPUT = SKILL_DIR / "data" / "scores.json"
DEFAULT_NAME_MAP = SKILL_DIR / "data" / "name_map.json"

METRICS = ("overall", "coding", "agentic", "vision")
SOURCES = ("artificial_analysis", "lmarena")  # порядок = приоритет (AA выше)
REASONS = ("no_key", "auth_error", "rate_limited", "network", "bad_response")


class NetworkError(Exception):
    """Сеть недоступна или таймаут. Текст исключения не хранится: он может содержать лишнее."""


# ----------------------------------------------------------------- сеть
def http_get(url: str, headers: dict | None = None, timeout: int = 60) -> tuple[int, bytes]:
    """GET -> (код, тело). Коды 4xx/5xx возвращаются, а не бросаются; сеть -> NetworkError."""
    h = {"User-Agent": "hermes-model-advisor/0.1", "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001
            body = b""
        return exc.code, body
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NetworkError(type(exc).__name__) from None


# ----------------------------------------------------------------- ключ и конфигурация
def find_aa_key(environ=None, dirs=None) -> str | None:
    """AA_API_KEY: переменная окружения -> skill/.env -> .env в корне репозитория (AgDR-0002)."""
    environ = os.environ if environ is None else environ
    key = (environ.get("AA_API_KEY") or "").strip()
    if key:
        return key
    for directory in dirs if dirs is not None else (SKILL_DIR, REPO_ROOT):
        try:
            text = (Path(directory) / ".env").read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            if line.startswith("AA_API_KEY="):
                value = line.split("=", 1)[1].strip().strip("\"'")
                if value:
                    return value
    return None


def scrub(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["scores"]


# ----------------------------------------------------------------- нормализация имён
PAREN_RE = re.compile(r"\(([^()]*)\)")
EFFORT_LABELS = {"max", "xhigh", "high", "medium", "low", "minimal", "non-reasoning", "reasoning"}
LABEL_SYNONYMS = {"thinking": "reasoning", "non-thinking": "non-reasoning", "thinking-minimal": "minimal"}
# суффиксы имени без скобок (Arena: gpt-6-sol-max); длинные раньше коротких
SUFFIX_LABELS = {"non-reasoning": "non-reasoning", "reasoning": "reasoning", "thinking": "reasoning",
                 "xhigh": "xhigh", "max": "max", "high": "high", "medium": "medium", "low": "low",
                 "minimal": "minimal"}


def slugify(text: str) -> str:
    """Нижний регистр, точки и подчёркивания -> дефисы, '+' -> plus, прочее не a-z0-9 -> дефис."""
    text = text.lower().replace("+", "-plus-").replace(".", "-").replace("_", "-")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def canon_label(token: str) -> str | None:
    t = slugify(token)
    t = LABEL_SYNONYMS.get(t, t)
    return t if t in EFFORT_LABELS else None


def parse_name(name: str) -> tuple[str, str | None]:
    """'Claude Opus 5.5 (Max, Default Fallback)' -> ('claude-opus-5-5', 'max'). Скобки отбрасываются."""
    labels: list[str] = []

    def grab(match):
        for part in match.group(1).split(","):
            label = canon_label(part)
            if label:
                labels.append(label)
        return " "

    base = slugify(PAREN_RE.sub(grab, name))
    effort = None
    if labels:
        effort = "non-reasoning" if "non-reasoning" in labels else labels[0]
    return base, effort


def resolve_key(base: str, effort: str | None, catalog_keys: set[str]) -> tuple[str, str | None]:
    """Суффикс усилия без скобок отрезается, только если полного имени нет в каталоге, а базовое есть."""
    if effort is not None or base in catalog_keys:
        return base, effort
    for suffix, label in SUFFIX_LABELS.items():
        tail = "-" + suffix
        if base.endswith(tail) and base[: -len(tail)] in catalog_keys:
            return base[: -len(tail)], label
    return base, None


def catalog_key(family_id: str) -> str:
    return slugify(family_id.split("/", 1)[-1])


# ----------------------------------------------------------------- записи источников
def aa_entries(records: list, cfg: dict, fetch_date: str) -> list[dict]:
    out = []
    for rec in records:
        if not isinstance(rec, dict) or not isinstance(rec.get("name"), str):
            continue
        evals = rec.get("evaluations")
        if not isinstance(evals, dict):
            continue
        values = {}
        for metric, spec in cfg["artificial_analysis"]["metrics"].items():
            v = evals.get(spec["field"])
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                values[metric] = {"value": v, "date": fetch_date}
        if values:
            out.append({"name": rec["name"], "order_date": str(rec.get("release_date") or ""), "values": values})
    return out


def arena_entries(rows_by_metric: dict, cfg: dict) -> list[dict]:
    out = []
    for metric, rows in rows_by_metric.items():
        spec = cfg["lmarena"]["metrics"][metric]
        newest: dict[str, tuple] = {}
        for row in rows:
            name, v = row.get("model_name"), row.get(spec["field"])
            if not isinstance(name, str) or not isinstance(v, (int, float)) or isinstance(v, bool):
                continue
            date = str(row.get("leaderboard_publish_date") or "")
            if name not in newest or date > newest[name][1]:  # vision: даты смешаны, берём свежую
                newest[name] = (round(float(v), spec.get("decimals", 2)), date)
        for name in sorted(newest):
            value, date = newest[name]
            out.append({"name": name, "order_date": date, "values": {metric: {"value": value, "date": date}}})
    return out


# ----------------------------------------------------------------- Artificial Analysis
def aa_unavailable(reason: str) -> dict:
    return {"status": "unavailable", "reason": reason}


def parse_aa_payload(payload, cfg: dict, fetch_date: str) -> tuple[dict, list[dict]]:
    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return aa_unavailable("bad_response"), []
    entries = aa_entries(records, cfg, fetch_date)
    if not entries:
        return aa_unavailable("bad_response"), []
    status = {"status": "ok", "terms": cfg["artificial_analysis"]["terms"], "entry_count": len(entries)}
    return status, entries


def fetch_aa(cfg: dict, key: str | None, fetch_date: str) -> tuple[dict, list[dict]]:
    """Никогда не бросает. Статус содержит только код причины, не текст ошибки."""
    if not key:
        return aa_unavailable("no_key"), []
    acfg = cfg["artificial_analysis"]
    try:
        code, body = http_get(acfg["url"], {"x-api-key": key}, acfg.get("timeout_s", 60))
    except Exception:  # noqa: BLE001 - текст исключения может содержать ключ, не сохраняем
        return aa_unavailable("network"), []
    if code in (401, 403):
        return aa_unavailable("auth_error"), []
    if code == 429:
        return aa_unavailable("rate_limited"), []
    if code != 200:
        return aa_unavailable("bad_response"), []
    try:
        payload = json.loads(body)
    except ValueError:
        return aa_unavailable("bad_response"), []
    return parse_aa_payload(payload, cfg, fetch_date)


# ----------------------------------------------------------------- LMArena
RETRY_CODES = (None, 429, 500, 502, 503, 504)


def arena_get_json(url: str, lcfg: dict, sleep) -> tuple[dict | None, str | None]:
    delay, reason = lcfg["base_delay_s"], "network"
    for attempt in range(lcfg["max_attempts"]):
        try:
            code, body = http_get(url, None, lcfg.get("timeout_s", 60))
        except Exception:  # noqa: BLE001
            code, body = None, b""
        if code == 200:
            try:
                data = json.loads(body)
            except ValueError:
                return None, "bad_response"
            return (data, None) if isinstance(data, dict) else (None, "bad_response")
        if code not in RETRY_CODES:
            return None, "auth_error" if code in (401, 403) else "bad_response"
        reason = "rate_limited" if code == 429 else "network" if code is None else "bad_response"
        if attempt < lcfg["max_attempts"] - 1:
            sleep(delay)
            delay = min(delay * 2, lcfg["max_delay_s"])
    return None, reason


def arena_pages(make_url, lcfg: dict, sleep) -> tuple[list, bool, str | None]:
    """Постраничная выгрузка. complete=False при любой неудаче или partial у datasets-server."""
    rows, offset = [], 0
    while True:
        data, reason = arena_get_json(make_url(offset), lcfg, sleep)
        if data is None:
            return rows, False, reason
        total = data.get("num_rows_total")
        if data.get("partial") or not isinstance(total, int):
            return rows, False, "bad_response"
        page = [x["row"] for x in data.get("rows") or [] if isinstance(x, dict) and isinstance(x.get("row"), dict)]
        rows += page
        offset += len(page)
        if offset >= total:
            return rows, True, None
        if not page:
            return rows, False, "bad_response"


def _arena_url(lcfg: dict, endpoint: str, config: str, offset: int, where: str | None = None) -> str:
    query = {"dataset": lcfg["dataset"], "config": config, "split": lcfg["split"],
             "offset": offset, "length": lcfg["page_size"]}
    if where:
        query["where"] = where
    return f"{lcfg['base_url']}/{endpoint}?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


def fetch_arena_rows(cfg: dict, sleep=time.sleep) -> tuple[dict, dict]:
    """-> (rows_by_metric, incomplete{metric: reason}). Сначала /filter по категории, при неудаче /rows."""
    lcfg = cfg["lmarena"]
    by_config: dict[str, list] = defaultdict(list)
    for metric, spec in lcfg["metrics"].items():
        by_config[spec["config"]].append(metric)
    got: dict[str, list] = {}
    incomplete: dict[str, str] = {}
    for config, metrics in by_config.items():
        failed: list[tuple[str, str | None]] = []
        for metric in metrics:
            category = lcfg["metrics"][metric]["category"]
            where = f"\"category\"='{category}'"
            rows, ok, reason = arena_pages(lambda off: _arena_url(lcfg, "filter", config, off, where), lcfg, sleep)
            if ok and rows:
                got[metric] = rows
            else:
                failed.append((metric, reason or "bad_response"))
        if not failed:
            continue
        all_rows, ok, reason = arena_pages(lambda off: _arena_url(lcfg, "rows", config, off), lcfg, sleep)
        for metric, filter_reason in failed:
            category = lcfg["metrics"][metric]["category"]
            rows = [r for r in all_rows if r.get("category") == category] if ok else []
            if rows:
                got[metric] = rows
            else:
                incomplete[metric] = (reason if not ok else None) or filter_reason or "bad_response"
    return got, incomplete


def arena_from_raw(raw, cfg: dict) -> tuple[dict, dict]:
    """Готовая выгрузка {config: [строки всех категорий]} (офлайн, тесты)."""
    lcfg, got, incomplete = cfg["lmarena"], {}, {}
    for metric, spec in lcfg["metrics"].items():
        rows = [r for r in (raw.get(spec["config"]) or []) if isinstance(r, dict) and r.get("category") == spec["category"]]
        if rows:
            got[metric] = rows
        else:
            incomplete[metric] = "bad_response"
    return got, incomplete


def arena_status(cfg: dict, rows_by_metric: dict, incomplete: dict) -> dict:
    lcfg = cfg["lmarena"]
    dates = [str(r.get("leaderboard_publish_date")) for rows in rows_by_metric.values() for r in rows
             if r.get("leaderboard_publish_date")]
    if not rows_by_metric:
        reasons = sorted(set(incomplete.values()))
        return {"status": "unavailable", "reason": reasons[0] if reasons else "bad_response"}
    status = {"status": "partial" if incomplete else "ok", "license": lcfg["license"],
              "leaderboard_date": max(dates) if dates else None, "metrics_loaded": sorted(rows_by_metric)}
    if incomplete:
        status["incomplete"] = dict(sorted(incomplete.items()))  # не отдаём частичную категорию как полную
    return status


# ----------------------------------------------------------------- выбор конфигурации и сопоставление
def build_index(entries: list[dict], catalog_keys: set[str]) -> dict:
    index: dict[str, list] = {}
    for e in entries:
        base, effort = parse_name(e["name"])
        key, effort = resolve_key(base, effort, catalog_keys)
        index.setdefault(key, []).append({"effort": effort, "name": e["name"], "order_date": e["order_date"],
                                          "values": e["values"]})
    return index


def pick_config(configs: list[dict], metric: str, order: list[str]):
    """D1: Medium, иначе по порядку из config.json; единственная берётся как есть.
    Выбор по метрике: из конфигураций, у которых это значение есть. Равные - свежее по дате, затем по имени."""
    cands = [c for c in configs if metric in c["values"]]
    if not cands:
        return None
    cands.sort(key=lambda c: c["name"])
    cands.sort(key=lambda c: c["order_date"], reverse=True)
    cands.sort(key=lambda c: order.index(c["effort"]) if c["effort"] in order else len(order))
    return cands[0]


def build_scores(families: list[dict], entries_by_source: dict, cfg: dict) -> tuple[dict, dict]:
    """Чистая функция: -> (models, name_map). entries_by_source содержит только доступные источники."""
    order = cfg["effort_order"]
    chat_families = [f for f in families if not f.get("alias")]  # алиасы оценок не получают
    by_key: dict[str, list[str]] = defaultdict(list)
    for f in chat_families:
        by_key[catalog_key(f["family_id"])].append(f["family_id"])
    unique = {k: v[0] for k, v in by_key.items() if len(v) == 1}
    ambiguous = {k: sorted(v) for k, v in by_key.items() if len(v) > 1}
    catalog_keys = set(by_key)
    indexes = {s: build_index(entries_by_source[s], catalog_keys) for s in SOURCES if s in entries_by_source}
    metric_names = {"artificial_analysis": cfg["artificial_analysis"]["metrics"],
                    "lmarena": cfg["lmarena"]["metrics"]}

    models, matches = {}, {}
    for key, fid in sorted(unique.items(), key=lambda kv: kv[1]):
        record = {}
        for metric in METRICS:
            record[metric] = None
            for source in SOURCES:
                config = indexes.get(source, {}).get(key)
                chosen = pick_config(config, metric, order) if config else None
                if chosen:
                    v = chosen["values"][metric]
                    record[metric] = {"value": v["value"], "source": source,
                                      "metric": metric_names[source][metric]["metric"],
                                      "date": v["date"], "config": chosen["effort"],
                                      "matched_name": chosen["name"]}
                    break
        if any(record.values()):
            models[fid] = record
        found = {s: {"status": "exact", "key": key, "names": sorted({c["name"] for c in idx[key]})}
                 for s, idx in indexes.items() if key in idx}
        if found:
            matches[fid] = found

    cutoff = cfg["name_match"]["candidate_cutoff"]
    candidates = []
    for source, idx in indexes.items():
        pool = sorted(k for k in idx if k not in catalog_keys)
        for key, fid in sorted(unique.items(), key=lambda kv: kv[1]):
            if key in idx:
                continue
            close = difflib.get_close_matches(key, pool, n=1, cutoff=cutoff)
            if close:  # кандидат: в выводе не используется (правило 1)
                ratio = difflib.SequenceMatcher(None, key, close[0]).ratio()
                candidates.append({"family_id": fid, "source": source, "candidate_name": idx[close[0]][0]["name"],
                                   "similarity": round(ratio, 3), "status": "candidate"})
    name_map = {"matches": matches, "candidates": candidates, "ambiguous_catalog_keys": ambiguous}
    return models, name_map


# ----------------------------------------------------------------- сбор
def collect(families: list[dict], cfg: dict, key: str | None, now: str, aa_payload=None, arena_raw=None,
            sleep=time.sleep) -> tuple[dict, dict] | None:
    """-> (scores_snapshot, name_map) либо None, если нет данных ни из одного источника."""
    fetch_date = now[:10]
    entries: dict[str, list] = {}
    sources: dict[str, dict] = {}

    try:
        if aa_payload is not None:
            aa_status, aa_list = parse_aa_payload(aa_payload, cfg, fetch_date)
        else:
            aa_status, aa_list = fetch_aa(cfg, key, fetch_date)
    except Exception:  # noqa: BLE001
        aa_status, aa_list = aa_unavailable("bad_response"), []
    sources["artificial_analysis"] = aa_status
    if aa_status["status"] == "ok":
        entries["artificial_analysis"] = aa_list

    try:
        rows, incomplete = arena_from_raw(arena_raw, cfg) if arena_raw is not None else fetch_arena_rows(cfg, sleep)
        ar_status = arena_status(cfg, rows, incomplete)
        ar_list = arena_entries(rows, cfg)
    except Exception:  # noqa: BLE001
        ar_status, ar_list = {"status": "unavailable", "reason": "bad_response"}, []
    sources["lmarena"] = ar_status
    if ar_status["status"] in ("ok", "partial"):
        entries["lmarena"] = ar_list

    if not entries:
        return None
    models, name_map = build_scores(families, entries, cfg)
    mode = "full" if len(entries) == 2 else ("aa_only" if "artificial_analysis" in entries else "fallback")
    snapshot = {"schema_version": SCHEMA_VERSION, "fetched_at": now, "mode": mode, "sources": sources,
                "model_count": len(models), "models": models}
    name_map = {"schema_version": SCHEMA_VERSION, "generated_at": now,
                "rule": "exact equality after normalization; candidates are not used in output", **name_map}
    return snapshot, name_map


def summary(snapshot: dict) -> list[str]:
    s = snapshot["sources"]
    lines = []
    for name in SOURCES:
        st = s[name]
        extra = f", причина: {st['reason']}" if st.get("reason") else ""
        if st.get("incomplete"):
            extra += f", неполно: {', '.join(f'{m}={r}' for m, r in st['incomplete'].items())}"
        lines.append(f"{name}: {st['status']}{extra}")
    lines.append(f"Режим: {snapshot['mode']}; семейств с оценками: {snapshot['model_count']}")
    for metric in METRICS:
        by_src: dict[str, int] = defaultdict(int)
        for rec in snapshot["models"].values():
            if rec[metric]:
                by_src[rec[metric]["source"]] += 1
        lines.append(f"  {metric}: " + (", ".join(f"{k} {v}" for k, v in sorted(by_src.items())) or "нет данных"))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", type=Path, default=DEFAULT_MODELS, help="снапшот каталога (fetch_models.py)")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--name-map", type=Path, default=DEFAULT_NAME_MAP)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--aa-file", type=Path, help="ответ API AA из файла (тесты, офлайн)")
    ap.add_argument("--arena-file", type=Path, help="выгрузка LMArena {config: [строки]} из файла")
    args = ap.parse_args(argv)

    key = find_aa_key()
    try:
        cfg = load_config(args.config)
        families = json.loads(args.models.read_text(encoding="utf-8"))["families"]
        aa_payload = json.loads(args.aa_file.read_text(encoding="utf-8")) if args.aa_file else None
        arena_raw = json.loads(args.arena_file.read_text(encoding="utf-8")) if args.arena_file else None
    except (OSError, ValueError, KeyError) as exc:
        print(scrub(f"ОШИБКА: не удалось прочитать входные файлы ({type(exc).__name__}); "
                    "сначала выполните fetch_models.py", key), file=sys.stderr)
        return 1
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    result = collect(families, cfg, key, now, aa_payload, arena_raw)
    if result is None:
        print("ОШИБКА: ни AA, ни LMArena не дали данных; прежний снапшот оценок не тронут", file=sys.stderr)
        return 1
    snapshot, name_map = result
    write_atomic(args.name_map, name_map)
    write_atomic(args.output, snapshot)
    for line in summary(snapshot):
        print(scrub(line, key))
    print(f"Записано: {args.output}")
    print("Данные LMArena: лицензия CC-BY-4.0 (lmarena-ai/leaderboard-dataset). "
          "Снапшот с данными AA не распространять.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
