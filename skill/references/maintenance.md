# Обслуживание советника по моделям Nous

Процедура обновления данных и диагностика. Ссылки на правила — `docs/agdr/0001…0005`, термины — `docs/constitution.md`. Команды ниже выполняются из папки скилла (`skill/`).

## 1. Обновление каталога

```bash
python scripts/fetch_models.py [--token T] [--output data/models.json] [--from-file RAW.json]
```

- Источник: `https://inference-api.nousresearch.com/v1/models` (Bearer-токен).
- Токен ищется в порядке: аргумент `--token` → переменная `NOUS_API_TOKEN` → `auth.json` (`~/.hermes`, `$HERMES_HOME`, `%LOCALAPPDATA%\hermes`). Токен необязателен.
- Цены нормализуются в **$/1M токенов** (API отдаёт цену за токен, умножается на 1e6). Дубли схлопываются в семейства (AgDR-0001); представитель — базовый вариант без суффиксов.
- Пишет `data/models.json` атомарно (tmp + rename): при ошибке сети предыдущий снапшот сохраняется.
- `--from-file` берёт ответ из файла — для офлайн-проверок и тестов.

Схема `models.json`: `{schema_version: 1, fetched_at, source, count, family_count, families:[…]}`. У каждого семейства: `family_id`, `representative_id`, `variant_ids`, `alias`, `name`, `created`, `context_length`, `modalities`, `modalities_from`, `chat`, `pricing_per_1m`, `price_variants`, `description`. Описания в каталоге приходят уже укороченными (около 200–330 символов, с `...` на конце) — это формат источника, а не обрезка скриптом.

## 2. Обновление оценок

```bash
python scripts/fetch_scores.py [--models data/models.json] [--output data/scores.json] \
    [--name-map data/name_map.json] [--config config.json] [--aa-file X.json] [--arena-file X.json]
```

- Источники: Artificial Analysis (REST API, ключ `AA_API_KEY`) и LMArena (открытый датасет `lmarena-ai/leaderboard-dataset` через datasets-server Hugging Face, CC-BY-4.0).
- Ключ AA: переменная `AA_API_KEY` → `skill/.env` → `.env` в корне репозитория. Ключ не печатается ни в выводе, ни в `scores.json`, ни в тексте причины ошибки.
- **Резервный режим:** нет ключа / 401–403 / 429 / сеть / нераспознанный ответ → сбор не падает, все оценки из LMArena; у AA `status: unavailable` с кодом причины (`no_key | auth_error | rate_limited | network | bad_response`). Старые значения AA из прошлого снапшота не переносятся.
- Пишет `data/scores.json` и `data/name_map.json`. Оба — атомарно; при полном отказе обоих источников предыдущие файлы сохраняются.
- Живой прогон идёт минуты из-за лимитов Hugging Face (повторы с паузами 5→40 с); LMArena `/filter` отвечает нестабильно (500/502) — сбор делает повторы, при отказе берёт `/rows` и помечает `status: partial`.

Схема `scores.json`: `{schema_version: 1, fetched_at, mode: full|aa_only|fallback, sources:{artificial_analysis, lmarena}, model_count, models:{family_id:{overall,coding,agentic,vision}}}`. Каждое значение — `{value, source, metric, date, config, matched_name}` или `null`. Конфигурация выбирается по каждой метрике отдельно (AgDR-0002); пометка «конфигурация: X» нужна, когда `config` не `medium` и не `null`.

## 3. Перевод описаний (делает агент)

Перевод выполняется агентом: у скрипта на stdlib нет переводчика (FR-5, out of scope). Результат — `data/descriptions_ru.json`.

**Схема файла** (контракт AgDR-0004):

```json
{
  "schema_version": 1,
  "descriptions": {
    "<family_id>": {
      "text": "Русский перевод",
      "source_hash": "<sha256>",
      "translated_at": "YYYY-MM-DD"
    }
  }
}
```

**Хеш оригинала считается только функцией `query.description_hash`**, а не вручную:

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path("skill/scripts").resolve()))
import query
h = query.description_hash(описание_из_models.json)   # sha256 текста с нормализованными пробелами
```

Несовпадение хеша → оригинал показывается с пометкой «перевод устарел». Запись без любого из трёх полей (`text`, `source_hash`, `translated_at`) игнорируется. Битый файл или иной `schema_version` — предупреждение и игнорирование, без падения.

**Какие модели переводить** (AgDR-0005): детерминированный «видимый набор» в порядке приоритета — флагманы → spotlight свежие (показанные) → spotlight дешёвые (показанные) → топ-N каждой категории рекомендаций; дедупликация, пустые описания пропускаются (у 10 семейств Anthropic описание пустое). Ориентир объёма из ТЗ — 40–60; фактически видимый набор в текущем каталоге — 67.

**Порядок обновления перевода:**

1. Вычислить видимый набор (команды ниже) и выгрузить `family_id` + `description` для каждой модели.
2. Перевести каждое описание на русский; пустые пропустить.
3. Считать `source_hash` через `query.description_hash` для каждого.
4. Записать `data/descriptions_ru.json` с `schema_version: 1` и `translated_at` = сегодня.
5. Проверить: `python scripts/query.py models` — описания с пометкой «машинный перевод, дата»; нет предупреждений «перевод устарел».

Помощник для выгрузки видимого набора (повторяет логику шага 6):

```python
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path("skill/scripts").resolve()))
import query
cfg = json.loads(Path("skill/config.json").read_text(encoding="utf-8"))["query"]
models = json.loads(Path("skill/data/models.json").read_text(encoding="utf-8"))
scores = json.loads(Path("skill/data/scores.json").read_text(encoding="utf-8"))
families, smap = models["families"], scores["models"]
flag = query.flagships(families, smap, cfg)
spot = query.spotlight(families, smap, cfg, query.parse_time(models["fetched_at"]).timestamp())
ids = [e["family"]["family_id"] for e in flag["rows"]] \
    + [f["family_id"] for f in spot["fresh"]] \
    + [e["family"]["family_id"] for e in spot["cheap"]]
seen, queue = set(), []
for fid in ids:
    if fid in seen: continue
    seen.add(fid)
    queue.append(fid)
# при желании добавить топ рекомендаций каждой категории и повторить дедупликацию
```

## 4. Диагностика сопоставления имён

`data/name_map.json` хранит автосопоставление «каталог Nous ↔ бенчмарк»:

```json
{
  "schema_version": 1, "generated_at": "...",
  "rule": "exact equality after normalization; candidates are not used in output",
  "matches": {"<family_id>": {"source": "...", "benchmark_name": "..."}},
  "candidates": [{"family_id": "...", "source": "...", "candidate_name": "..."}],
  "ambiguous_catalog_keys": {"<key>": ["<источник-имена…>"]}
}
```

- `matches` — уверенные совпадения (точное равенство после нормализации); только они дают оценки.
- `candidates` — нечёткие совпадения (порог 0,85); **не используются в выводе** (правило 1: нет уверенного совпадения → «нет данных», а не оценка соседней модели).
- `ambiguous_catalog_keys` — ключи каталога, которым соответствует несколько имён бенчмарка; оценок не дают.

Если у модели «нет данных», хотя она, кажется, есть в бенчмарке — смотрите `candidates` и `ambiguous_catalog_keys`: вероятно, это нечёткое или неоднозначное сопоставление. Пересмотр порога/правила — решение владельца, не агента (зафиксировано в шаге 3). Пересбор сопоставления — повторный `fetch_scores.py`.

## 5. Диагностика источников и свежести

- Заголовок каждого вывода показывает дату и возраст обоих снапшотов и статус источников; старше `max_snapshot_age_days` (по умолчанию 7) — подсказка с командами обновления.
- `sources.artificial_analysis.status == "unavailable"` → заголовок называет причину из `config.json` (`query.unavailable_reasons`) и сообщает, что оценки из LMArena.
- `sources.lmarena.status == "partial"` → какие метрики недогружены (`incomplete`), остальные считаются из того, что есть.
- Резервный режим (`mode: fallback`) — все рейтинги из Arena; метрики без аналога в LMArena показываются как «нет данных».

## 6. Распространение и лицензии

- В дистрибутив скилла входит только `skill/` (ADR-0001, часть C). `docs/`, `specs/`, `tests/`, `tools/` — рабочие материалы репозитория.
- `data/scores.json` и `data/name_map.json` содержат данные Artificial Analysis («internal use only, no redistribution») — **не публикуются и не входят в дистрибутив**; в `.gitignore`. Каждый пользователь создаёт их локально своим ключом.
- `data/models.json` и `data/descriptions_ru.json` — описания каталога Nous и их перевод, в репозитории (не данные AA).
- LMArena — CC-BY-4.0, атрибуция есть в выводе `query.py`.

## 7. Troubleshooting

| Симптом | Причина и действие |
|---|---|
| `schema_version X, поддерживается 1` | Снапшот создан иной версией скриптов. Обновите снапшоты `fetch_models.py` / `fetch_scores.py`. |
| `Нет файла снапшота каталога` | `data/models.json` отсутствует. Выполните `fetch_models.py`. |
| Все оценки из LMArena, хотя AA раньше работал | Резервный режим: проверьте заголовок (причина) и ключ `AA_API_KEY` / `skill/.env`. |
| У модели «нет данных», но она в бенчмарке | См. §4 — нечёткое/неоднозначное сопоставление или конфигурация без нужной метрики. |
| `перевод устарел` | Оригинал описания в каталоге изменился после перевода. Переведите заново по §3. |
| Описание отсутствует | В каталоге нет текста (например, 10 семейств Anthropic) — переводить нечего, так и показывается. |
