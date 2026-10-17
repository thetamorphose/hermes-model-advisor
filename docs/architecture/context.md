# System Context

```mermaid
graph LR
    Owner[Product owner] --> Agent[AI agent]
    Agent --> Project[Target project]
    Agent --> Hermes[(Hermes state.db: read-only)]
```
