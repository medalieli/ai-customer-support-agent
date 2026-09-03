# ADR-014: Confirmed shipping-address actions

- **Status:** Accepted (M8)
- **Context:** Shipping addresses are sensitive and a mistaken or replayed update can redirect an
  order. M6 checkpoints and SSE are durable but are not appropriate stores for pending address
  payloads or confirmation secrets.
- **Decision:** The LangGraph route classifies the intent, deterministically parses a labelled or
  JSON address, resolves the authenticated customer's public order number, checks the live order
  is `open` and `unfulfilled`, creates an encrypted RLS-scoped pending action, and interrupts. Graph
  state retains only the action ID and HMAC-bound action hash. The authenticated resume API locks
  and atomically consumes the action, validates its tenant/customer/session/thread/run/order/payload
  and version bindings plus expiry, re-reads eligibility, and invokes only the M5 mock-commerce
  adapter with `If-Match` and a stable idempotency key. A post-submit timeout is reconciled by a live
  read before any result is reported. Full addresses never enter checkpoints, SSE, logs, or audit
  metadata.
- **Consequences:** A changed order or changed payload needs a new preview and confirmation. The
  service does not claim postal/carrier deliverability. Shopify, refunds, CRM, tickets, handoff,
  webhooks, and frontend work remain outside M8.
