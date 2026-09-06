"""Dependency-free, reproducible HTTP/SSE load smoke for the isolated mock-provider stack."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

PROMPTS = (
    "What is the NovaCart return policy?",
    "Puis-je retourner un article dans les 30 jours ?",
    "Track my order NC-1001",
    "I need a human support agent",
    "Refund order NC-1004; reason: changed mind; SKU: MSE-PRO; quantity: 1; amount: USD 69.00",
    "I want an enterprise product demo and to speak with sales by email",
    "Change the shipping address for order NC-1001 to 21 Cedar Avenue, Rabat 10000, Morocco",
)


def percentile(values: list[float], p: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * p))] if values else 0.0


def metric_totals(base: str) -> dict[str, float]:
    wanted = (
        "novacart_http_requests_total",
        "novacart_http_request_duration_seconds_sum",
        "novacart_provider_requests_total",
        "novacart_provider_request_duration_seconds_sum",
        "novacart_tool_executions_total",
        "novacart_control_rejections_total",
    )
    try:
        payload = build_opener().open(base + "/metrics", timeout=10).read().decode()
    except OSError:
        return {}
    totals = {name: 0.0 for name in wanted}
    for line in payload.splitlines():
        for name in wanted:
            if line.startswith(name + "{") or line.startswith(name + " "):
                totals[name] += float(line.rsplit(" ", 1)[1])
    return totals


def one(base: str, index: int, approve_writes: bool = False) -> dict[str, float | int]:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": "novacart-browser-v1",
    }

    def call(path: str, method: str = "GET", body: object | None = None):
        data = json.dumps(body).encode() if body is not None else None
        started = time.perf_counter()
        request_headers = {**headers, "Idempotency-Key": f"load-{uuid4()}"}
        response = opener.open(
            Request(base + path, data=data, headers=request_headers, method=method),
            timeout=40,
        )
        first_byte_ms = (time.perf_counter() - started) * 1000
        payload = response.read()
        return response, payload, first_byte_ms, (time.perf_counter() - started) * 1000

    call(
        "/api/v1/auth/demo-login",
        "POST",
        {"organization_slug": "novacart", "persona_key": "amira-en"},
    )
    _, raw, _, create_ms = call(
        "/api/v1/conversations",
        "POST",
        {"locale": "en", "title": f"load-{index}-{uuid4().hex[:8]}"},
    )
    conversation = json.loads(raw)["id"]
    _, raw, _, run_ms = call(
        f"/api/v1/agent/threads/{conversation}/messages",
        "POST",
        {"content": PROMPTS[index % len(PROMPTS)]},
    )
    run = json.loads(raw)
    if run.get("confirmation"):
        confirmation = run["confirmation"]
        decision = "approve" if approve_writes and index in {4, 5, 6} else "deny"
        call(
            f"/api/v1/agent/threads/{conversation}/resume",
            "POST",
            {
                "checkpoint_version": run["checkpoint_version"],
                "action_id": confirmation["action_id"],
                "confirmation_token": confirmation["confirmation_token"],
                "decision": decision,
                "value": {"decision": decision},
            },
        )
    _, events, sse_first_ms, sse_ms = call(
        f"/api/v1/agent/runs/{run['run_id']}/events?after=0"
    )
    return {
        "create_ms": create_ms,
        "run_ms": run_ms,
        "sse_first_ms": sse_first_ms,
        "sse_ms": sse_ms,
        "sse_events": events.count(b"event:"),
    }


def analytics_probe(base: str) -> float:
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    headers = {
        "Content-Type": "application/json",
        "X-CSRF-Token": "novacart-browser-v1",
    }
    login = Request(
        base + "/api/v1/auth/staff-login",
        data=json.dumps(
            {
                "organization_slug": "novacart",
                "email": "support@novacart.test",
                "password": "synthetic-demo-password",
            }
        ).encode(),
        headers=headers,
        method="POST",
    )
    opener.open(login, timeout=20).read()
    started = time.perf_counter()
    opener.open(base + "/api/v1/staff/analytics?range=24h", timeout=20).read()
    return (time.perf_counter() - started) * 1000


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--approve-writes", action="store_true")
    args = parser.parse_args()
    metrics_before = metric_totals(args.base_url.rstrip("/"))
    started = time.perf_counter()
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(one, args.base_url.rstrip("/"), i, args.approve_writes)
            for i in range(args.requests)
        ]
        for future in as_completed(futures):
            try:
                results.append(future.result())
            except HTTPError as exc:
                try:
                    problem = json.loads(exc.read())["error"]["code"]
                except (ValueError, KeyError):
                    problem = "safe_error_unavailable"
                errors.append(f"HTTP_{exc.code}:{problem}")
            except (OSError, KeyError, ValueError) as exc:
                errors.append(type(exc).__name__)
    elapsed = time.perf_counter() - started
    runs = [float(r["run_ms"]) for r in results]
    sse = [float(r["sse_ms"]) for r in results]
    sse_first = [float(r["sse_first_ms"]) for r in results]
    http = [
        float(value)
        for result in results
        for value in (result["create_ms"], result["run_ms"], result["sse_ms"])
    ]
    analytics_ms = analytics_probe(args.base_url.rstrip("/")) if results else 0.0
    report = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "concurrency": args.concurrency,
            "provider": "configured isolated mock/deterministic stack",
            "approved_guarded_writes": args.approve_writes,
        },
        "attempted_workflows": args.requests,
        "completed_workflows": len(results),
        "throughput_workflows_per_second": round(len(results) / elapsed, 3),
        "error_rate": round(len(errors) / args.requests, 4),
        "errors": errors,
        "http_ms": {
            "p50": round(statistics.median(http), 2) if http else 0,
            "p95": round(percentile(http, 0.95), 2),
        },
        "agent_run_ms": {
            "p50": round(statistics.median(runs), 2) if runs else 0,
            "p95": round(percentile(runs, 0.95), 2),
        },
        "sse_first_event_ms": {
            "p50": round(statistics.median(sse_first), 2) if sse_first else 0,
            "p95": round(percentile(sse_first, 0.95), 2),
        },
        "sse_completion_ms": {
            "p50": round(statistics.median(sse), 2) if sse else 0,
            "p95": round(percentile(sse, 0.95), 2),
        },
        "sse_events": sum(int(r["sse_events"]) for r in results),
        "analytics_ms": round(analytics_ms, 2),
        "elapsed_seconds": round(elapsed, 3),
        "prometheus_delta": {
            key: round(value - metrics_before.get(key, 0.0), 6)
            for key, value in metric_totals(args.base_url.rstrip("/")).items()
        },
        "notes": "HTTP wall-clock timings; not deterministic evaluator timings.",
    }
    print(json.dumps(report, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
