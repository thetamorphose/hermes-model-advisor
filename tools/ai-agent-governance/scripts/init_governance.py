"""Idempotently initialize Product Agent Governance in a target project."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "templates"

TEXT_FILES = {
    "AGENTS.md": "# AI-Agent Governance\n\nRead and follow `tools/ai-agent-governance/SKILL.md`.\n\n**Hard boundary:** never modify Hermes core. Work only in this project, the vendored tool, Git, and read-only Hermes session export.\n",
    "tasks/index.md": "# Task Register\n\nTasks are individual Markdown files in this directory.\n",
    "bugs/index.md": "# Bug Register\n\nNew agent-detected defects enter `inbox/`; owner triage controls priority and status.\n",
    "docs/adr/index.md": "# Architecture Decision Records\n\n",
    "docs/agdr/index.md": "# Agent Decision Records\n\n",
    "docs/architecture/index.md": "# Architecture\n\n- [System context](context.md)\n- [Containers](containers.md)\n",
    "docs/architecture/context.md": "# System Context\n\n```mermaid\ngraph LR\n    Owner[Product owner] --> Agent[AI agent]\n    Agent --> Project[Target project]\n    Agent --> Hermes[(Hermes state.db: read-only)]\n```\n",
    "docs/architecture/containers.md": "# Containers\n\n```mermaid\ngraph TB\n    subgraph Project\n      Code[Application code]\n      Docs[Governance artifacts]\n      Logs[Redacted JSONL audit logs]\n    end\n    Agent[AI agent] --> Code\n    Agent --> Docs\n    Agent --> Logs\n```\n",
    "docs/contexts/index.md": "# Bounded Contexts\n\nAdd one YAML file per approved bounded context.\n",
    "logs/task-session-map.yml": "mappings:\n",
    "logs/exports/export-state.json": '{"schema_version":"1.0","exported_event_ids":[]}\n',
    ".governance.yml": "schema_version: '1.0'\nproject_id: '{{PROJECT_ID}}'\ntool_version: '0.3.0'\nlog_retention_months: 3\n",
}
COPIES = {
    "CORE.md": "core.md",
    "docs/constitution.md": "constitution.md",
    "tech-debt.md": "tech-debt.md",
}
DIRECTORIES = [
    "tasks", "specs", "bugs/inbox", "bugs/triaged", "bugs/fixed", "docs/adr", "docs/agdr",
    "docs/architecture", "docs/contexts", "logs/tasks", "logs/exports",
]


def write_new(path: Path, content: str, dry_run: bool) -> str:
    if path.exists():
        return f"SKIP {path}"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return f"CREATE {path}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--mode", choices=["apply", "dry-run"], default="apply")
    args = parser.parse_args()
    root = args.project_root.resolve()
    if not root.is_dir():
        parser.error(f"project root does not exist: {root}")
    dry_run = args.mode == "dry-run"
    actions: list[str] = []
    for rel in DIRECTORIES:
        target = root / rel
        if target.exists():
            actions.append(f"SKIP {target}")
        else:
            if not dry_run:
                target.mkdir(parents=True)
            actions.append(f"MKDIR {target}")
    for rel, content in TEXT_FILES.items():
        actions.append(write_new(root / rel, content.replace("{{PROJECT_ID}}", args.project_id), dry_run))
    for rel, template in COPIES.items():
        target = root / rel
        if target.exists():
            actions.append(f"SKIP {target}")
        else:
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(TEMPLATES / template, target)
            actions.append(f"CREATE {target}")
    print("\n".join(actions))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
