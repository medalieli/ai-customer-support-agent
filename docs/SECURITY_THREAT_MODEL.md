# Security Threat Model

## Scope and trust model

Protected assets are customer/order data, tenant separation, provider/LLM credentials, consent, action authority, policy integrity, conversations, staff functions and audit evidence. Browsers, user/document/provider text, LLM output, webhooks and public networks are untrusted. FastAPI's authenticated authorization layer, tool gateway, deterministic rule services and persistence constraints form the enforcement boundary. The LLM is never a policy decision point.

## Threats and required mitigations

| Threat | Controls | Verification |
|---|---|---|
| Cross-customer order access / IDOR | Session-derived customer ID; tool ignores supplied identity; provider query scoped to verified customer; indistinguishable not-found/forbidden response; audit and rate-limit enumeration. | Attempt another synthetic customer's refs across every read/write tool; assert no data/existence leak. |
| Cross-tenant access | Tenant from trusted host/session mapping; tenant key on rows/cache/checkpoints/queues; composite FKs, scoped repositories and RLS defense; never accept tenant from model. | Matrix tests across customer/staff/provider/webhook/knowledge paths. |
| Prompt injection in messages/documents/provider text | Treat content as quoted data; fixed system policy; approved-source ingestion; instruction-pattern flags; strict tool schemas; least-privilege tool gateway; no secrets/tools in retrieval layer; output/citation validation. | Direct/indirect multilingual injection corpus cannot alter identity, tools, provider or policy result. |
| Unauthorized tool execution | Server-side allowlist by actor/role/risk; fresh authentication; ownership, capability and domain-rule checks; guarded writes unavailable to raw model/API callers. | Negative permission and confused-deputy tests; every denial audited. |
| Confirmation-token replay/substitution | High-entropy signed opaque token; store hash; bind tenant/actor/session/conversation/action hash/version/expiry; atomic single use; invalidate on change/logout/takeover; rate limit. | Replay, cross-session, modified preview, expired and concurrent-consume tests. |
| Duplicate/out-of-order webhooks | Durable inbox unique key/payload hash; idempotent workers; resource version/occurred-time checks; monotonic projection updates. | Reorder and replay fixtures; exactly one logical effect. |
| Forged webhook signatures | Verify raw-body HMAC/provider signature, constant-time compare, timestamp tolerance/replay cache, per-provider endpoint secret; reject before parse/queue. | Official fixture plus tampered body/signature/time tests. |
| Provider credential leakage | Secret manager/env injection; least scopes and separate test credentials; rotation; egress allowlist; never browser/model/DB/log; secret scanning and redaction. | Repository/history scan, telemetry canaries, rotation drill. |
| Sensitive data in logs/traces | Structured allowlist; pseudonymous IDs; field-level redaction; no raw prompts, tokens, address/email/provider payload; restricted access/retention. | Automated sink tests with seeded canary PII/secrets. |
| Hallucinated policy/order data | Approved versioned hybrid retrieval, local rerank, claim-citation validation; live normalized provider facts with provenance/freshness; refuse uncertainty; deterministic refund rules. | Unsupported-claim, stale-fact, citation corruption and provider-outage evals. |
| CRM record without consent | Purpose-specific explicit consent evidence required by gateway and DB relation; minimum fields; no implied consent; staff source/reason; idempotent upsert. | “Interested”/ambiguous/no/revoked consent tests create zero contacts. |

Additional risks include staff privilege abuse (RBAC, reason capture, review/audit), CSRF/session theft (secure HttpOnly SameSite cookies, CSRF token, short sessions/MFA for staff), XSS (escaped Markdown/URL allowlist/CSP), SSRF (adapter egress allowlist and no user URLs), denial of service/cost abuse (rate/token/tool budgets, queues/circuit breakers), supply-chain risk (lockfiles/scanning/SBOM), and audit tampering (append-only DB role, hash chaining and export).

## M2 controls implemented

M2 derives organization/customer identity only from a high-entropy cookie whose full value is SHA-256 hashed in PostgreSQL. Customer repositories require organization and customer IDs from the authenticated principal; staff access requires an active tenant membership. Composite foreign keys prevent tenant-mismatched conversations, messages, assignments and knowledge versions. Conversation authorization returns a uniform 404 and records a content-free denial event. Audit metadata is allowlisted and a database trigger rejects update/delete. Demo personas are allowlisted synthetic identities, require an explicit flag and password, and production settings reject demo mode or non-Secure cookies.

M2 does not yet add CSRF tokens, login rate limits, MFA/OIDC, encrypted message bodies, key rotation or a separate non-owner database role. The last item means local Compose's database owner can bypass RLS; service/repository scoping is mandatory there, while deployments must use a `NOSUPERUSER NOBYPASSRLS` runtime role. These hardening items remain M15 gates.

## Data and model privacy

Send the model only the minimum conversation window and redacted tool facts needed for the current intent. Do not request/store chain-of-thought. Store structured decision reason codes and customer-visible explanations instead. Vendor data-processing and retention settings require review before Integration Mode beyond test data. Synthetic fixtures must be visibly tagged and contain no copied real identities.

## Incident-safe behavior

Authorization or signature failures fail closed and emit safe security metrics. Suspected credential exposure disables the adapter, rotates the credential, preserves audit evidence and places affected actions into reconciliation. Unknown write outcomes never report success; they are reconciled by idempotency key/provider lookup or escalated.

## Residual risks and decisions pending M15

Exact identity provider, RLS coverage, secret manager, regional hosting, DPA settings, retention/legal basis and webhook raw-payload retention need deployment-specific decisions. These do not change the invariant that trusted server context—not prompts—controls tenant, actor and authority.
