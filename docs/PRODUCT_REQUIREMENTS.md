# Product Requirements

## Product definition

NovaCart Support helps authenticated shoppers resolve order issues and helps anonymous or authenticated visitors answer product/policy questions. It reduces routine staff load without surrendering control of identity, money, customer records, or policy interpretation. Staff work from a single console with the evidence, tool history, and factual summary needed to take over.

### Users and goals

- **Customer/visitor:** receive fast, cited EN/FR answers and safely manage their own orders.
- **Support agent:** take over a complete thread, act with role-appropriate tools, reply, resolve, or return control to AI.
- **Support manager:** review escalations, refunds, quality, and audit history.
- **Sales/CRM operator:** receive only consented, deduplicated leads.
- **System administrator/auditor:** configure providers/policies and investigate immutable events without viewing secrets.

## Functional requirements

| ID | Requirement | Success condition |
|---|---|---|
| FR-01 | Cited policy/product Q&A | Answer is supported by approved retrieved chunks; citation validator rejects missing/mismatched citations. |
| FR-02 | Authenticated order/tracking/return reads | Every read is scoped to organization and session customer; unavailable fields are stated, never invented. |
| FR-03 | Address change | Show normalized before/after preview and consequences; execute only after explicit, action-bound confirmation and revalidation. |
| FR-04 | Refund request | Retrieve policy for explanation, combine live order facts with deterministic eligibility, and create an idempotent request; real-provider cases require staff approval. |
| FR-05 | Sales lead | Detect interest, ask for explicit CRM consent, then idempotently upsert the minimum contact data. |
| FR-06 | Human escalation | Create ticket and evidence-linked factual summary; interrupt graph and notify queue. |
| FR-07 | Staff lifecycle | Authorized staff take over, reply, resolve, or explicitly return the conversation to AI; customer sees current ownership. |
| FR-08 | Mixed requests | Represent multiple intents, answer safe independent reads, clarify ambiguity, and serialize guarded writes. |
| FR-09 | Failures | Bound calls with timeouts/retries, preserve pending state, disclose uncertainty, and offer retry/escalation. |
| FR-10 | Order webhooks | Verify, durably deduplicate, asynchronously normalize, update projections, and notify/resume relevant workflows. |
| FR-11 | Bilingual | Understand and respond in English and French without weakening safety or citation requirements. |
| FR-12 | Adversarial input | Treat messages/documents/provider text as data; refuse instruction override and unauthorized access, logging a safe security event. |

## Operating modes and provider contract requirements

`COMMERCE_PROVIDER` and `CRM_PROVIDER` are independently selected at process start and health-reported without exposing credentials. Local Demo Mode uses realistic mock services and is the only public demo. Integration Mode uses synthetic data in isolated Shopify development and HubSpot developer-test accounts. A mixed selection such as Shopify + mock CRM is valid.

Adapters must implement versioned application-owned interfaces and normalized IDs, money, addresses, timestamps, statuses and errors. Required commerce capabilities are customer/order lookup, tracking, return status, address-change preview/execute, refund-request context, and normalized webhooks. Required CRM capability is consent-evidenced contact upsert. Capability discovery returns `supported`, `unsupported`, or `temporarily_unavailable`; workflows degrade safely rather than vendor-branching.

Contract tests run against mock on every CI run and against real adapters only when credentials are present. Real suites create tagged synthetic records, avoid real fulfillment/payment, and clean up where APIs permit.

## Non-functional requirements and initial targets

- Availability target: 99.5% monthly for the portfolio service, excluding providers; clear degraded states.
- Latency: p95 first streamed response under 2.5 s for non-tool chat; p95 read workflow under 8 s; guarded writes may pause for confirmation/human action.
- Durability: acknowledged messages, confirmations, tool outcomes, tickets and webhook inbox records survive process restart.
- Security: deny by default, tenant and ownership checks at application and persistence boundaries, secrets outside source control.
- Accessibility: responsive keyboard-usable UI, semantic announcements for streaming and ownership changes; WCAG 2.2 AA is the target.
- Privacy: data minimization and configurable retention; no chain-of-thought; redact sensitive structured fields from telemetry.
- Observability: correlation IDs link conversation turn, graph run, tool run, provider request and audit event.

## Out of scope for initial release

Voice/telephony, autonomous real-money refunds, arbitrary Shopify administration, marketing enrollment, payment-card handling, training/fine-tuning on conversations, more than EN/FR, multi-region active-active operation, and replacing provider systems of record.

## M0 assumptions and open product choices

The public experience uses only synthetic personas and orders. Authentication implementation, retention durations, staff SLA/queue rules, exact production policy thresholds, and jurisdiction-specific privacy wording remain decisions for later milestones; their enforcement points and configurable representation are fixed now. No later choice may weaken ownership, consent, confirmation, or real-refund human approval.
