"""Export mapped Hermes sessions to deduplicated, redacted JSONL traces.

Reads state.db exclusively through SQLite's read-only URI and tolerates optional
columns that may differ between Hermes versions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from redact import redact


def scalar(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def rows(conn, sql, args=()):
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError as exc:
        print(f"WARNING: skipped unavailable query: {exc}", file=sys.stderr)
        return []


def mappings(path: Path) -> dict[str, dict]:
    """Parse the deliberately small YAML subset emitted by create_task.py."""
    if not path.exists():
        return {}
    result, current = {}, None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("- task_id:"):
            current = line.split(":", 1)[1].strip()
            result[current] = {"task_id": current, "session_ids": [], "tags": [], "status": "planned"}
        elif current and line.startswith("session_ids:"):
            try:
                result[current]["session_ids"] = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                pass
        elif current and line.startswith("tags:"):
            try:
                result[current]["tags"] = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                pass
        elif current and line.startswith("status:"):
            result[current]["status"] = line.split(":", 1)[1].strip()
    return result


def event_id(session_id: str, source: str, source_id: object) -> str:
    material = f"1.0|{session_id}|{source}|{source_id}".encode()
    return "sha256:" + hashlib.sha256(material).hexdigest()


def iso(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return str(value)


def event_datetime(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--state-db", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-unlinked", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    state_db = args.state_db.resolve()
    if not state_db.is_file():
        parser.error(f"state db not found: {state_db}")
    mapping_by_task = mappings(root / "logs/task-session-map.yml")
    session_map = {sid: data for data in mapping_by_task.values() for sid in data["session_ids"]}
    conn = sqlite3.connect(f"file:{state_db.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    sessions = rows(conn, "SELECT * FROM sessions")
    state_path = root / "logs/exports/export-state.json"
    known = set(json.loads(state_path.read_text(encoding="utf-8")).get("exported_event_ids", [])) if state_path.exists() else set()
    grouped: dict[str, list[dict]] = {}
    added = []
    for session in sessions:
        sid = session.get("id")
        info = session_map.get(sid)
        if not info and not args.include_unlinked:
            continue
        task_id = info["task_id"] if info else None
        quarter_ts = session.get("started_at") or datetime.now(timezone.utc).isoformat()
        try:
            parsed = event_datetime(quarter_ts)
            target_key = f"{parsed.year}-Q{(parsed.month - 1) // 3 + 1}"
        except (TypeError, ValueError, OverflowError):
            target_key = "unlinked"
        metadata = {key: scalar(session.get(key)) for key in ("model", "input_tokens", "output_tokens", "reasoning_tokens", "estimated_cost_usd", "git_branch", "git_repo_root") if key in session}
        base = {"schema_version": "1.0", "ts": iso(session.get("started_at")), "trace_id": task_id, "task_id": task_id, "session_id": sid, "parent_span_id": session.get("parent_session_id"), "status": info["status"] if info else "unlinked", "tags": info["tags"] if info else [], "metadata": metadata}
        records = [("session_started", "session", sid, session.get("title") or "session", {"source": session.get("source")})]
        for msg in rows(conn, "SELECT * FROM messages WHERE session_id = ? ORDER BY id", (sid,)):
            records.append(("message", "tool" if msg.get("tool_name") else "message", msg.get("id"), msg.get("tool_name") or msg.get("role"), {"input": msg.get("content"), "tool_calls": msg.get("tool_calls"), "api_content": msg.get("api_content")}))
        for source, typ, source_id, name, payload in records:
            eid = event_id(sid, source, source_id)
            if eid in known:
                continue
            entry = dict(base, event_id=eid, span_id=f"{source}:{source_id}", event=source, observation_type=typ, name=name, input=redact(payload))
            grouped.setdefault(target_key, []).append(entry)
            added.append(eid)
    if args.dry_run:
        print(f"DRY_RUN_EVENTS={len(added)}")
        print("TARGETS=" + ",".join(sorted(grouped)))
        return 0
    for key, events in grouped.items():
        filename = f"{key}.jsonl" if key != "unlinked" else "unlinked.jsonl"
        target = root / "logs/tasks" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as out:
            for item in events:
                out.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"schema_version": "1.0", "exported_event_ids": sorted(known | set(added))}, indent=2) + "\n", encoding="utf-8")
    print(f"EXPORTED_EVENTS={len(added)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
