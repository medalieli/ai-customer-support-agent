# ADR-013: Grounded knowledge and public order workflows

Status: Accepted

## Decision

M7 adds read-only grounded FAQ and live order support to the M6 durable graph. Public order
numbers are normalized and resolved by a provider operation whose organization and customer scope
comes exclusively from authenticated server context. Internal provider identifiers are never
accepted from model-generated arguments.

Knowledge answers use M4 hybrid retrieval and reranking, then the OpenAI Responses API with a
strict output schema. Retrieved text is delimited as untrusted data. Every cited receipt is
revalidated against the active tenant-owned document version before an answer is persisted or
streamed. Missing evidence, invalid citations, model failures, and action claims fail closed.

Order responses are composed from a minimized normalized fact set and identify their mock-commerce
source as live data with a UTC retrieval timestamp. Combined FAQ and order messages execute both
read-only tools and return separate, language-matched sections.

## Consequences

M7 performs no writes. Address changes, refunds, CRM writes, confirmation UX, tickets, staff
handoff, webhooks, and frontend chat remain deferred. Local and automated execution is restricted
to mock commerce and mock CRM adapters.
