"""Propose Core Domain candidates from objective signals; never decides.

Reads docs/constitution.md (vision + ubiquitous language), the source tree,
CORE.md and docs/contexts/*.yml, then writes a dated proposal under
docs/core-proposals/. The owner accepts or rejects each row; promotion to
CORE.md always happens through a human-approved ADR, never by this script.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".cs", ".rb", ".php", ".vue", ".svelte"}
GENERIC_PATTERNS = re.compile(
    r"(^|[_\-./])(auth|login|oauth|log(?:s|ging)?|config(?:uration)?|util(?:s|ity)?|helper|infra(?:structure)?|"
    r"test(?:s|ing)?|spec|build|dist|vendor|node_modules|generated?|mock|fixture|deploy|ci|docker|"
    r"migrations?|cache|tmp|assets?|static|public|styles?|css)([_\-./]|$)",
    re.IGNORECASE,
)
DOMAIN_BLOCK_MARKERS = re.compile(
    r"\b(class\s+\w+|interface\s+\w+|type\s+\w+\s*=|enum\s+\w+|struct\s+\w+)", re.MULTILINE
)
STOP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv", "dist", "build", ".next", "tools"}


def constitution_terms(constitution: Path) -> list[str]:
    if not constitution.exists():
        return []
    text = constitution.read_text(encoding="utf-8", errors="ignore")
    terms: set[str] = set()
    in_language_section = False
    seen_table_header = False
    for line in text.splitlines():
        if re.match(r"^#{1,3}\s", line):
            in_language_section = "ubiquitous language" in line.lower() or "единый язык" in line.lower()
            seen_table_header = False
            continue
        if not in_language_section:
            continue
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        if re.match(r"^\|[-\s|]+\|$", stripped):
            continue
        cell = stripped.split("|")[1].strip()
        if cell.lower() in {"term", "термин", ""}:
            seen_table_header = True
            continue
        if not seen_table_header:
            continue
        for word in re.split(r"[,/]", cell):
            word = word.strip().strip("*_` ")
            if len(word) >= 3 and not word.startswith("_"):
                terms.add(word.lower())
    return sorted(terms)


def core_paths(core_file: Path) -> set[str]:
    if not core_file.exists():
        return set()
    declared: set[str] = set()
    for line in core_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        for match in re.findall(r"`([^`]+)`", line):
            if "/" in match or match.endswith(tuple(SOURCE_SUFFIXES)):
                declared.add(match.strip().rstrip("/"))
    return declared


def iter_modules(root: Path) -> list[Path]:
    modules: list[Path] = []
    src = root / "src"
    base = src if src.is_dir() else root
    for child in sorted(base.iterdir()):
        if child.is_dir() and child.name not in STOP_DIRS and not child.name.startswith("."):
            if any(p.suffix in SOURCE_SUFFIXES for p in child.rglob("*") if p.is_file()):
                modules.append(child)
    return modules


def module_terms(module: Path) -> set[str]:
    words: set[str] = set()
    for path in module.rglob("*"):
        if path.suffix in SOURCE_SUFFIXES:
            words.update(w.lower() for w in re.split(r"[^A-Za-zА-Яа-я0-9]+", path.stem) if len(w) >= 3)
            if path.stat().st_size < 512_000:
                for match in DOMAIN_BLOCK_MARKERS.findall(path.read_text(encoding="utf-8", errors="ignore")):
                    name = match.split()[-1].rstrip("=")
                    if len(name) >= 3:
                        words.add(name.lower())
    return words


def domain_block_hits(module: Path) -> int:
    hits = 0
    for path in module.rglob("*"):
        if path.suffix in SOURCE_SUFFIXES and path.stat().st_size < 512_000:
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits += len(DOMAIN_BLOCK_MARKERS.findall(text))
    return hits


def classify(module: Path, root: Path, terms: list[str]) -> tuple[str, list[str]]:
    evidence: list[str] = []
    name = module.name
    score = 0
    if GENERIC_PATTERNS.search(name):
        evidence.append(f"name matches generic/technical pattern: {name}")
        return "likely-supporting", evidence
    words = module_terms(module)
    matched = [t for t in terms if any(t in w or w in t for w in words)]
    if matched:
        score += 2 * len(matched)
        evidence.append(f"ubiquitous-language terms in identifiers: {', '.join(matched[:8])}")
    hits = domain_block_hits(module)
    if hits:
        score += min(hits, 20) // 5
        evidence.append(f"{hits} domain-type definitions (class/interface/type/enum)")
    if not evidence:
        return "uncertain", ["no domain-language or type-definition signals found"]
    if score >= 4:
        return "core-candidate", evidence
    return "uncertain" if score >= 1 else "likely-supporting", evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--mode", choices=["apply", "dry-run"], default="apply")
    args = parser.parse_args()
    root = args.project_root.resolve()
    constitution = root / "docs/constitution.md"
    terms = constitution_terms(constitution)
    declared = core_paths(root / "CORE.md")
    modules = iter_modules(root)
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    out_dir = root / "docs/core-proposals"
    out = out_dir / f"{today}-core-proposal.md"

    lines = [
        f"# Core Domain Proposal — {today}",
        "",
        "Status of every row: **proposed**. Nothing here is in `CORE.md` until the owner",
        "accepts a row and promotion is recorded in an ADR (Task Protocol step 4).",
        "",
    ]
    if not terms:
        lines += [
            "> WARNING: no ubiquitous-language terms found in `docs/constitution.md`.",
            "> Signal quality is degraded — classification relies on type-definition density",
            "> and generic-name penalties only. Fill the constitution term table first.",
            "",
        ]
    lines += [
        f"Ubiquitous-language terms loaded: {len(terms)}. Modules scanned: {len(modules)}.",
        "Modules already declared in CORE.md are listed as `declared`.",
        "",
        "| Path | Suggestion | Evidence | Owner decision (accept/reject) |",
        "|---|---|---|---|",
    ]
    for module in modules:
        rel = module.relative_to(root).as_posix()
        if rel in declared or any(rel.startswith(d + "/") for d in declared):
            lines.append(f"| `{rel}` | declared | already in CORE.md | — |")
            continue
        suggestion, evidence = classify(module, root, terms)
        lines.append(f"| `{rel}` | {suggestion} | {'; '.join(evidence)} | |")
    lines += [
        "",
        "## How to complete this proposal",
        "",
        "1. Owner marks each row accept or reject (judging business value, not the score).",
        "2. Agent applies accepted rows to `CORE.md` and writes an ADR per promoted path.",
        "3. Accepted rows never reappear; re-runs only diff against CORE.md.",
    ]
    if args.mode == "dry-run":
        print("\n".join(lines))
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"PROPOSAL {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
