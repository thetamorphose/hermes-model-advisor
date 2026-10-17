# Tasks

Спецификация: `spec.md` (AC-1…AC-20). Порядок — этапы ТЗ §8. Задача governance: `task-2026-10-17-001`.

- [x] 0. Этап 0 (выполнен 2026-10-17, см. `stage0/report.md`): живая проверка AA и LMArena, сопоставление имён, согласованность шкал → `stage0/report.md`; закрыть [NEEDS CLARIFICATION] в `spec.md`; финальная таблица категорий (AC: 3, 4, 9, 12)
- [x] 1. Gate 1: ADR-0001 принят (A1, B1, C2), решения D1–D5 приняты (2026-10-17)
- [x] 2. `skill/scripts/fetch_models.py`, фикстуры и тесты каталога; `docs/architecture/*` обновлены (AC: 1, 2) — 18 тестов, живой прогон 426 → 321 семейство
- [x] 3. `fetch_scores.py`: адаптеры AA и LMArena, резервный режим, сопоставление имён; тесты (AC: 3, 4, 18) — 61 тест всего (43 новых), живой прогон: AA ok, LMArena ok, 172 семейства с оценками; AgDR-0002
- [x] 4. `query.py` — расчёты и заголовок: value, нормализация по источнику, флагманы, spotlight; `config.json` (AC: 5–12, 17, 20) — 106 тестов всего (45 новых), живой вывод проверен на 321 семействе; AgDR-0003 (value флагманов — решение владельца принято)
- [ ] 5. `query.py` — show, compare, search, плюсы и минусы, перевод/целостность (AC: 13–16) — после 4
- [ ] 6. `SKILL.md`, `references/maintenance.md`, процедура перевода агентом (AC: 15, 19) — [P] с 5
- [ ] 7. Приёмка на живых данных, отчёт о прогоне, настройка порогов spotlight (AC: все)
- [ ] 8. Обновить constitution (если изменился язык) и закрыть task-запись: коммит, тест-команда, результат

## Передача в следующую сессию

Состояние на конец сессии 3. Шаги 0–4 готовы. Код-коммит шага 4 — см. `git log` (трейлер `Task: task-2026-10-17-001`). Следующий шаг — **5 (`query.py`: show, compare, search, плюсы и минусы, перевод и целостность)**; шаг 6 можно вести параллельно.

**Как начать:** «Следуй `tools/ai-agent-governance/SKILL.md`, продолжи `task-2026-10-17-001`, шаг 5 из `specs/001-model-advisor/tasks.md`». Прочитать: `docs/constitution.md`, `spec.md` (AC-13…AC-16), ТЗ v0.6 FR-4, FR-5, FR-8, `docs/agdr/0003-*.md`, раздел `query` в `skill/config.json`, `skill/scripts/query.py`.

**Проверки:** `python -m unittest discover -s tests` (106 тестов), `python tools/ai-agent-governance/scripts/validate_governance.py --project-root . --strict`.

**Среда:** Windows, `python`, bash. Ключ AA в `.env` в корне (не печатать). Полный живой прогон `fetch_scores.py` идёт ~10 минут из-за лимитов Hugging Face (паузы 5→40 с): запускать в фоне, не в foreground-терминале с таймаутом 420 с.

### Что сделано на шаге 4 (для шага 5)
- `skill/scripts/query.py`: команды `recommend --task <категория> [--top N]` и `models [--set flagships|spotlight]`; глобальные `--data-dir`, `--config`, `--now` (фиксирует время для воспроизводимых проверок; `--now` стоит перед командой). Ошибки данных — `QueryError`, код 2, без трассировки.
- Чистые функции расчёта: `rated_pool`, `annotate_norm`, `attach_prices`, `recommend`, `flagships`, `spotlight`; вывод — `render_*`; заголовок — `header_lines`. Для `show`/`compare` переиспользовать `score_text`, `price_notes`, `price_pair`, `fmt_*`, `description_line`; исключение алиасов и не-чат (`exclusion_reason`) в них **не** применять.
- `skill/config.json`, раздел `query`: 11 категорий с `metric` (`coding|agentic|vision|overall`), `ratio`, `require_input` (vision), `rank_by` (budget); `quality_percentile` 50, `top_n` 10, `models_value.ratio` 1:1, `spotlight` (45 дн., 30 свежих, нижние 25% цены, 15 дешёвых), `max_snapshot_age_days` 7, `unavailable_reasons`. Для шага 5 сюда же добавятся пороги плюсов и минусов.
- Принятые допущения — AgDR-0003. Решение владельца: value флагманов и spotlight — нейтральное 1:1 (`models_value`); определение «Value» в конституции уточнено. Открытых вопросов по шагу 4 нет.
- Живой вывод (321 семейство): флагманов 24 (все по AA; флагманов по Arena нет — у каждого разработчика с Arena есть и AA), 15 разработчиков без рейтинга; spotlight — 51 свежее семейство (показаны 30), 21 «необычно дёшево» (показаны 15). Пороги spotlight настраиваются на шаге 7.
- Замечено при просмотре вывода (не баг шага 4, решать на шаге 7): в таблице recommend попадают модели с Arena-метрикой и AA в одном ранжировании — value источников сравнивается условно, пометка «источники смешаны» стоит; у Arena agent значения около 0 и отрицательные, нормализация min–max их выравнивает.

### Контракт `scores.json` (schema_version 1) — вход для шага 4
- Верхний уровень: `fetched_at`, `mode` (`full | aa_only | fallback`), `sources`, `model_count`, `models`.
- `sources.artificial_analysis`: `{status: ok, terms, entry_count}` или `{status: unavailable, reason}`; `sources.lmarena`: `{status: ok | partial | unavailable, license, leaderboard_date, metrics_loaded, incomplete{метрика: причина}}`.
- `models[family_id]` содержит ровно четыре ключа `overall, coding, agentic, vision`; значение — `null` или `{value, source, metric, date, config, matched_name}`. `config` — `null`, если у модели одна конфигурация без метки; иначе `medium/high/…` (пометка «конфигурация: X» нужна при `config` не равном `medium` и не `null`).
- Семейства без единой оценки в `models` отсутствуют (= «нет данных»). Алиасов там нет. Не-чат семейства могут быть.
- Шкалы: AA `value` — баллы (десятки), `terminalbench_hard` — доля 0–1; Arena rating ~1000–1500, Arena `agent` — доля около 0–0,2. Нормализовать только внутри источника **и метрики**.
- `name_map.json`: `matches`, `candidates` (не использовать), `ambiguous_catalog_keys`. Файл содержит имена AA, не коммитится.

### Что показал шаг 3 (живые данные)
- AA вернул 696 записей с хотя бы одной нужной метрикой. Семейств с оценками 172 из 321 (с поправкой на 321 вместо 331 из этапа 0): overall — AA 154 + Arena 16 (170 всего), coding — 95 + 47, agentic — 101 + 34, vision — 63 (только Arena).
- Конфигурация выбирается **по каждой метрике отдельно** (AgDR-0002), поэтому у одного семейства overall может быть из Medium, а coding из High; `config` у каждого значения свой. В `query.py` пометку конфигурации показывать у значения, а не у семейства.
- В AA есть метка `Reasoning` (старые модели с отдельным режимом); в порядок предпочтения она добавлена после `xhigh/max`, перед `minimal` и `non-reasoning`.
- LMArena: `/filter` отвечает нестабильно (500 «index is loading», 502); сбор делает повторы с паузами, при отказе — `/rows`. `partial` фиксируется в статусе.
- Нечётких кандидатов 52 (порог 0,85); часть из них — вероятные верные совпадения (`Nova Lite` → `nova-lite-v1`), но по правилу 1 они оценок не дают. Пересмотр правила — решение владельца, не агента.

### Шаги 4–5: что учесть
- Формулы и пороги — ТЗ v0.6 §7; все числа порогов в `config.json`, не в коде. Пороги spotlight пока черновые, настраиваются на шаге 7.
- Качество: coding = AA Coding Index (резерв Arena text coding); agentic = AA `terminalbench_hard` (резерв Arena agent overall); vision = Arena vision overall, только для моделей с `image` во входе; все остальные категории, включая reasoning, — общий рейтинг с пометкой «оценка по общему рейтингу».
- Нормализация и медиана — внутри источника; смешанная выдача помечается «источники смешаны» (AC-9).
- Флагман: внутри источника, AA приоритетнее Arena; рядом всегда цена и value (AC-10). Не-чат и алиасы в флагманы, рекомендации и spotlight не входят (AC-20), в `show` и `compare` остаются.
- Пометки расхождения цен берутся из `price_variants` семейства; скидку показывать через `list_price`, считать по текущей цене.
- Каждый вывод начинается с заголовка свежести обоих снапшотов и причины недоступности AA, если она есть (AC-5).

### Шаги 6–7
- Описания переводит агент по процедуре из `SKILL.md`: 40–60 моделей (с оценками или из spotlight), результат в `skill/data/descriptions_ru.json` с хешем оригинала, пометкой «машинный перевод» и датой.
- `skill/data/scores.json` и `skill/data/models.json` не распространяются: в `.gitignore` уже есть `scores.json` (лицензия AA, NFR-2); проверить, что в дистрибутив он не попадает.
- На приёмке показать владельцу реальный вывод `models --set spotlight` и подобрать пороги. Числа покрытия в `stage0/report.md` (331 семейство, 47%/35%) посчитаны упрощённо до появления `fetch_models.py`; фактически семейств 321 — сравнивать с ними результат шага 3 надо с поправкой.

## Definition of Done

- [ ] Acceptance criteria from spec.md pass (tests or verified behavior)
- [ ] Task record updated: commit hash, files changed, test command and result
- [ ] Architecture docs updated in the same commit if structure changed (`docs/architecture/*`)
- [ ] Constitution updated if language, vision or principles changed
- [ ] ADR/AgDR written for every decision made along the way
