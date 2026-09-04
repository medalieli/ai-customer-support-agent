# ADR-016: Consented sales-lead synchronization

- **Status:** Accepted (M10)
- **Context:** Genuine business inquiries may be recorded in CRM only after a customer sees the
  exact minimum data and separately consents. Conversation text, email, and business details are
  inappropriate for checkpoints, streams, logs, and audit metadata.
- **Decision:** OpenAI strict structured output extracts only user-stated lead fields. Deterministic
  validation combines those fields with authenticated server-owned name/email, rejects non-sales
  support and missing/invalid values, and creates an encrypted pending action. Confirmation binds
  tenant, customer, session, conversation, run, payload hash, and expiry. The M5 CRM V1 port then
  idempotently finds/upserts one contact, one optimistic-concurrency lead, and one concise note.
  Local execution is mock-only; HubSpot remains credential-gated and contract-tested with mocked
  HTTP.
- **Consequences:** Missing or uncertain values never erase trusted CRM data. Replays and retries
  have one logical effect, sanitized immutable audit events retain no lead content, and no outreach
  is initiated. Human handoff, staff UI, webhooks, and live HubSpot validation remain deferred.
