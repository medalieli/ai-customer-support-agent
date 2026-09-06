# Production-like operations

## Boundaries and configuration

`NOVACART_APP_ENV` selects development, test, or production independently of `COMMERCE_PROVIDER` (`mock`/`shopify`) and `CRM_PROVIDER` (`mock`/`hubspot`). Production-like mode intentionally uses the internal persistent mocks; it makes no Shopify or HubSpot request. Real integration mode is credential-gated and has only contract-test coverage unless an operator separately verifies a sandbox account.

Production startup rejects demo authentication, insecure cookies/public HTTP URLs, API documentation, disabled CSRF, debug logging, weak database/Redis/action secrets, a privileged/default database identity, deterministic/fake AI providers, and selected real adapters without credentials. Secret-file settings support Docker-mounted PostgreSQL and Redis credentials. `.env.production.example` contains placeholders, not usable secrets.

Create `secrets/` (gitignored), generate independent random values for the two database roles and Redis, and provide a trusted certificate/key as `tls.crt`/`tls.key`. `scripts/new-production-secrets.ps1` creates the mounted credentials; its self-signed certificate switch is for local verification only. Copy `.env.production.example` to `.env.production`, replace every placeholder, then:

```powershell
powershell -File scripts/new-production-secrets.ps1 -DevelopmentSelfSignedCertificate
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml config --quiet
docker compose --env-file .env.production -f compose.yaml -f compose.production.yaml up --build -d --wait
curl.exe -k -I http://localhost:8088/health/live
curl.exe -k -I https://localhost:8443/health/ready
```

Only the proxy publishes host ports. Data services are on an internal network; API, worker, and mocks have no published ports. The proxy redirects HTTP, terminates TLS, sets HSTS/CSP/frame/content-type/referrer/permissions headers, caps bodies at 2 MiB, and disables buffering with a 65-second read timeout for SSE. Application images are multi-stage and non-root. The overlay drops capabilities, enables `no-new-privileges`, uses read-only roots where practical, tmpfs scratch space, health gates, restart policies, graceful stop intervals, resource bounds, and rotating local logs.

## Database roles and migrations

The one-shot `migrate` service uses `novacart_migrator`, applies Alembic before startup, and provisions `novacart_runtime`. Runtime is explicitly `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS`, receives DML rather than ownership, and cannot migrate. Forced RLS policies compare every tenant table to transaction-local `app.organization_id`; test it as the runtime identity, including no-scope and cross-tenant queries.

Never run a schema downgrade against live data as an automatic rollback. Roll application images back only when the prior version is schema-compatible. Otherwise preserve evidence/backups and ship a reviewed forward-fix migration. Expand/contract migrations must keep old and new binaries compatible through rollout.

## Backups and restoration

The scripts validate names, keep artifacts within the repository's ignored `backups/` directory, use PostgreSQL custom format without owners/ACLs, and restore only into a uniquely named disposable database:

```powershell
powershell -File scripts/backup-postgres.ps1
powershell -File scripts/restore-drill.ps1
```

The drill verifies organization/customer/conversation counts and that restored conversations retain valid tenant/customer relationships, then drops only its generated database. Encrypt backups outside the repository, restrict access, record checksums/retention, and periodically restore using the same PostgreSQL major/extension version.

## Incident runbooks

- Worker failure: stop ingestion pressure if backlog grows, preserve PostgreSQL job truth, replace the worker, confirm health key and queue drain. Do not manually replay writes with new idempotency keys.
- Provider outage: keep circuit breakers/timeouts active, surface safe degraded responses, pause guarded writes, reconcile ambiguous outcomes using original idempotency keys after recovery.
- Webhook dead letters: inspect safe reason codes/fingerprints, repair configuration/code, use the scoped Admin retry operation; never paste raw payloads into tickets or logs.
- Database outage: keep API unready, stop migration/writers, restore connectivity or latest verified backup, check Alembic head and tenant relationships before reopening traffic.
- Redis outage: PostgreSQL remains durable truth; chat control/queue functions fail closed. Restore Redis, restart workers, and recover due database jobs.

## Rotation

Rotate provider/webhook secrets one connection and tenant at a time: install the encrypted new secret as current, retain the encrypted previous secret for the documented replay overlap, verify signed test delivery, then remove the previous value. Rotate database/Redis credentials by creating a new secret, updating the role/service, rolling clients, confirming readiness, then revoking the old credential.

Encryption/action keys require versioned key identifiers and dual-read/single-write migration: back up first, deploy readers for old+new keys, re-encrypt in bounded tenant batches, verify counts/decryption canaries, switch writes, then retire the old key after the retention window. Never replace a key in place while ciphertext still depends on it.

Logs, traces and health endpoints are allowlisted/content-free. During incidents, search for PII canaries and secrets before exporting diagnostics.

## Performance and shutdown verification

Use `scripts/run-load-test.ps1`; it creates and destroys only a unique Compose project. Capture host CPU/RAM/OS, Docker resource limits, provider mode, database size and timestamp. Results are end-to-end wall-clock HTTP/SSE timings, never evaluator microbenchmarks. Confirm Prometheus tool/provider latency, DB/Redis readiness, rate/budget `429`s, and queue recovery.

For graceful shutdown, keep an SSE request open, run `docker compose stop -t 30 api worker`, verify accepted work completes or remains durably recoverable, restart, and confirm readiness and persisted counts.
