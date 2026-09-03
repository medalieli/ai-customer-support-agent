# ADR-015: Policy-grounded refund requests

- **Status:** Accepted (M9)
- **Context:** Refund eligibility combines versioned policy, live order state, item and monetary
  facts. Model output cannot safely authorize a financial workflow, and a request must never be
  represented as money movement.
- **Decision:** M9 uses M4 retrieval only to obtain and validate customer-facing evidence. A pure
  `refund-v1` engine, bound to allowlisted active policy checksums, returns `eligible`,
  `ineligible`, or `manual_review_required`. Money uses `Decimal`. Eligible mock-commerce cases
  create an encrypted, expiring pending action bound to tenant, customer, session, conversation,
  order version, policy version/checksum, ruleset, item, quantity, amount, currency, reason and
  payload hash. Confirmation locks and consumes the action, reloads policy and order state, then
  submits one idempotent `create_refund_request`. Timeouts are reconciled by live read. Shopify
  cases stop at `human_approval_required`. Manual review is persisted without creating a ticket.
- **Consequences:** Policy mismatch, stale state, replay, tampering, duplicate refunds and
  concurrency fail closed. Audit events contain IDs, outcomes and reason codes, not customer
  reasons or financial details. The system says “refund request submitted,” never “money
  refunded.” Actual payment mutation, tickets, staff takeover and webhooks remain deferred.
