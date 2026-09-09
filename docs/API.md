# M2 HTTP API

## M14 operational APIs

`GET /metrics` exposes content-free Prometheus metrics with fixed, bounded label sets. It never
uses tenant, actor, conversation, order, or message values as labels. `GET /api/v1/staff/analytics`
accepts `range=24h|7d|30d|90d` and returns tenant-scoped operational aggregates to Support and
Admin staff. Customers are denied, and the response contains no raw conversation or customer data.

## M11 human handoff API

Escalation creates at most one active tenant-scoped ticket per conversation and changes ownership
from `ai_active` to `handoff_pending`. A successful staff claim changes it to `staff_active`; resolve
changes it to `resolved`; close uses a separate `closed` status. Return-to-AI leaves the ticket
resolved and restores `ai_active` on an open conversation. It accepts in-progress or resolved
tickets owned by the acting staff member, not closed tickets. During
handoff or staff ownership, customer messages remain durable but the agent does not answer or run
tools.

Support and Admin staff can list and retrieve their tenant queue at `GET /staff/tickets` and
`GET /staff/tickets/{ticket_id}`. `POST /staff/tickets/{ticket_id}/{action}` accepts `claim`,
`reply`, `note`, `resolve`, `close`, or `return_to_ai`, plus the current ticket `version` and an
`Idempotency-Key`. Replies are customer-visible; notes are private. Customers and cross-tenant
staff receive no queue or ticket access. Stale versions and invalid ownership transitions return
safe conflicts.

`GET /staff/tickets/{ticket_id}/audit` and `GET /staff/conversations/{conversation_id}/audit`
return cursor-paginated, sanitized operational events to Support/Admin staff in that tenant.
Payload bodies, private notes, addresses, credentials, provider payloads, and model reasoning are
excluded from this projection.

Ticket summaries contain only validated conversation/tool evidence, citation receipt IDs,
pending/failed action references, the deterministic reason/priority, and timestamp. OpenAI creates
the strict draft; validation failure uses an explicitly marked deterministic summary so escalation
still succeeds. Mock CRM is the only local write target; HubSpot coverage uses mocked HTTP only.

## M10 consented CRM lead workflow

Authenticated customer messages sent to `POST /api/v1/agent/threads/{conversation_id}/messages`
may return `confirmation_required` with an exact `fields_to_store` preview, purpose, opaque action
ID/hash, single-use confirmation token, and expiry. Sales inquiry content is redacted from durable
messages and graph state. `POST /api/v1/agent/threads/{conversation_id}/resume` requires the current
checkpoint version, action ID, token, and an explicit English or French approve/deny decision.

Approval returns normalized contact, lead, and note references plus `created|updated` operation
status. Denial returns no CRM references. Tenant/customer/session/thread binding, encrypted payload,
optimistic lead versioning, stable provider idempotency keys, and timeout reconciliation apply.
Local execution is restricted to `CRM_PROVIDER=mock`; HubSpot has mocked contract coverage only.

All M2 identity and conversation routes are under `/api/v1`. Errors use
`{"error":{"code":"...","message":"..."}}`; authentication failures are `401`, explicit
role failures are `403`, and inaccessible conversations are the same safe `404` whether absent or
owned by another customer or tenant.

| Method | Path | Access | Result |
|---|---|---|---|
| GET | `/auth/demo-personas` | Public, demo mode only | Synthetic allowlisted personas; disabled mode returns 401. |
| POST | `/auth/demo-login` | Public, demo mode only | Customer session cookie for organization slug + persona key. |
| POST | `/auth/staff-login` | Public | Staff session cookie after password and active-membership checks. |
| GET | `/auth/me` | Authenticated | Session-derived kind, organization, subject and staff role. |
| POST | `/auth/logout` | Authenticated | Revokes the hashed server session and clears the cookie. |
| GET | `/auth/admin-check` | Admin | Minimal RBAC verification endpoint. |
| POST | `/conversations` | Customer | Creates a customer-owned conversation; supplied identity fields are ignored. |
| GET | `/conversations` | Customer | Lists only the authenticated customer's conversations (`limit`, `offset`). |
| GET | `/conversations/{id}` | Owner or organization staff | Returns one authorized conversation. |
| POST | `/conversations/{id}/messages` | Owner or organization staff | Persists a customer/staff message without AI generation. |
| GET | `/conversations/{id}/messages` | Owner or organization staff | Stable ascending sequence pagination (`after_sequence`, `limit`). |

Cookies are HttpOnly, path `/`, expire with the server session, and have configurable `Secure` and
`SameSite=lax|strict` attributes. Browser payload IDs never establish identity or tenant scope.

## M3 mock commerce contract

The independently deployed service listens at `http://localhost:8080` locally and publishes OpenAPI
at `/docs` and `/openapi.json`. Health routes require no authentication. Every `/v1` request requires
`X-Internal-API-Key`, trusted `X-Organization-Id`, and opaque `X-External-Customer-Id` headers. The
future adapter—not a browser or model—supplies these values. An inaccessible or unknown order always
returns the same 404. Lists use `cursor` and `limit` (1–50), returning `items` and `next_cursor`.

| Method | Path | Result |
|---|---|---|
| GET | `/v1/orders` | Scoped order summaries with cursor pagination. |
| GET | `/v1/orders/{order_ref}` | Full order, lines, totals, delivery, address, tracking and refund requests. |
| GET | `/v1/orders/{order_ref}/fulfillment` | Fulfillment/delivery state and order version. |
| GET | `/v1/orders/{order_ref}/tracking` | Carrier, URL, estimate and tracking history. |
| GET | `/v1/orders/{order_ref}/shipping-address` | Current shipping address. |
| GET | `/v1/orders/{order_ref}/returns` | Existing return/refund-request statuses. |
| PATCH | `/v1/orders/{order_ref}/shipping-address` | Changes an open, unfulfilled order address. |
| POST | `/v1/orders/{order_ref}/refund-requests` | Records a request without deciding eligibility or moving money. |

Writes require `Idempotency-Key` and integer `If-Match`. The SHA-256 fingerprint covers operation,
version and canonical body. Exact replay returns the original result; changed input under the same
key returns `409 idempotency_conflict`; stale versions return `409 version_conflict`. Address changes
reject non-open or non-unfulfilled orders. Refund requests reject cancelled/already-refunded orders
and invalid amounts but deliberately do not evaluate return policy.

Errors use `{"error":{"code":"...","message":"...","retryable":false}}`. Codes include
`unauthenticated`, `forbidden`, `not_found`, `validation`, `idempotency_conflict`,
`version_conflict`, `order_state_conflict`, `rate_limited`, `timeout`, and `unavailable`; rate limits
include `Retry-After`.

In development/test only, authenticated calls may send `X-Mock-Failure` with `timeout`,
`rate_limit`, `temporary`, `not_found`, `invalid`, or `version_conflict`. Production configuration
rejects enabling simulation.

Deterministic fixtures link `seed-amira-en` to changeable, shipped, delayed, recently delivered and
out-of-window delivered orders; `seed-lucas-fr` to final-sale, partially fulfilled, cancelled and
already-refunded orders; and isolation persona `seed-nora-en` in the second organization to a
same-looking `NC-1001`. Products, lines, USD totals, addresses, fulfillment events and tracking are
synthetic. Re-running initialization never overwrites mutations in the persistent commerce volume.

## M4 knowledge API

All routes are under `/api/v1/knowledge` and use the M2 HttpOnly session. Document lifecycle routes require an active Admin membership; Support staff receive 403. Search and citation validation require any authenticated identity and always derive the organization from that session.

| Method | Path | Access | Result |
|---|---|---|---|
| POST | `/documents` | Admin | Multipart file and metadata; creates or replaces with a queued immutable version. |
| GET | `/documents` | Admin | Tenant-scoped documents and all preserved versions. |
| GET | `/documents/{document_id}/versions/{version_id}` | Admin | Safe ingestion state and error code. |
| DELETE | `/documents/{document_id}` | Admin | Soft-deletes the document; historical versions/citations remain. |
| POST | `/documents/{document_id}/versions/{version_id}/retry` | Admin | Requeues a failed version; ready versions are idempotent. |
| GET | `/search` | Authenticated | Structured passages for `q`, optional filters, and bounded `top_k`. |
| POST | `/citations/validate` | Authenticated | Validates an exact previously issued citation receipt. |

Uploads accept `.md`, `.txt`, and `.pdf`, at most 2 MiB. Duplicate `(document, language, checksum)` uploads return the existing version. Search results contain lexical, vector, fusion and rerank scores plus a citation receipt with document/version/chunk IDs, title, language, section/page and exact snippet. Validation succeeds only when every supplied field matches a durable tenant-owned receipt and current stored chunk checksum.

Search is eligible only after the active version has a matching 1,536-dimensional indexing fingerprint. A provider/model/chunk-configuration change leaves the version pending until ARQ reingests it. Real embedding or reranker failures return safe explicit codes such as `embedding_rate_limited`, `embedding_timeout`, `embedding_unavailable`, or `reranker_unavailable`; the API never substitutes a test provider.

## M5 outbound provider contracts

M5 adds no public main-API routes. `CommerceProviderV1` normalizes order lookup, tracking, guarded address mutation and refund-request creation. `CrmProviderV1` normalizes contact lookup/upsert and conversation-note creation. Every method accepts a trusted `ProviderContext`; browser/body identity never establishes tenant scope.

Mock CRM listens on `http://localhost:8090`, with `/health/live`, `/health/ready`, and `/docs`. Its `/v1` contacts and notes require `X-Internal-API-Key`, `X-Organization-Id`, and `Idempotency-Key` for writes. Authenticated `X-Mock-Failure` simulation is development/test-only. Shared adapter errors are `not_found`, `not_authorized`, `conflict`, `validation`, `unsupported`, `rate_limited`, `timeout`, and `unavailable`.

## M6 agent API

Customer sessions submit an idempotent agent turn with `POST /api/v1/agent/threads/{conversation_id}/messages` and a required `Idempotency-Key` header. Conversation and tenant/customer ownership always come from the authenticated session. Identical replay returns the original run; changed content under the same key, or a second active run, returns `409`.

`GET /api/v1/agent/runs/{run_id}/events` is an authenticated SSE replay endpoint. `Last-Event-ID` or `after` resumes after a durable sequence number. Events are `triage_completed`, `tool_started`, `tool_completed`, `confirmation_required`, `response_completed`, and `escalation_required`. Payloads contain only labels, safe status/reason codes, minimized tool data, visible responses, and citation receipts—never prompts, credentials, reasoning, identity, or raw provider payloads.

`POST /api/v1/agent/threads/{conversation_id}/resume` supplies a generic resume value, required idempotency key, and expected checkpoint version. It reauthenticates ownership and rejects stale or non-interrupted threads. For an M8 address proposal, the submission response also returns a `confirmation` object with the masked current address, normalized proposed address, consequences, expiry, opaque action ID/hash, and confirmation credential. Address values and the credential never enter SSE. Resume supplies `action_id`, `confirmation_token`, and an explicit EN/FR `decision`; the server revalidates the binding and live order version before the mock-commerce write. Exact resume-key replay returns the original result; new-key replay, tampering, expiry, stale versions, and cross-tenant access fail closed.

M8 adds only confirmed mock-commerce shipping-address writes. Refund decisions, CRM writes, ticket creation, human ownership, webhooks, live Shopify writes, and frontend chat remain deferred.

## M12 provider webhook API

Provider ingress is public but authenticated by the provider signature and a high-entropy connection
key. `POST /api/v1/webhooks/{provider}/{endpoint_key}` accepts Shopify, HubSpot, mock-commerce, and
mock-CRM topics. It requires JSON, enforces the configured body and replay limits, verifies the exact
raw bytes, persists before returning `202`, and never invokes the agent or a provider in-request.

Support and Admin staff may inspect minimized metadata at `GET /api/v1/staff/webhooks` and retry a
failed/dead-letter event with `POST /api/v1/staff/webhooks/{event_id}/retry`. Responses exclude raw
payloads, secrets, signatures, addresses, payment data, and webhook text. Both routes derive tenant
scope from the staff session. Mock providers expose authenticated `POST /v1/webhooks/emit` controls
for signed delivery, duplicates, delay, and invalid-signature simulation.
