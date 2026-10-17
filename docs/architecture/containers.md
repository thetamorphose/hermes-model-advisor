# Containers

```mermaid
graph TB
    subgraph Project
      Code[Application code]
      Docs[Governance artifacts]
      Logs[Redacted JSONL audit logs]
    end
    Agent[AI agent] --> Code
    Agent --> Docs
    Agent --> Logs
```
