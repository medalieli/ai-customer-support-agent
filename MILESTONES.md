# Milestones

Status vocabulary: `COMPLETE`, `PLANNED`, `BLOCKED`. A milestone is complete only when its gate is evidenced in the repository and CI where applicable.

| ID | Status | Deliverable | Completion gate |
|---|---|---|---|
| M0 | COMPLETE | Product specification and architecture | All required documents exist; scenario traceability, safety boundaries, provider contracts, handoff, non-goals, and M1 inputs agree; documentation-only repository. |
| M1 | COMPLETE | Repository and local platform skeleton | Next.js/FastAPI/ARQ worker boot; configuration validates; pgvector-only migration applies; all five Compose services become healthy and retain migration state across restart; backend lint/type/tests (16 tests, 93.9% coverage) and frontend lint/type/tests/build (4 tests, 100% line coverage) pass; no feature behavior claimed. |
| M2 | COMPLETE | Core schema, local identity and tenancy | M2 migration covers approved entities with composite tenant FKs, RLS policies and an immutable audit trigger; Argon2 credentials, hashed revocable sessions, Support/Admin membership checks, scoped conversation repositories, two-tenant seeds and 25 backend tests (90.31% coverage) pass, including authorized/denied integration cases. |
| M3 | PLANNED | Synthetic mock providers | Commerce/CRM contract suites pass with seeded fixtures, failure injection, idempotency and webhook generation. |
| M4 | PLANNED | Knowledge ingestion and retrieval | Versioned EN/FR corpus ingestion, hybrid retrieval, local reranking and citation validation meet offline retrieval gates. |
| M5 | PLANNED | Durable conversation API | Authenticated threads/messages, streaming, PostgreSQL checkpoints and resume work across restarts. |
| M6 | PLANNED | Agent read workflows | Multi-intent routing and all read tools pass ownership, grounding, timeout and tool-budget tests. |
| M7 | PLANNED | Guarded address changes | Preview, expiring confirmation token, execution, replay defense and audit tests pass. |
| M8 | PLANNED | Refund request workflow | Versioned deterministic eligibility outputs all three states; real-provider human-approval rule is enforced. |
| M9 | PLANNED | CRM lead capture | Explicit consent evidence gates normalized CRM upsert; revocation/no-consent tests pass. |
| M10 | PLANNED | Human escalation and staff console | Ticket summary, interrupt, RBAC takeover/reply/resolve/return-to-AI and audit flows pass. |
| M11 | PLANNED | Webhooks and background jobs | Signature verification, durable inbox, deduplication, retries/DLQ and order projection updates pass. |
| M12 | PLANNED | Shopify adapter | Credential-gated development-store contract and synthetic read/write-preview tests pass; no real refunds. |
| M13 | PLANNED | HubSpot adapter | Credential-gated developer-test-account contract tests pass with synthetic contacts and cleanup. |
| M14 | PLANNED | Bilingual UX and accessibility | EN/FR scenario suite, locale fallback disclosure, WCAG-oriented keyboard/screen-reader review pass. |
| M15 | PLANNED | Security hardening | Threat-model tests, secret scanning, dependency scanning, rate limits and authorization review pass. |
| M16 | PLANNED | Evaluation and observability | Quality/safety regression suite, traces, metrics, dashboards and alert runbooks meet thresholds. |
| M17 | PLANNED | Public portfolio release | Mock-only deployment, synthetic-data verification, demo scripts, architecture narrative and rollback/runbook approved. |

Milestones are intentionally sequential gates, but implementation work may be parallelized after dependencies are met. M0 completion does not imply production readiness.
