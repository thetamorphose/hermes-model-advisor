# Tasks

Спецификация: `spec.md` (AC-1…AC-20). Порядок — этапы ТЗ §8. Задача governance: `task-2026-10-17-001`.

- [x] 0. Этап 0 (выполнен 2026-10-17, см. `stage0/report.md`): живая проверка AA и LMArena, сопоставление имён, согласованность шкал → `stage0/report.md`; закрыть [NEEDS CLARIFICATION] в `spec.md`; финальная таблица категорий (AC: 3, 4, 9, 12)
- [x] 1. Gate 1: ADR-0001 принят (A1, B1, C2), решения D1–D5 приняты (2026-10-17)
- [x] 2. `skill/scripts/fetch_models.py`, фикстуры и тесты каталога; `docs/architecture/*` обновлены (AC: 1, 2) — 18 тестов, живой прогон 426 → 321 семейство
- [ ] 3. `fetch_scores.py`: адаптеры AA и LMArena, резервный режим, сопоставление имён; тесты (AC: 3, 4, 18) — после 2
- [ ] 4. `query.py` — расчёты и заголовок: value, нормализация по источнику, флагманы, spotlight; `config.json` (AC: 5–12, 17) — после 3
- [ ] 5. `query.py` — show, compare, search, плюсы и минусы, перевод/целостность (AC: 13–16) — после 4
- [ ] 6. `SKILL.md`, `references/maintenance.md`, процедура перевода агентом (AC: 15, 19) — [P] с 5
- [ ] 7. Приёмка на живых данных, отчёт о прогоне, настройка порогов spotlight (AC: все)
- [ ] 8. Обновить constitution (если изменился язык) и закрыть task-запись: коммит, тест-команда, результат

## Передача в следующую сессию

Состояние на конец сессии 1. Код-коммит: `cd8307a` (шаги 0–2 готовы). Следующий шаг — **3 (`fetch_scores.py`)**.

**Как начать:** «Следуй `tools/ai-agent-governance/SKILL.md`, продолжи `task-2026-10-17-001`, шаг 3 из `specs/001-model-advisor/tasks.md`». Прочитать: `docs/constitution.md`, `spec.md`, `plan.md`, `stage0/report.md`, `docs/agdr/0001-*.md`, `skill/scripts/fetch_models.py` (образец стиля и контракта).

**Проверки перед работой и после каждого шага:**
- `python -m unittest discover -s tests` — сейчас 18 тестов, все проходят.
- `python tools/ai-agent-governance/scripts/validate_governance.py --project-root . --strict` — сейчас OK.
- Коммиты с трейлером `Task: task-2026-10-17-001`.

**Среда:** Windows, команда `python` (не `python3`), оболочка bash. Предупреждения Git про LF/CRLF безвредны. Кириллица в пути проблем не создаёт. Ключ AA лежит в `.env` в корне репозитория (35 символов); значение не печатать, не писать в файлы, не включать в тексты ошибок.

### Шаг 3: что известно про источники (проверено живыми запросами, этап 0)

**Artificial Analysis**
- `GET https://artificialanalysis.ai/api/v2/data/llms/models`, заголовок `x-api-key`. Один запрос отдаёт всё: 704 записи.
- Каждая запись — конфигурация модели: усилие стоит в имени, например `Claude Opus 5.5 (Max, Default Fallback)`, слаг вида `claude-opus-5-5-high`. Метки усилия: `max, xhigh, high, medium, low, minimal, non-reasoning` и «без метки» (у большинства моделей конфигурация одна).
- Нужные поля `evaluations`: `artificial_analysis_intelligence_index`, `artificial_analysis_coding_index`, `terminalbench_hard`. Поля `artificial_analysis_math_index` и `tau2` не нужны: reasoning переведён на общий рейтинг (D5), agentic = `terminalbench_hard` (D4).
- Без ключа и с неверным ключом — `401` (`API key is required` / `Invalid API key.`). Ответы `403`, `429` и суточный лимит **не проверены**; резервный режим должен обработать их как недоступность.

**LMArena** (`https://datasets-server.huggingface.co`, датасет `lmarena-ai/leaderboard-dataset`, split `latest`)
- Нужны конфигурации и категории: `text` → `overall`, `coding`; `vision` → `overall`; `agent` → `overall`. Колонки: `model_name, rating, category, leaderboard_publish_date`. Резерв для reasoning — `text overall` (D5).
- Размеры: `text` 11033 строки (111 страниц по 100), `vision` 1124, `agent` 52.
- **Лимит Hugging Face.** Постраничная выгрузка `text` целиком упёрлась в `429`; помогла экспоненциальная пауза 5→40 с, всё заняло несколько минут. Один запрос к `/filter` вернул `500: the dataset index is loading` — штатное поведение фильтра не проверено. Нужно: попробовать `/filter` по `category`, при неудаче — пагинация с паузами; при неполной загрузке записать это в `scores.json`, а не отдавать частичные данные как полные.
- В `vision` смешаны даты (34 строки от 2026-01-09, остальные 2026-10-09): для пары модель×категория брать самую свежую.
- Имена содержат усилие в скобках (`Claude Opus 5.5 (High)`) или суффиксом (`gpt-6-sol-max`). У 388 из 399 моделей `text overall` одна конфигурация; Medium всего у 2.

### Шаг 3: правила, которые легко нарушить

1. **Сопоставление имён — только точное равенство после нормализации** (нижний регистр, точки и подчёркивания → дефисы, скобки и метка усилия отброшены). Нечёткие совпадения идут в `name_map.json` со статусом `candidate` и в выводе не используются: `gpt-6-sol-pro` ≠ `gpt-6-sol`, `gpt-terra` ≠ `gpt-5-6-terra`. Пример нормализации — `specs/001-model-advisor/stage0/p9_deep.py`.
2. **Алиасы (`alias: true`) оценок не получают**; не-чат семейства (`chat: false`) в рекомендации не идут, но оценки могут иметь. Сопоставлять по `family_id`/`representative_id` из `skill/data/models.json`.
3. **Конфигурация усилия (D1):** Medium, иначе порядок `medium → high → low → xhigh → max → minimal → non-reasoning`; единственная конфигурация берётся как есть; выбранная конфигурация сохраняется в оценке (поле для пометки «конфигурация: High»). Порядок вынести в `config.json`.
4. **Резервный режим:** `status: unavailable` + `reason` из набора `no_key | auth_error | rate_limited | network | bad_response`; значения AA из прошлого снапшота не переносить; ошибка AA не должна ронять сбор LMArena и наоборот.
5. **Выбор источника по модели:** AA, а если у модели нет значения метрики — аналог из LMArena; источник в каждом значении `{value, source, metric, date}`. Нет значения — `null`, не ноль.
6. **Секреты:** ключ не попадает в `scores.json`, `name_map.json`, stdout/stderr и тексты исключений (фильтровать). Тест на это обязателен (AC-4, AC-18).
7. Схему снапшота снабдить `schema_version`, запись атомарная (как в `fetch_models.py`): при полном провале прежний файл остаётся.

### Шаг 3: открытое мелкое решение
Где искать ключ: по ТЗ NFR-1 — переменная `AA_API_KEY`, затем `.env` «в корне скилла». После раскладки `skill/` (ADR-0001, C2) файл `.env` фактически лежит в корне репозитория. Предложение: искать `AA_API_KEY` → `skill/.env` → `.env` в корне репозитория, зафиксировать в AgDR. Если сомнений нет — принять без вопросов к владельцу; `.env` уже в `.gitignore`.

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
