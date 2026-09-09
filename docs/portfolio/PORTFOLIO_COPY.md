# Portfolio copy

## One-sentence pitch

A full-stack AI support agent that grounds policy answers in cited knowledge, executes commerce and CRM tools behind explicit confirmations, and hands uncertain work to an audited staff queue.

## Short description

NovaCart demonstrates production-minded AI support with OpenAI and without commerce/CRM vendor accounts: real LangGraph orchestration, PostgreSQL/pgvector RAG, Redis-backed work, FastAPI/SSE, a polished customer/staff UI, and persistent mock commerce/CRM services. Consequential actions are checked, confirmed, idempotent, tenant-scoped, and observable.

## Longer GitHub / Upwork description

NovaCart is an end-to-end reference implementation for safe AI customer support. Customers receive cited policy answers, track orders, request refunds, update addresses, opt into sales contact, or escalate to a person. LangGraph routes turns and persists checkpoints; PostgreSQL/pgvector powers tenant state and retrieval; Redis supports bounded execution; FastAPI streams progress to Next.js. Writes pause for approval and use idempotency. Staff can claim tickets, publicly reply, privately note, resolve, close, return to AI, inspect analytics, and review audit history.

Customer-facing AI responses require OpenAI; commerce and CRM use fictional persistent REST providers. Shopify/HubSpot adapters are contract-tested and configurable. The repository includes deterministic evaluations, Playwright journeys, coverage gates, observability, a hardened production-like stack, backup/restore, supply-chain checks, and reproducible local load tests. It does not claim live vendor verification, cloud deployment, production traffic, or universal performance.

## Five feature bullets

- Tenant-scoped RAG answers with displayed, validated citations.
- Live order tools plus policy-aware address and refund workflows.
- Consent/confirmation gates with expiring tokens and idempotent writes.
- Human escalation with claim, reply, private note, resolution, audit, and AI resume.
- Repeatable Docker demo with test, evaluation, security, observability, backup, and load evidence.

## Technology

Python, FastAPI, LangGraph, SQLAlchemy, Alembic, PostgreSQL, pgvector, Redis, ARQ, Next.js, React, TypeScript, SSE, Docker Compose, Nginx, Prometheus, Grafana, OpenTelemetry, Playwright, Vitest, Pytest, Ruff, MyPy, Bandit, Trivy, GitHub Actions.

## CV-ready achievements

- Engineered a multi-tenant LangGraph workflow combining cited RAG, provider tools, persisted checkpoints, confirmation gates, idempotency, and staffed escalation.
- Built contract-tested provider adapters and persistent mock REST services so full customer journeys run without Shopify or HubSpot accounts.
- Established evidence across coverage gates, restricted-role RLS, TLS/security tests, observability, backup/restore, scanning, and a 102-workflow local load baseline.

## Five interview talking points

1. Why cited RAG owns policy while mutable orders stay behind provider ports.
2. How checkpointed confirmations and idempotency prevent duplicate writes.
3. How tenant context reaches RLS, bindings, knowledge, tickets, and analytics.
4. How safe handoff handles low confidence, missing resources, outages, and explicit requests.
5. How deterministic adapters/evaluations improve reproducibility without overstating vendor or production validation.
