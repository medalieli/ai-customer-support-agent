# Observability, analytics, and cost controls

NovaCart exports content-free OpenTelemetry spans and bounded Prometheus metrics. Traces may
contain generated request, trace, conversation, run, and worker-job identifiers for correlation.
They must never contain prompts, message bodies, retrieved passages, email or postal addresses,
provider payloads, credentials, or model reasoning. Identifiers are span attributes only and are
never Prometheus labels.

Start the development-only stack with:

```sh
docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d
```

Grafana is loopback-bound at `http://localhost:3001`; Prometheus, Tempo, and the collector remain
on an internal Docker network. The provisioned **NovaCart operations** dashboard links Prometheus
and Tempo. Persistent named volumes retain local dashboards, metrics, and traces across restarts.
Removing those volumes is an explicit operator action.

## Correlation and privacy

Inbound `X-Request-ID` is accepted only when it matches a short safe identifier grammar; otherwise
one is generated. Responses return request, correlation, and trace IDs. W3C `traceparent` context is
injected into provider calls. LangGraph nodes, retrieval, tools, PostgreSQL sessions, ARQ jobs, and
webhook processing create child spans. Logs and telemetry use allowlisted outcome names. PII
canaries are tested against metric and span exports.

## Pricing and budgets

The OpenAI counters use actual `input_tokens` and `output_tokens` reported by the Responses API.
`NOVACART_OPENAI_*_COST_PER_MILLION_USD` is a configurable pricing table. The resulting value is
explicitly named **estimated cost** and is not an invoice. Tenant daily token/cost reservations,
tenant and actor request rates, and concurrent-run caps are Redis-backed. Rejections return a safe
`429` with `Retry-After`. Customer limits are attached only to chat submission/resumption; staff
resolution and webhook ingestion are deliberately outside that dependency.

The checked-in defaults are the GPT-5 Mini standard text rates published on 2026-09-06
([$0.25 input and $2.00 output per million tokens](https://developers.openai.com/api/docs/models/gpt-5-mini)).
Operators must review and update the table when the configured model or provider pricing changes.
The Responses API exposes both counters in its optional
[`usage` object](https://developers.openai.com/api/reference/cli/resources/responses/methods/create).

## Local SLOs

The initial objectives are 99% API availability, API P95 below 2 seconds, agent failures below 5%,
citation-validation failures below 5%, provider errors/rate limits below 10%, worker backlog below
100 jobs, and zero dead-letter events. `observability/alerts.yaml` evaluates these locally. No
PagerDuty, email, Slack, or other external alert receiver is configured.

The tenant-scoped `/api/v1/staff/analytics` endpoint accepts `24h`, `7d`, `30d`, or `90d`. Support
and Admin can access aggregates; customers receive no access. The endpoint never returns raw
conversation content or customer identifiers.
