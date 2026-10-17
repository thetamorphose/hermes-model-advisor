#!/usr/bin/env python3
"""fetch_models.py - снапшот каталога Nous Portal (FR-1).

Тянет /v1/models, нормализует цены в $/1M токенов, схлопывает записи в семейства
(ADR-0001, AgDR-0001, ТЗ v0.6 FR-1) и пишет skill/data/models.json.
Только stdlib, Python 3.10+. Токен печатать нельзя и не печатается.

Использование:
    python fetch_models.py [--token T] [--output PATH] [--from-file RAW.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

SCHEMA_VERSION = 1
API_URL = "https://inference-api.nousresearch.com/v1/models"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "data" / "models.json"

SUFFIX_RE = re.compile(r":[A-Za-z0-9_-]+$")  # :batch, :US, :free
DATE_RE = re.compile(r"-(\d{8}|\d{4}-\d{2}-\d{2}|\d{4})$")
PRICE_KEYS = ("prompt", "completion", "input_cache_read", "input_cache_write", "input_cache_write_1h")


# ----------------------------------------------------------------- цены
def per_million(value) -> float | None:
    """Цена за 1 токен (строка) -> $/1M токенов. Decimal, чтобы не получить 0.9400000000000001."""
    if value is None:
        return None
    try:
        return float((Decimal(str(value)) * 1_000_000).normalize())
    except Exception:
        return None


def normalize_price_block(block: dict) -> dict:
    return {k: per_million(block[k]) for k in PRICE_KEYS if k in block and per_million(block[k]) is not None}


def normalize_pricing(pricing: dict) -> dict:
    out = normalize_price_block(pricing)
    original = pricing.get("original")
    if isinstance(original, dict):
        out["list_price"] = normalize_price_block(original)  # цена без скидки
    tiers = pricing.get("overrides")
    if isinstance(tiers, list):
        out["tiers"] = [
            {"min_prompt_tokens": t.get("min_prompt_tokens"), **normalize_price_block(t)}
            for t in tiers if isinstance(t, dict)
        ]
    return out


# ----------------------------------------------------------------- семейства
def strip_variant(model_id: str) -> str:
    """Убрать префикс '~' и суффикс ':batch' / ':US' / ':free'."""
    return SUFFIX_RE.sub("", model_id.lstrip("~"))


def is_alias(rec: dict) -> bool:
    """Плавающий алиас (AgDR-0001): canonical_slug равен собственному id и id - '~...' или '...-latest'."""
    mid = rec["id"]
    return rec.get("canonical_slug") == mid and (mid.startswith("~") or mid.endswith("-latest"))


def family_key(rec: dict, base_ids: set[str]) -> str:
    key = strip_variant(rec["id"])
    if is_alias(rec):
        return key
    m = DATE_RE.search(key)
    if m and key[: m.start()] in base_ids:  # дата срезается, только если база есть в каталоге
        return key[: m.start()]
    return key


def variant_rank(rec: dict) -> tuple:
    """Кто представляет семейство: базовый вариант без суффиксов, затем без '~', без суффикса, не batch."""
    mid = rec["id"]
    return (mid != strip_variant(mid), mid.startswith("~"), ":" in mid, mid.endswith(":batch"), mid)


def price_pair(rec: dict) -> tuple:
    p = normalize_pricing(rec.get("pricing") or {})
    return (p.get("prompt"), p.get("completion"))


def modalities_of(rec: dict) -> dict | None:
    arch = rec.get("architecture") or {}
    inp, out = arch.get("input_modalities"), arch.get("output_modalities")
    if not inp and not out:
        return None
    return {"input": list(inp or []), "output": list(out or [])}


def build_family(key: str, members: list[dict]) -> dict:
    members = sorted(members, key=variant_rank)
    rep = members[0]
    mods, mods_from = modalities_of(rep), rep["id"]
    if mods is None:  # блок architecture пуст (10 записей Anthropic): берём у ближайшего варианта
        for other in members[1:]:
            mods = modalities_of(other)
            if mods:
                mods_from = other["id"]
                break
        else:
            mods_from = None
    rep_price = price_pair(rep)
    price_variants = [
        {"id": m["id"], "prompt": price_pair(m)[0], "completion": price_pair(m)[1]}
        for m in members[1:] if price_pair(m) != rep_price
    ]
    chat = None if mods is None else ("text" in mods["output"])
    return {
        "family_id": key,
        "representative_id": rep["id"],
        "variant_ids": [m["id"] for m in members],
        "alias": is_alias(rep),
        "name": rep.get("name"),
        "created": rep.get("created"),
        "context_length": rep.get("context_length"),
        "modalities": mods,
        "modalities_from": mods_from if mods_from != rep["id"] else None,
        "chat": chat,  # False = нет text в выходе (embeddings и т.п.): D3, исключается из рекомендаций
        "pricing_per_1m": normalize_pricing(rep.get("pricing") or {}),
        "price_variants": price_variants,
        "description": rep.get("description"),
    }


def build_families(records: list[dict]) -> list[dict]:
    base_ids = {r["id"] for r in records if r["id"] == strip_variant(r["id"])}
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        groups[family_key(r, base_ids)].append(r)
    return [build_family(k, groups[k]) for k in sorted(groups)]


# ----------------------------------------------------------------- токен и сеть
def find_token(cli_token: str | None) -> str | None:
    """Приоритет: --token, NOUS_API_TOKEN, auth.json. Токен необязателен (проверено на этапе 0)."""
    if cli_token:
        return cli_token
    if os.environ.get("NOUS_API_TOKEN"):
        return os.environ["NOUS_API_TOKEN"]
    candidates = [Path.home() / ".hermes" / "auth.json"]
    if os.environ.get("HERMES_HOME"):
        candidates.append(Path(os.environ["HERMES_HOME"]) / "auth.json")
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "hermes" / "auth.json")
    for path in candidates:
        try:
            nous = json.loads(path.read_text(encoding="utf-8"))["providers"]["nous"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        token = nous.get("agent_key") or nous.get("access_token")
        if token:
            return token
    return None


def http_get(url: str, token: str | None, timeout: int = 60) -> bytes:
    headers = {"User-Agent": "hermes-model-advisor/0.1", "Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_catalog(token: str | None) -> dict:
    try:
        raw = http_get(API_URL, token)
    except urllib.error.HTTPError as exc:
        if token and exc.code in (401, 403):  # просроченный токен не должен ломать открытый эндпоинт
            raw = http_get(API_URL, None)
        else:
            raise
    return json.loads(raw)


# ----------------------------------------------------------------- снапшот
def build_snapshot(payload: dict, fetched_at: str) -> dict:
    records = payload.get("data")
    if not isinstance(records, list) or not records:
        raise ValueError("в ответе нет списка моделей 'data'")
    families = build_families(records)
    return {
        "schema_version": SCHEMA_VERSION,
        "fetched_at": fetched_at,
        "source": API_URL,
        "count": len(records),
        "family_count": len(families),
        "families": families,
    }


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--token", help="токен Nous (необязателен)")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--from-file", type=Path, help="взять ответ API из файла (тесты, офлайн)")
    args = ap.parse_args(argv)
    try:
        if args.from_file:
            payload = json.loads(args.from_file.read_text(encoding="utf-8"))
        else:
            payload = fetch_catalog(find_token(args.token))
        snapshot = build_snapshot(payload, dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"ОШИБКА: каталог не обновлён ({type(exc).__name__}: {exc}); прежний снапшот не тронут", file=sys.stderr)
        return 1
    write_atomic(args.output, snapshot)
    fams = snapshot["families"]
    print(f"Каталог: {snapshot['count']} записей -> {snapshot['family_count']} семейств; "
          f"алиасов {sum(f['alias'] for f in fams)}, не-чат {sum(f['chat'] is False for f in fams)}, "
          f"с разной ценой вариантов {sum(bool(f['price_variants']) for f in fams)}")
    print(f"Записано: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
