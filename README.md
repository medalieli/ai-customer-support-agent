# NovaCart AI Customer Support Agent

NovaCart is a standalone portfolio project for a production-style customer-support platform. **Milestones M0 through M2 are complete.** M2 adds tenant-scoped persistence, local synthetic identity, durable conversation/message APIs, RBAC, sessions and append-only audit events on the M1 foundation.

No chatbot, RAG, LangGraph graph, commerce/CRM adapters, order system, streaming, production OIDC or final product interface is implemented. Those remain later milestones.

## Foundation services

| Service | Local URL | Purpose |
|---|---|---|
| Frontend | <http://localhost:3000> | Minimal M1 placeholder and live API readiness indicator |
| API | <http://localhost:8000> | FastAPI foundation |
| OpenAPI | <http://localhost:8000/docs> | Interactive API documentation |
| PostgreSQL | `127.0.0.1:5432` | Loopback-only for native migrations/tests; persistent PostgreSQL 17 + pgvector volume |
| Redis | Internal only | ARQ queue and worker health; persistent named volume |

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
# and choose a local-only NOVACART_DEMO_STAFF_PASSWORD.
docker compose config --quiet
docker compose up --build -d --wait
docker compose ps
```

Then open <http://localhost:3000>. The status pill should report that the API and dependencies are ready.

Mock mode is the default through the exact M0 switches:

```text
COMMERCE_PROVIDER=mock
CRM_PROVIDER=mock
```

M2 does not invoke mock providers, OpenAI, Shopify, or HubSpot. Optional credentials may remain empty. Demo identity is explicitly enabled in `.env.example` for local synthetic data and is rejected in production configuration.

## Migrations and worker verification

The API applies migrations before starting. M1 enables pgvector; M2 adds the approved domain schema, tenant policies and append-only audit trigger. Seed data is explicit and idempotent:

```powershell
docker compose run --rm api alembic upgrade head
docker compose exec -T api python -m app.seed
docker compose exec -T api python -m app.seed # safe idempotency check
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

## Common startup problems

- **Compose reports a missing variable:** copy `.env.example` to `.env` and set both PostgreSQL password fields to the same value. Existing volumes retain the password used at creation; reset only the project volumes if intentionally changing it in a disposable environment.
- **Port 3000, 8000 or 5432 is busy:** stop the conflicting process or change the loopback mapping (`POSTGRES_HOST_PORT` for PostgreSQL) and matching native-test settings.
- **API is unhealthy:** run `docker compose logs api postgres redis`; readiness intentionally fails if PostgreSQL, Redis, the migration, or pgvector is unavailable.
- **Frontend reports unavailable:** confirm `curl.exe http://localhost:8000/health/ready`, the frontend build-time API URL, and browser access to localhost:8000.
- **Worker is unhealthy:** inspect `docker compose logs worker redis` and run the worker probe above.

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
