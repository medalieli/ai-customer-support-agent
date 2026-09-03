# Architecture Decision Records

ADRs are accepted for M0 and may be superseded only by a later numbered record. They capture durable choices; operational details remain in the main specifications.

| ADR | Decision |
|---|---|
| [001](001-system-boundaries.md) | Next.js, FastAPI and worker boundaries |
| [002](002-durable-langgraph.md) | LangGraph typed state and PostgreSQL checkpoints |
| [003](003-provider-ports.md) | Application-owned mock/real provider contracts |
| [004](004-hybrid-rag.md) | pgvector/full-text hybrid RAG, local reranking and citations |
| [005](005-guarded-actions.md) | Server-enforced confirmation, idempotency and audit |
| [006](006-refund-decisions.md) | Deterministic refund eligibility and human approval |
| [007](007-webhook-jobs.md) | Durable webhook inbox with Redis-backed workers |
| [008](008-human-handoff.md) | LangGraph interrupt/resume and explicit staff ownership |
| [009](009-model-api-and-privacy.md) | Responses API strict tools and no private reasoning storage |
| [010](010-arq-background-worker.md) | ARQ for Redis-backed Python jobs |
| [011](011-provider-integration-sequencing.md) | Deliver provider adapters in M5; reserve M12/M13 for live validation |
