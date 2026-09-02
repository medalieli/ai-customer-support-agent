# ADR-002: Durable typed orchestration

- **Status:** Accepted (M0)
- **Context:** Confirmations and human handoffs may pause for minutes or days and must survive restarts.
- **Decision:** Use LangGraph with versioned typed state and PostgreSQL checkpointing. Use explicit interrupts for confirmation/approval/handoff and resume through authenticated, optimistic-locking application endpoints.
- **Consequences:** Flows are inspectable and resumable; state migrations and stale-action invalidation must be designed. Completed side effects are referenced, never replayed.
