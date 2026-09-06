# Load validation

Run only against an isolated test/demo stack using mock commerce, mock CRM, and the explicit deterministic agent configuration. The harness creates unique conversations, reads FAQ/order data, proposes but never approves refund/CRM writes, exercises handoff and SSE, and reports wall-clock HTTP latency.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-load-test.ps1 -Requests 100 -Warmup 8 -ConcurrencyLevels 1,4,8
```

The default executes eight warm-up workflows, including one confirmed refund, CRM lead, and address
update with unique idempotency keys, followed by 102 completed mixed workflows (34 at each of
concurrency 1, 4, and 8; `Requests` is rounded up across the matrix). Bulk guarded writes are denied
to keep repeated runs safe. Each report includes HTTP,
agent-run, SSE first-event/completion latency,
throughput, errors, and relevant Prometheus deltas. Record CPU/RAM, Docker limits, host OS, database
size, provider mode, and timestamp. Verify the worker queue returns to zero. Results are local
baselines, not universal production claims.

The wrapper also primes only disposable Redis control keys and verifies live rate, concurrent-run,
and token-budget rejection as safe `429` responses with `Retry-After` values of 60, 5, and 3600
seconds, then removes the keys and verifies that an agent request succeeds again.
