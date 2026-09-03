# Milestones

Status vocabulary: `COMPLETE`, `PLANNED`, `BLOCKED`. A milestone is complete only when its gate is evidenced in the repository and CI where applicable.

| ID | Status | Deliverable | Completion gate |
|---|---|---|---|
| M0 | COMPLETE | Product specification and architecture | All required documents exist; scenario traceability, safety boundaries, provider contracts, handoff, non-goals, and M1 inputs agree; documentation-only repository. |
| M1 | COMPLETE | Repository and local platform skeleton | Next.js/FastAPI/ARQ worker boot; configuration validates; pgvector-only migration applies; all five Compose services become healthy and retain migration state across restart; backend lint/type/tests (16 tests, 93.9% coverage) and frontend lint/type/tests/build (4 tests, 100% line coverage) pass; no feature behavior claimed. |
| M2 | COMPLETE | Core schema, local identity and tenancy | M2 migration covers approved entities with composite tenant FKs, RLS policies and an immutable audit trigger; Argon2 credentials, hashed revocable sessions, Support/Admin membership checks, scoped conversation repositories, two-tenant seeds and 25 backend tests (90.31% coverage) pass, including authorized/denied integration cases. |
| M3 | COMPLETE | Realistic mock commerce API | Independently persisted FastAPI service passes 13 tests at 95.95% coverage across its typed contract, isolation, seeded scenarios, pagination, failure injection, optimistic concurrency, restart persistence and idempotency; mock CRM and webhook generation remain later work by explicit M3 scope. |
| M4 | COMPLETE | Knowledge ingestion and retrieval | Semantic hardening adds 1,536-dimensional fake/OpenAI provider parity, fingerprinted reindexing and deterministic/cross-encoder reranker selection. Real OpenAI reingestion and cross-encoder evaluation pass the 29-case gate at Recall@5 0.909091, MRR 0.909091 and zero unsupported/isolation false positives; provider failures do not fall back. |
| M5 | COMPLETE | Commerce and CRM provider integration layer | Versioned async ports, normalized models, mock-commerce/mock-CRM HTTP adapters, credential-gated Shopify/HubSpot adapters, and contract/error/isolation/idempotency tests pass without live vendor credentials. Durable conversation streaming remains future work. |
| M6 | COMPLETE | Durable LangGraph orchestration core | Strict multi-intent Responses triage, deterministic risk/tool enforcement, tenant-owned PostgreSQL checkpoints, mock-provider reads, sanitized replayable SSE, idempotency/concurrency controls, restart persistence, and safety/coverage gates pass. |
| M7 | COMPLETE | Grounded FAQ and live order support | Validated M4 citation receipts, bilingual Responses generation, tenant/customer-scoped public order resolution, combined read-only routing, and fail-closed grounding tests pass. |
| M8 | COMPLETE | Confirmed shipping-address changes | Strict EN/FR collection, encrypted tenant-scoped pending actions, durable confirmation interrupt, version revalidation, mock-provider idempotency/reconciliation, immutable audit, PII sink checks, restart persistence, and real OpenAI demonstrations pass. |
| M9 | PLANNED | CRM lead capture | Explicit consent evidence gates normalized CRM upsert; revocation/no-consent tests pass. |
| M10 | PLANNED | Human escalation and staff console | Ticket summary, interrupt, RBAC takeover/reply/resolve/return-to-AI and audit flows pass. |
| M11 | PLANNED | Webhooks and background jobs | Signature verification, durable inbox, deduplication, retries/DLQ and order projection updates pass. |
| M12 | PLANNED | Shopify live validation | Credential-gated development-store validation of the M5 adapter passes with synthetic reads/address updates; no real refunds. |
| M13 | PLANNED | HubSpot live validation | Credential-gated developer-test-account validation of the M5 adapter passes with synthetic contacts, notes and cleanup. |
| M14 | PLANNED | Bilingual UX and accessibility | EN/FR scenario suite, locale fallback disclosure, WCAG-oriented keyboard/screen-reader review pass. |
| M15 | PLANNED | Security hardening | Threat-model tests, secret scanning, dependency scanning, rate limits and authorization review pass. |
| M16 | PLANNED | Evaluation and observability | Quality/safety regression suite, traces, metrics, dashboards and alert runbooks meet thresholds. |
| M17 | PLANNED | Public portfolio release | Mock-only deployment, synthetic-data verification, demo scripts, architecture narrative and rollback/runbook approved. |

Milestones are intentionally sequential gates, but implementation work may be parallelized after dependencies are met. M0 completion does not imply production readiness.
