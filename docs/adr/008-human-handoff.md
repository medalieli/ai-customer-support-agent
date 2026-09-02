# ADR-008: Explicit AI/staff ownership

- **Status:** Accepted (M0)
- **Context:** Staff must safely take over without racing AI responses and later return work.
- **Decision:** Model conversation ownership as `ai|staff`. Ticket creation interrupts LangGraph; staff takeover silences AI. Resolve terminates the episode; explicit return-to-AI writes a note, invalidates stale actions, refreshes facts and resumes at routing.
- **Consequences:** Customers see predictable ownership and every transition is auditable. The console must handle queues, presence and concurrent transitions.
