# ADR-007: Durable webhook inbox and Redis delivery

- **Status:** Accepted (M0)
- **Context:** Provider webhooks duplicate, reorder, fail and may arrive while services restart.
- **Decision:** Verify raw signatures first, insert a uniquely deduplicated PostgreSQL inbox event, then enqueue its ID through Redis. Idempotent workers normalize and apply only newer resource versions; retries end in a durable dead-letter state.
- **Consequences:** PostgreSQL, not Redis, proves receipt and outcome. Workers need reconciliation, queue monitoring and retention controls.
