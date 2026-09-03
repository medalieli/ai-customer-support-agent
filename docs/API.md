# M2 HTTP API

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
