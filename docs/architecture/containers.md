# Containers

Граница дистрибутива скилла — папка `skill/` (ADR-0001, часть C). Остальное — рабочие материалы проекта.

```mermaid
graph TB
    subgraph Skill["skill/ (дистрибутив, stdlib-only)"]
      FM[scripts/fetch_models.py]
      FS[scripts/fetch_scores.py - planned]
      Q[scripts/query.py - planned]
      Cfg[config.json - planned]
      Models[(data/models.json)]
      Scores[(data/scores.json - не распространяется)]
      NameMap[(data/name_map.json)]
      Desc[(data/descriptions_ru.json)]
    end
    subgraph Project["Рабочие материалы проекта"]
      Tests[tests/ - unittest, фикстуры]
      Specs[specs/ - spec, plan, tasks, stage0]
      Docs[docs/ - constitution, ADR, AgDR, history]
      Tools[tools/ - governance]
      Logs[logs/ - redacted audit]
    end
    Nous[(API Nous)] --> FM
    FM --> Models
    AA[(Artificial Analysis)] --> FS
    Arena[(LMArena)] --> FS
    FS --> Scores
    FS --> NameMap
    Models --> Q
    Scores --> Q
    NameMap --> Q
    Desc --> Q
    Cfg --> Q
    Tests -.проверяют.-> FM
```

Контракт между скриптами — JSON-файлы в `skill/data/` с полем `schema_version` (сейчас 1 для `models.json`). `fetch_models.py` пишет снапшот атомарно: при ошибке прежний файл остаётся нетронутым.
