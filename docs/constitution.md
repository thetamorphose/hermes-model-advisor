# Project Constitution

## Domain vision

_TODO: owner-approved domain vision._

## Ubiquitous language

| Term | Meaning | Disallowed synonyms |
|---|---|---|

## Architecture principles

1. Documentation in this repository is the source of truth.
2. One fact has one canonical home.
3. Structural code changes update relevant architecture documentation in the same commit.
4. Missing domain terminology blocks implementation until clarified by the owner.

## Maintenance rules (memory bank)

This constitution is the project's memory bank — durable context read by every agent session.

1. Updating it is part of every task's Definition of Done: if a task changed the language, vision or principles, the constitution changes in the same commit.
2. If this file grows past ~3000 words, split it: stable architecture detail moves to `docs/architecture/system.md`, current project state to `docs/progress.md`; this file keeps only vision, language and principles.
3. Per-feature specifications live under `specs/` and are spec-first: they describe a change while it is implemented. They never replace this file, ADR, architecture docs, code or tests as the durable record.
