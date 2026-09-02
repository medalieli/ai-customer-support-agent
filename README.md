# NovaCart AI Customer Support Agent

NovaCart is a standalone portfolio project for a production-style customer-support platform. **Milestones M0 and M1 are complete.** M1 supplies only the reproducible platform foundation: FastAPI, Next.js, PostgreSQL with pgvector, Redis, an ARQ worker, Alembic, quality tooling, tests, CI, and Docker Compose.

No chatbot, RAG, LangGraph graph, domain tables, authentication, commerce/CRM integration, or product interface is implemented yet. Those remain later milestones.

## Foundation services

| Service | Local URL | Purpose |
|---|---|---|
| Frontend | <http://localhost:3000> | Minimal M1 placeholder and live API readiness indicator |
| API | <http://localhost:8000> | FastAPI foundation |
| OpenAPI | <http://localhost:8000/docs> | Interactive API documentation |
| PostgreSQL | Internal only | PostgreSQL 17 plus pgvector; persistent named volume |
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
# Set the same strong local value for POSTGRES_PASSWORD and NOVACART_POSTGRES_PASSWORD.
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

M1 does not invoke mock providers, OpenAI, Shopify, or HubSpot. Optional credentials may remain empty. Selecting a real provider validates that its future credential fields exist, but no adapter is implemented or called.

## Migrations and worker verification

The API applies migrations before starting. M1 contains exactly one foundation revision, which enables and verifies pgvector; it creates no business tables.

```powershell
docker compose run --rm api alembic upgrade head
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
- **Port 3000 or 8000 is busy:** stop the conflicting process or change the localhost port mapping and `NEXT_PUBLIC_API_URL`, then rebuild the frontend.
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
