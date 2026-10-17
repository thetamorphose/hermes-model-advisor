"""Create a task record with a Git checkpoint and optional Hermes session mapping."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
from pathlib import Path


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^\w]+", "-", value, flags=re.UNICODE)
    return value.strip("-")[:60] or "task"


def next_id(tasks_dir: Path, date: str) -> tuple[str, int]:
    numbers = []
    for path in tasks_dir.glob(f"{date}-*.md"):
        match = re.match(rf"{re.escape(date)}-(\d{{3}})-", path.name)
        if match:
            numbers.append(int(match.group(1)))
    number = max(numbers, default=0) + 1
    return f"task-{date}-{number:03d}", number


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--session-id")
    args = parser.parse_args()
    root = args.project_root.resolve()
    tasks = root / "tasks"
    if not tasks.is_dir():
        parser.error("governance is not initialized; tasks/ is missing")
    date = dt.datetime.now(dt.timezone.utc).date().isoformat()
    lock = root / ".governance-task.lock"
    try:
        lock.touch(exist_ok=False)
    except FileExistsError:
        parser.error(f"task creation lock exists: {lock}")
    try:
        task_id, number = next_id(tasks, date)
        created = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        head = git(root, "rev-parse", "HEAD")
        clean = not bool(git(root, "status", "--porcelain"))
        filename = f"{date}-{number:03d}-{slugify(args.title)}.md"
        content = f'''---
id: {task_id}
title: {json.dumps(args.title, ensure_ascii=False)}
status: planned
created: {json.dumps(created)}
updated: {json.dumps(created)}
hermes_sessions: {json.dumps([args.session_id] if args.session_id else [])}
skill_version: "product-agent-governance@0.3.0"
tags: {json.dumps(args.tag, ensure_ascii=False)}
checkpoint:
  git_head: "{head}"
  git_status_clean: {str(clean).lower()}
  backup_ref: ""
result:
  commit: ""
  files_changed: []
  test_command: ""
  test_result: ""
related:
  specs: []
  adr: []
  agdr: []
  bugs: []
---

# {args.title}

## Input

## Acceptance criteria
- [ ]

## Output

## Rollback

'''
        target = tasks / filename
        target.write_text(content, encoding="utf-8")
        if args.session_id:
            mapping = root / "logs/task-session-map.yml"
            with mapping.open("a", encoding="utf-8") as f:
                f.write(f"\n# {task_id}\n- task_id: {task_id}\n  session_ids: [{json.dumps(args.session_id)}]\n  tags: {json.dumps(args.tag)}\n  status: planned\n")
        print(f"TASK_ID={task_id}")
        print(f"TASK_FILE={target}")
    finally:
        lock.unlink(missing_ok=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
