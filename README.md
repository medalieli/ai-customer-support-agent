# NovaCart AI Customer Support Agent

NovaCart is a standalone portfolio project for a production-style customer-support platform.
**Milestones M0 through M11 are complete.** M11 adds durable human escalation, tenant-scoped
support tickets, and an RBAC-protected staff handoff lifecycle.

The local workflow uses real OpenAI structured extraction only when configured and always uses the
persistent mock CRM. HubSpot remains credential-gated and is verified with mocked HTTP contracts;
live Shopify/HubSpot validation, external notifications, webhooks, and the final staff inbox remain
deferred.

## Foundation services

| Service | Local URL | Purpose |
|---|---|---|
| Frontend | <http://localhost:3000> | Minimal M1 placeholder and live API readiness indicator |
| API | <http://localhost:8000> | FastAPI foundation |
| OpenAPI | <http://localhost:8000/docs> | Interactive API documentation |
| PostgreSQL | `127.0.0.1:5432` | Loopback-only for native migrations/tests; persistent PostgreSQL 17 + pgvector volume |
| Redis | Internal only | ARQ queue and worker health; persistent named volume |
| Mock commerce | <http://localhost:8080> | Synthetic external commerce API with an independent SQLite volume |
| Mock CRM | <http://localhost:8090> | Synthetic contacts and notes API with an independent SQLite volume |

Health endpoints:

- `GET /` returns service metadata.
- `GET /health/live` proves only that the API process is serving requests.
- `GET /health/ready` checks PostgreSQL, Redis, and the installed `vector` extension. Failures return HTTP 503 with only component-level status—never secrets or stack traces.

## Requirements

The recommended workflow requires Git and Docker Desktop/Engine with Docker Compose. Native quality checks additionally require Python 3.10–3.13 and Node.js 22 with npm 10+. Verified M1 versions were Docker Engine 29.7.2, Compose 5.4.0, Python 3.10.0, Node 22.16.0, and npm 10.9.2.

## Start locally with Docker

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
# Set the same strong local value for POSTGRES_PASSWORD and NOVACART_POSTGRES_PASSWORD,
# choose NOVACART_DEMO_STAFF_PASSWORD, and replace MOCK_COMMERCE_INTERNAL_API_KEY.
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps
```

Then open <http://localhost:3000>. The status pill should report that the API and dependencies are ready.
Mock commerce health and OpenAPI are at <http://localhost:8080/health/ready> and
<http://localhost:8080/docs>. Its `/v1` contract requires internal authentication and trusted tenant/customer headers; see [API contract](docs/API.md).

Mock mode is the default through the exact M0 switches:

```text
COMMERCE_PROVIDER=mock
CRM_PROVIDER=mock
```

Mock mode never invokes Shopify or HubSpot, and their credentials may remain empty. Demo identity and provider failure simulation are explicitly enabled only for local development and rejected in production configuration.

M5 connects application-owned provider ports to mock commerce and persistent mock CRM by default. No Shopify or HubSpot account is required. Integration modes are independently selectable:

```text
# Local demo
COMMERCE_PROVIDER=mock
CRM_PROVIDER=mock

# Optional integration mode
COMMERCE_PROVIDER=shopify
NOVACART_SHOPIFY_STORE_DOMAIN=your-development-store.myshopify.com
NOVACART_SHOPIFY_ACCESS_TOKEN=stored-secret
NOVACART_SHOPIFY_API_VERSION=2026-01

CRM_PROVIDER=hubspot
NOVACART_HUBSPOT_ACCESS_TOKEN=stored-secret
```

Missing credentials for a selected real provider fail startup configuration; mixed modes work and there is no automatic mock fallback. Live vendor validation remains M12/M13. Shopify refund creation is intentionally unsupported in M5 because it would move money.

## Knowledge ingestion and retrieval

M4 ships original synthetic NovaCart policies in `backend/knowledge-base`, in English and French. Admin uploads accept UTF-8 Markdown/plain text and PDFs up to 2 MiB. The API stores a pending immutable version and queues ARQ; the worker extracts text, creates deterministic overlapping chunks, generates embeddings, and marks the version ready or failed.

Portfolio/demo configuration uses OpenAI `text-embedding-3-small` at 1,536 dimensions and the local `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` reranker. `NOVACART_OPENAI_API_KEY` is required in that mode. Automated tests use the explicit `fake` embedding provider and deterministic reranker at the same 1,536 dimensions. Production rejects either test provider, and a selected real provider fails explicitly rather than falling back. Model weights persist in the Compose `model_cache` volume.

Only approved, non-deleted documents' active ready version with the current indexing fingerprint is searchable. Retrieval combines PostgreSQL full-text rank and pgvector cosine candidates with reciprocal-rank fusion, followed by the configured reranker. It returns passages, scores, and persisted citation receipts; it never generates an answer. Seed and evaluate with:

```powershell
docker compose exec -T api python -m app.knowledge.seed
docker compose exec -T api python -m app.knowledge.seed # idempotent
docker compose exec -T api python -m app.knowledge.evaluation
```

The expanded 29-case dataset reports Recall@5, MRR, unsupported/isolation false positives, mean latency and p95 latency from actual retrieval. Test-provider and real-provider runs must be labeled separately; a test-provider score is not a production semantic-quality claim. See [API documentation](docs/API.md) for lifecycle and retrieval routes.

## Migrations and worker verification

The API applies migrations before starting. M1 enables pgvector; M2 adds the approved domain schema, tenant policies and append-only audit trigger; M4 adds knowledge content, vector/FTS indexes and durable citation receipts. Seed data is explicit and idempotent:

```powershell
docker compose run --rm api alembic upgrade head
docker compose exec -T api python -m app.seed
docker compose exec -T api python -m app.seed # safe idempotency check
docker compose exec -T api python -m app.knowledge.seed
docker compose exec -T postgres psql -U novacart -d novacart -c "SELECT version_num FROM alembic_version"
docker compose exec -T postgres psql -U novacart -d novacart -c "SELECT extversion FROM pg_extension WHERE extname='vector'"
docker compose exec -T worker python -m app.worker.probe
```

## Native quality checks

The existing root `.venv` is supported and ignored by Git:

```powershell
# Create only if it does not already exist: py -m venv .venv
Set-Location backend
..\.venv\Scripts\python.exe -m pip install -e ".[dev]"
..\.venv\Scripts\ruff.exe format --check .
..\.venv\Scripts\ruff.exe check .
..\.venv\Scripts\mypy.exe app tests
..\.venv\Scripts\pytest.exe

Set-Location ..\frontend
npm ci
npm run lint
npm run typecheck
npm test
npm run build
Set-Location ..

Set-Location mock-commerce
..\.venv\Scripts\python.exe -m ruff format --check .
..\.venv\Scripts\python.exe -m ruff check .
..\.venv\Scripts\python.exe -m mypy app tests
..\.venv\Scripts\python.exe -m pytest
Set-Location ..
```

CI repeats these checks and validates/builds the Compose services. Generated dependency, build, coverage, cache, secret, log, and local-database files are ignored.

Database integration tests run when `NOVACART_RUN_DB_TESTS=1`; point `NOVACART_POSTGRES_HOST` at a disposable PostgreSQL 17/pgvector database. See [M2 HTTP API](docs/API.md) for endpoints and access rules. Seeded logins use the password configured in `NOVACART_DEMO_STAFF_PASSWORD`; personas are `amira-en`, `lucas-fr`, and the isolation-only `nora-en` in the second tenant.

## Stop, restart, and reset

Stop containers while retaining PostgreSQL and Redis data:

```powershell
docker compose down
```

Restart and verify persisted migration state:

```powershell
docker compose up -d --wait
docker compose exec -T postgres psql -U novacart -d novacart -c "SELECT version_num FROM alembic_version"
```

To delete **only this Compose project's development data**, first verify the project name and volumes, then remove them:

```powershell
docker compose ls
docker volume ls --filter label=com.docker.compose.project=novacart-support
docker compose down --volumes
```

This is destructive and cannot recover local database/queue data. It does not touch the source tree or `.venv`.

Mock commerce state is isolated in `novacart-support_mock_commerce_data`. To reset only that
synthetic external system, stop `mock-commerce`, verify that exact volume name with `docker volume
inspect`, remove it, and start the service again. Its deterministic fixtures are recreated on boot;
the PostgreSQL and Redis volumes are untouched.

## Common startup problems

- **Compose reports a missing variable:** copy `.env.example` to `.env` and set both PostgreSQL password fields to the same value. Existing volumes retain the password used at creation; reset only the project volumes if intentionally changing it in a disposable environment.
- **Port 3000, 8000, 8080 or 5432 is busy:** stop the conflicting process or change the loopback mapping (`MOCK_COMMERCE_HOST_PORT` or `POSTGRES_HOST_PORT`) and matching native-test settings.
- **API is unhealthy:** run `docker compose logs api postgres redis`; readiness intentionally fails if PostgreSQL, Redis, the migration, or pgvector is unavailable.
- **Frontend reports unavailable:** confirm `curl.exe http://localhost:8000/health/ready`, the frontend build-time API URL, and browser access to localhost:8000.
- **Worker is unhealthy:** inspect `docker compose logs worker redis` and run the worker probe above.
- **Knowledge version remains pending:** confirm Redis and worker health, then use the Admin retry endpoint. A failed version stores only a safe error code; inspect redacted worker logs for operational diagnosis.
- **Native database tests skip:** set `NOVACART_RUN_DB_TESTS=1` and point `NOVACART_POSTGRES_HOST` to the disposable/local pgvector PostgreSQL instance.

## Documentation

- [Milestones](MILESTONES.md)
- [Product requirements](docs/PRODUCT_REQUIREMENTS.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Agent workflows and tools](docs/AGENT_WORKFLOWS_AND_TOOLS.md)
- [Data model](docs/DATA_MODEL.md)
- [Security threat model](docs/SECURITY_THREAT_MODEL.md)
- [Evaluation plan](docs/EVALUATION_PLAN.md)
- [Demo scenarios](docs/DEMO_SCENARIOS.md)
- [Architecture decisions](docs/adr/)
