# ADR-010: ARQ for Redis-backed background jobs

- **Status:** Accepted (M1)
- **Context:** ADR-001 and ADR-007 require a separate Redis-backed async worker but intentionally did not select a Python job library.
- **Decision:** Use pinned ARQ with the same typed settings package as FastAPI. M1 exposes only ARQ's worker health key and a health-verification task/probe. Future durable jobs carry PostgreSQL record IDs; Redis remains delivery rather than system of record.
- **Consequences:** The foundation is small, async-native, and operationally testable. ARQ constrains the compatible redis-py major version, tasks must be idempotent, and later retry/DLQ semantics require explicit implementation in M11.
