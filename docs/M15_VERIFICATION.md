# M15 local verification record

Verified on 2026-09-06 on a Lenovo 82K2, AMD Ryzen 5 5600H (6 cores/12 threads),
15.4 GiB host RAM, Windows build 26200, Python 3.10.0, and Docker Desktop configured
with 12 CPUs and 7.4 GiB RAM. All providers were internal mocks and the load model was
deterministic. No Shopify, HubSpot, OpenAI, or cloud deployment was invoked.

## Production stack

The complete production and observability overlays reached healthy state. Only Nginx published
ports (`8088` and `8443`); API, worker, PostgreSQL, Redis, mock commerce, mock CRM, Prometheus,
Grafana, Tempo, and the collector had no host binding. Run the repeatable edge probe with:

```powershell
python scripts/verify-production-edge.py
```

Runtime checks passed for HTTP-to-HTTPS redirect, customer/staff routes, disabled docs/OpenAPI,
HSTS/CSP/frame/content-type headers, untrusted origin rejection, CSRF rejection, and the 2 MiB
request limit. A staff login cookie was `Secure`, `HttpOnly`, and `SameSite=strict`. Synthetic
durable SSE events replayed through Nginx, and `Last-Event-ID: 1` returned only the later event.

API, worker, proxy, PostgreSQL, and Redis were restarted. Synthetic conversation, audit, and agent
thread records remained present. A worker probe stayed at queue depth one across a Redis restart,
was processed once after worker restart, and returned the queue to zero. The running API identity
reported `novacart_runtime|false|false|false` for superuser/create-database/BYPASSRLS. It saw zero
unscoped conversations, one for tenant A, and zero for tenant B.

## Performance baseline

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-load-test.ps1 -Requests 100 -Warmup 8 -ConcurrencyLevels 1,4,8
```

Eight warm-up workflows completed with no errors, including confirmed mock refund, CRM lead, and
address-update flows. The measured matrix completed 102/102 workflows with zero errors:

| Concurrency | Workflows | Throughput/s | HTTP P50/P95 ms | Agent P50/P95 ms | SSE first P50/P95 ms | SSE complete P50/P95 ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 34 | 0.116 | 2056 / 2189 | 2154 / 2208 | 2047 / 2080 | 2048 / 2080 |
| 4 | 34 | 0.434 | 2056 / 2472 | 2250 / 2578 | 2045 / 2063 | 2046 / 2067 |
| 8 | 34 | 0.754 | 2062 / 2872 | 2501 / 2896 | 2053 / 2088 | 2054 / 2094 |

Across the three measured runs, Prometheus deltas were 475 HTTP requests, 75 provider requests,
90 tool executions, 32.361253 seconds of HTTP histogram sum, and 0.821486 seconds of provider
histogram sum. The intentional controls scenario returned safe `429` responses with `Retry-After`
60 (rate), 5 (concurrency), and 3600 (token budget), then recovered with `202`. Final queue depth
was zero. These are local end-to-end wall-clock results, not evaluator microbenchmarks or universal
capacity claims.

## Security and test evidence

- Gitleaks 8.28.0 scanned 19 commits with no leak after narrowly allowlisting three documented
  synthetic fixture strings in `.gitleaks.toml`.
- Trivy 0.68.2 initially identified stale OS and packaging-tool HIGH findings. The image now applies
  Debian security upgrades and excludes build-only Python packaging tools. The final HIGH/CRITICAL
  scan reports 0 Debian and 0 Python findings.
- Trivy generated a 400,163-byte CycloneDX JSON SBOM with SHA-256
  `21C34E716A57A5EFB9D68E1EC9AAF1410D72A20478223891CBF3702F7F18B394`.
- Bandit reported no MEDIUM/HIGH findings and four pre-existing LOW findings (two fail-closed health
  probes and two internal assertions). Backend/mock `pip-audit` and frontend
  `npm audit --audit-level=high` reported no vulnerabilities.
- Backend: 240 passed, 88.17% coverage. Mock commerce: 13 passed, 91.71%. Mock CRM: 11 passed,
  92.15%. Frontend: 7 passed, 100% statements/lines and 75% branches; ESLint, TypeScript, and the
  production Next.js build passed. Ruff, strict MyPy, and both development/production Compose
  configuration validations passed.

The self-signed certificate validates behavior only, not public trust or renewal. No external
provider account, public-network firewall, cloud load balancer, CA chain, or production hardware
was independently verified.
