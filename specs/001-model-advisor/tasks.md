# Tasks

Спецификация: `spec.md` (AC-1…AC-19). Порядок — этапы ТЗ §8. Задача governance: `task-2026-10-17-001`.

- [x] 0. Этап 0 (выполнен 2026-10-17, см. `stage0/report.md`): живая проверка AA и LMArena, сопоставление имён, согласованность шкал → `stage0/report.md`; закрыть [NEEDS CLARIFICATION] в `spec.md`; финальная таблица категорий (AC: 3, 4, 9, 12)
- [ ] 1. Gate 1: владелец проверяет `plan.md`, решает по ADR-0001 и закрывает Q1, Q2 в `spec.md` (D1, D3–D5 приняты) (AC: —)
- [ ] 2. `fetch_models.py`, фикстуры и тесты каталога; обновить `docs/architecture/*` (AC: 1, 2)
- [ ] 3. `fetch_scores.py`: адаптеры AA и LMArena, резервный режим, сопоставление имён; тесты (AC: 3, 4, 18) — после 2
- [ ] 4. `query.py` — расчёты и заголовок: value, нормализация по источнику, флагманы, spotlight; `config.json` (AC: 5–12, 17) — после 3
- [ ] 5. `query.py` — show, compare, search, плюсы и минусы, перевод/целостность (AC: 13–16) — после 4
- [ ] 6. `SKILL.md`, `references/maintenance.md`, процедура перевода агентом (AC: 15, 19) — [P] с 5
- [ ] 7. Приёмка на живых данных, отчёт о прогоне, настройка порогов spotlight (AC: все)
- [ ] 8. Обновить constitution (если изменился язык) и закрыть task-запись: коммит, тест-команда, результат

## Definition of Done

- [ ] Acceptance criteria from spec.md pass (tests or verified behavior)
- [ ] Task record updated: commit hash, files changed, test command and result
- [ ] Architecture docs updated in the same commit if structure changed (`docs/architecture/*`)
- [ ] Constitution updated if language, vision or principles changed
- [ ] ADR/AgDR written for every decision made along the way
