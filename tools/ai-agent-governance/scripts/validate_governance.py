"""Validate local governance artifacts without external dependencies."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from redact import redact_text

REQUIRED_TASK = {"id", "title", "status", "created", "checkpoint", "result", "related"}
TASK_ID = re.compile(r"^task-\d{4}-\d{2}-\d{2}-\d{3}$")
RAW_SECRET = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9_]{20,}|\bgithub_pat_[A-Za-z0-9_]{20,}")


def frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("missing frontmatter")
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError("unterminated frontmatter")
    result, stack = {}, []
    for raw in text[4:end].splitlines():
        if not raw or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, _, value = raw.strip().partition(":")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        target = result
        for _, parent in stack:
            target = target.setdefault(parent, {})
        value = value.strip().strip('"')
        if value == "":
            target[key] = {}
            stack.append((indent, key))
        elif value in {"true", "false"}:
            target[key] = value == "true"
        else:
            target[key] = value
    return result


def git_message(root: Path, commit: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), "show", "-s", "--format=%B", commit], text=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    root = args.project_root.resolve()
    errors, warnings, ids = [], [], set()
    for task in sorted((root / "tasks").glob("*.md")):
        if task.name == "index.md":
            continue
        try:
            data = frontmatter(task)
        except ValueError as exc:
            errors.append(f"{task}: {exc}")
            continue
        missing = REQUIRED_TASK - data.keys()
        if missing:
            errors.append(f"{task}: missing fields {sorted(missing)}")
        task_id = data.get("id", "")
        if not TASK_ID.match(task_id):
            errors.append(f"{task}: invalid task id {task_id!r}")
        if task_id in ids:
            errors.append(f"{task}: duplicate task id {task_id}")
        ids.add(task_id)
        tags = data.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.strip("[]").split(",") if t.strip()]
        if "feature" in tags and "specs" not in str(data.get("related", {})):
            warnings.append(f"{task}: feature task does not reference a specs/ entry in related.specs")
        if data.get("status") == "done":
            checkpoint, result = data.get("checkpoint", {}), data.get("result", {})
            if not checkpoint.get("git_head") or not result.get("commit") or not result.get("test_result"):
                errors.append(f"{task}: completed task lacks checkpoint/commit/test result")
            elif re.fullmatch(r"[0-9a-f]{7,64}", result["commit"]):
                try:
                    if f"Task: {task_id}" not in git_message(root, result["commit"]):
                        warnings.append(f"{task}: commit lacks Task trailer")
                except subprocess.CalledProcessError:
                    errors.append(f"{task}: result commit does not exist")
    for log in (root / "logs/tasks").glob("*.jsonl"):
        for line_no, line in enumerate(log.read_text(encoding="utf-8").splitlines(), 1):
            try:
                json.loads(line)
            except json.JSONDecodeError:
                errors.append(f"{log}:{line_no}: invalid JSONL")
            if RAW_SECRET.search(line):
                errors.append(f"{log}:{line_no}: apparent unredacted secret")
    for spec_dir in sorted((root / "specs").glob("*/")):
        if not (spec_dir / "spec.md").exists():
            warnings.append(f"{spec_dir}: spec directory lacks spec.md")
        plan = spec_dir / "plan.md"
        if plan.exists():
            text = plan.read_text(encoding="utf-8")
            if "Architecture linkage" not in text:
                warnings.append(f"{plan}: missing mandatory 'Architecture linkage' section")
        elif (spec_dir / "spec.md").exists():
            warnings.append(f"{spec_dir}: spec.md exists without plan.md")
    for path in [root / "AGENTS.md", root / "CORE.md", root / "docs/constitution.md", root / "tech-debt.md"]:
        if not path.exists():
            errors.append(f"missing required artifact: {path}")
    for message in errors:
        print("ERROR", message)
    for message in warnings:
        print("WARNING", message)
    if errors or (args.strict and warnings):
        return 1
    print(f"VALIDATION_OK tasks={len(ids)} warnings={len(warnings)}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
