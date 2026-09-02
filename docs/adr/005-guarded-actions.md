# ADR-005: Guarded action gateway

- **Status:** Accepted (M0)
- **Context:** Model output cannot authorize customer-data access or external mutations.
- **Decision:** All tools pass a server gateway that injects trusted identity and enforces tenant, ownership, RBAC, capability and rules. Writes use previewed action hashes, expiring single-use confirmation, state revalidation, stable idempotency keys and immutable audit events.
- **Consequences:** The LLM may propose but cannot grant authority. Actions require additional storage/state and careful unknown-outcome reconciliation.
