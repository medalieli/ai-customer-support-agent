# NovaCart AI Customer Support Agent

NovaCart is a standalone portfolio blueprint for a production-style bilingual customer-support platform for a fictional electronics retailer. It combines grounded knowledge answers, authenticated live order access, guarded actions, deterministic refund decisions, CRM lead capture, durable conversations, and human takeover.

**Current status:** Milestone M0 (specification and architecture) complete; M1 has not started. This repository currently contains documentation only—no application code, migrations, dependencies, or real customer data.

## Provider modes

Commerce and CRM are selected independently:

```text
COMMERCE_PROVIDER=mock|shopify
CRM_PROVIDER=mock|hubspot
```

- **Local Demo Mode:** `mock` + `mock`; realistic deterministic services and synthetic NovaCart data. This is the public portfolio deployment.
- **Integration Mode:** either provider may be switched independently. Shopify uses a development store and HubSpot a developer test account, populated only with synthetic records. Credential-gated contract tests prove real adapters.
- Every adapter implements the same versioned interface and returns normalized domain models. Agent graphs and business rules never branch on vendor-specific payloads.

## Architecture at a glance

Next.js supplies customer chat and the role-protected staff console. FastAPI owns authorization, domain rules, LangGraph execution, tools, webhooks, and provider adapters. PostgreSQL stores business records, immutable audit events, and durable graph checkpoints; pgvector plus PostgreSQL full-text search powers hybrid retrieval. Redis backs asynchronous jobs. OpenAI Responses API calls use strict structured schemas; local reranking and citation validation gate grounded answers.

The LLM may propose a tool call. Application code always enforces tenant boundaries, identity, customer ownership, consent, confirmation, idempotency, and deterministic policy rules. Private chain-of-thought is never requested or stored.

## Documentation

- [Product requirements](docs/PRODUCT_REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Agent workflows and tools](docs/AGENT_WORKFLOWS_AND_TOOLS.md)
- [Data model](docs/DATA_MODEL.md)
- [Security threat model](docs/SECURITY_THREAT_MODEL.md)
- [Evaluation plan](docs/EVALUATION_PLAN.md)
- [Demo scenarios](docs/DEMO_SCENARIOS.md)
- [Milestones](MILESTONES.md)
- [Architecture decisions](docs/adr/)

## Scope and non-goals

M0 defines contracts, behavior, safety boundaries, evaluation gates, and implementation inputs. It does not implement UI, APIs, graph nodes, providers, infrastructure, migrations, ingestion, or tests. The initial product creates refund **requests**; it never autonomously moves real money. It is not a general-purpose assistant, an inventory/fulfillment system of record, a replacement for staff, or a repository for production personal data.

## Recorded assumptions

- NovaCart is initially one organization, but every tenant-owned row and request carries `organization_id` so isolation is testable.
- Customers authenticate through a NovaCart session; order number or email alone is never proof of identity.
- English (`en`) and French (`fr`) are launch languages. Replies follow the latest clear customer language; citations point to the matching approved document version or explicitly disclosed fallback translation.
- Mock providers simulate latency, failures, lifecycle transitions, webhooks, and idempotency using seeded synthetic fixtures.
- Provider order data is authoritative. The local database holds identifiers, projections, event history, and short-lived caches, never silently overriding the provider.
- Exact policy thresholds are versioned configuration owned by NovaCart operations, not prompts. Initial synthetic fixtures use the rules defined in the workflow document.

## Next milestone

M1 may begin only after M0 approval. Its inputs are the normalized contracts, state schema, entity model, security invariants, scenario IDs, and acceptance gates in these documents.
