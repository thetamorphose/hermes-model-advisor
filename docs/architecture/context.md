# System Context

Советник по моделям Nous: портативный скилл, который читает каталог Nous Portal и внешние бенчмарки и отвечает на вопросы о выборе модели.

```mermaid
graph LR
    User[Подписчик Nous Portal / AI-агент] --> Skill[Скилл model-advisor]
    Skill -->|каталог и цены, токен необязателен| Nous[(API Nous: /v1/models)]
    Skill -->|оценки, ключ AA_API_KEY| AA[(Artificial Analysis API)]
    Skill -->|оценки, резерв при недоступности AA| Arena[(LMArena: Hugging Face datasets-server)]
    Owner[Владелец продукта] --> Agent[AI-агент разработки]
    Agent --> Repo[Репозиторий проекта]
    Agent --> Hermes[(Hermes state.db: read-only)]
```

Статус реализации: подключены API Nous (`fetch_models.py`), AA и LMArena (`fetch_scores.py`); `query.py`: `recommend` и `models` готовы (шаг 4), `show`, `compare`, `search` — шаг 5 в `specs/001-model-advisor/tasks.md`.
