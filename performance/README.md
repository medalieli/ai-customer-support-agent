# Load validation

Run only against an isolated test/demo stack using mock commerce, mock CRM, and the explicit deterministic agent configuration. The harness creates unique conversations, reads FAQ/order data, proposes but never approves refund/CRM writes, exercises handoff and SSE, and reports wall-clock HTTP latency.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run-load-test.ps1 -Requests 60 -Concurrency 8
```

Record the output with CPU/RAM, Docker limits, host OS, database size, provider mode, and timestamp. Query Prometheus for tool/provider latency, database/Redis health, `429` budget/rate rejection counters, worker backlog, and verify backlog returns to zero. Results are not committed as a baseline unless actually measured.
