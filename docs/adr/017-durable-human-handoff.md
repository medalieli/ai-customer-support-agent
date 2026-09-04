# ADR 017: Durable human escalation and staff ownership

- **Status:** Accepted (M11)
- **Decision:** Deterministic safety triggers can force escalation independently of model output.
  PostgreSQL is authoritative for conversation ownership and the tenant-scoped support-ticket
  lifecycle; the configured mock CRM receives an idempotent projection through `CrmProviderV1`.
- **Summary boundary:** OpenAI may produce only the strict customer-visible summary draft. Stored
  messages, sanitized tool outcomes, validated citation receipts, and pending-action records are
  the allowlisted evidence. Invalid or unavailable model output becomes a marked deterministic
  fallback. Prompts, reasoning, secrets, payment data, full addresses, and unnecessary identity
  data are excluded.
- **Concurrency:** A conversation row lock plus a partial unique active-ticket index prevents
  duplicate escalation. Ticket versions and row locks serialize claims and transitions. Provider
  writes use stable idempotency keys, optimistic versions, and read-after-timeout reconciliation.
- **Ownership:** `ai_active -> handoff_pending -> staff_active -> resolved`, with an explicit
  `staff_active -> ai_active` return path. AI answers and tools are blocked while handoff is pending
  or staff is active; new customer messages continue to attach to the conversation and ticket.
- **Access:** Only active Support/Admin members may use staff ticket APIs. Every query is scoped by
  the authenticated organization. Customer-visible replies and private notes use separate message
  visibility, and transitions produce sanitized public events and immutable audit records.
- **Deferred:** Email/Slack notifications, webhooks, live HubSpot tickets, automated outreach, and
  the frontend staff inbox.
