---
name: nous-model-advisor
description: Подбор и сравнение моделей Nous Portal по цене и качеству.
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [models, llm, pricing, benchmarks, comparison]
---

# Советник по моделям Nous

Советник отвечает на три вопроса подписчика Nous Portal: какие модели доступны, сколько они стоят и какая лучше подойдёт под задачу. Каталог и цены берутся из API Nous, оценки качества — из внешних бенчмарков (Artificial Analysis, при его недоступности — LMArena). У каждой цифры указан источник и дата; нет данных — советник так и говорит, а не придумывает оценку. Скрипты работают на чистом Python 3.10+ без сторонних пакетов и без сети (кроме двух команд обновления).

## When to Use

- Пользователь спрашивает, какие модели сейчас доступны в Nous и сколько стоят.
- Пользователь называет задачу и хочет топ моделей по цене/качеству.
- Пользователь просит справку по одной модели или сравнить несколько.
- Пользователь просит обновить каталог или оценки.

Не используйте для: ручной курации оценок, профилей «задача → модель», автоматического обновления по расписанию.

## Prerequisites

- Python 3.10+ (только stdlib: `urllib`, `json`, `argparse`, `pathlib`).
- Токен Nous для обновления каталога: аргумент `--token` → переменная `NOUS_API_TOKEN` → `auth.json` (`~/.hermes`, `$HERMES_HOME`, `%LOCALAPPDATA%\hermes`).
- Ключ Artificial Analysis (необязателен): переменная `AA_API_KEY` → файл `.env` в папке скилла (строка `AA_API_KEY=...`). Без ключа или при недоступности AA включается резервный режим — все оценки из LMArena.

## Quick Reference

```bash
# обновление данных
python skill/scripts/fetch_models.py          # каталог → skill/data/models.json
python skill/scripts/fetch_scores.py          # оценки → skill/data/scores.json, name_map.json

# запросы (работают офлайн на локальных снапшотах)
python skill/scripts/query.py recommend --task coding [--top N]
python skill/scripts/query.py models [--set flagships|spotlight]
python skill/scripts/query.py show <модель>
python skill/scripts/query.py compare <модель> <модель> [...]
python skill/scripts/query.py search <часть имени> [--limit N]
```

Глобальный флаг `--now <ISO-8601>` фиксирует текущее время для воспроизводимых проверок (ставится перед командой). Категории задач (`--task`): `coding`, `reasoning`, `agentic`, `vision`, `business_spec`, `tech_spec`, `writing`, `translation`, `data_analysis`, `creative`, `budget`.

## Procedure — обновление данных

1. **Каталог.** `python skill/scripts/fetch_models.py` — тянет каталог Nous, нормализует цены в $/1M токенов, схлопывает дубли в семейства. Результат: `skill/data/models.json` с `fetched_at`.
2. **Оценки.** `python skill/scripts/fetch_scores.py` — собирает AA и LMArena. При недоступности AA завершается успешно на данных LMArena; в `scores.json` у AA `status: unavailable` и код причины без секретов. Результат: `skill/data/scores.json`, `skill/data/name_map.json`.
3. **Перевод описаний.** Выполняется агентом (у скрипта на stdlib нет переводчика) — см. `skill/references/maintenance.md`, раздел «Перевод описаний». Результат: `skill/data/descriptions_ru.json`.
4. **Проверка.** `python skill/scripts/query.py models` — заголовок свежести без предупреждений «старше N дн.», описания с пометкой «машинный перевод» и датой.

## Pitfalls

- `scores.json` и `name_map.json` содержат данные Artificial Analysis (лицензия «internal use only, no redistribution») — не публиковать и не коммитить; каждый пользователь создаёт их локально своим ключом.
- LMArena распространяется под CC-BY-4.0 — атрибуция есть в выводе, её нельзя убирать.
- `name_map.json` не в Git; проверка целостности для него работает на машине пользователя.
- Полный живой прогон `fetch_scores.py` идёт минуты из-за лимитов Hugging Face (повторы с паузами 5→40 с); запускайте его в фоне.
- Резервный режим не переносит старые значения AA из прошлого снапшота: снапшот описывает один момент времени.
- `descriptions_ru.json` и `name_map.json` — необязательные вспомогательные файлы: отсутствуют → работают без них, битые или иной `schema_version` → предупреждение и игнорирование, без падения.

## Verification

- `python skill/scripts/query.py recommend --task coding` — топ по value, рядом исходные значения, источник и дата.
- `python skill/scripts/query.py models --set flagships` — по одной модели на разработчика, AA приоритетнее Arena, рядом цена и value.
- Каждый вывод начинается с дат и возраста обоих снапшотов; старше 7 дн. — подсказка с командами обновления.
- Одинаковые снапшоты и конфигурация дают побайтно одинаковый вывод (AC-17).
- В выводе нигде нет ключа AA.
