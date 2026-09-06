# NovaCart AI support case study

## Business problem and roles

Support teams repeatedly answer policies, look up orders, and copy customer details between systems. Blind automation is risky: an unsupported answer harms trust, while an accidental address change, refund, or CRM contact has consequences. NovaCart demonstrates controlled automation for customers seeking self-service and staff taking ownership when automation should stop.

## System architecture

```mermaid
flowchart LR
  Customer[Customer UI] --> Edge[Next.js / Nginx]
  Staff[Staff UI] --> Edge
  Edge --> API[FastAPI + SSE]
  API --> Agent[LangGraph]
  Agent --> Knowledge[(PostgreSQL + pgvector)]
  Agent --> Redis[(Redis + ARQ)]
  Agent --> Commerce[Commerce port]
  Agent --> CRM[CRM port]
  Commerce --> MockShop[Mock commerce REST]
  CRM --> MockCRM[Mock CRM REST]
  Commerce -. credentials .-> Shopify[Shopify adapter]
  CRM -. credentials .-> HubSpot[HubSpot adapter]
  API --> Telemetry[Metrics, traces, redacted logs]
```

The UI never contacts providers directly. FastAPI establishes identity and tenant scope; LangGraph owns routing/checkpoints; adapters isolate vendor contracts; PostgreSQL persists state, knowledge, actions, and audit; Redis supports work and bounded execution.

## Agent routing and safety

```mermaid
flowchart TD
  Input[Authenticated message] --> Route{Intent + confidence}
  Route -->|FAQ| RAG[Retrieve tenant knowledge]
  Route -->|Order read| Read[Commerce read tool]
  Route -->|Address / refund / lead| Policy[Validate arguments + policy]
  RAG --> Grounded{Citation valid?}
  Read --> Respond[Persist + stream]
  Policy --> Eligible{Allowed + certain?}
  Eligible -->|No| Explain[Explain or escalate]
  Eligible -->|Yes| Pause[Persist pending action]
  Pause --> Decision{Customer decision}
  Decision -->|Cancel / expiry| Stop[Record no-op]
  Decision -->|Approve| Execute[Idempotent execution]
  Execute --> Audit[Persist result + audit]
  Grounded -->|Yes| Respond
  Grounded -->|No| Handoff[Create staff ticket]
  Explain --> Handoff
  Execute -->|Failure| Handoff
```

This describes controls, not hidden chain-of-thought.

## Design decisions

Policies live in versioned, tenant-scoped RAG because prose needs citation and controlled updates. Orders, refunds, and contacts come from provider APIs because they are mutable operational facts. Reads execute directly; consequential writes persist a structured pending action and suspend the graph. Approval resumes with an expiring signed token and idempotency key, so retries reuse an outcome rather than duplicate a side effect.

Low confidence, missing data, provider failure, or an explicit request creates a tenant-scoped ticket. Staff can claim, publicly reply, privately note, resolve, or return it to AI; audit history preserves transitions. Organization scope reaches sessions, RLS queries, provider bindings, knowledge, tools, tickets, and analytics. The runtime database role is tested as non-owner/non-superuser, while production-like Compose adds TLS, headers, network isolation, and mounted-secret support.

## Evidence, tradeoffs, and limitations

Deterministic evaluations cover grounding, routing, confirmations, failures, multilingual behavior, and adversarial inputs. Metrics cover HTTP, agent, tools/providers, queues, budgets, and SSE. The M15 local baseline completed 102 mixed workflows at concurrency 1/4/8 with zero workflow errors; at concurrency 8 it measured 0.754 workflows/s, HTTP P50/P95 2062/2872 ms, and agent-run P50/P95 2501/2896 ms. These are documented local Docker results, not a production SLA.

Deterministic mode is repeatable but does not demonstrate generative quality. OpenAI mode is optional and variable. Mock REST services exercise persistence, auth, failures, and contracts, but not every vendor edge case. Demo authentication is synthetic. No cloud deployment, live Shopify/HubSpot verification, or production traffic is claimed.

To replace mocks, independently select `shopify` and/or `hubspot`, supply secrets through the documented mechanism, and retain the same application-facing ports. A client launch must still validate scopes, webhooks, rate limits, tenant mapping, retention, and rollback in its own sandbox accounts.
