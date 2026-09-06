from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import cast
from uuid import UUID

import pytest
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient

import app.evaluation as evaluation
from app.core.config import Settings
from app.evaluation import evaluate
from app.main import create_app
from app.observability import (
    bounded,
    continued_job_context,
    correlation_id,
    job_carrier,
    metrics_payload,
    new_request_id,
    record_openai_usage,
    safe_route,
)
from app.services.auth import Principal
from app.services.controls import enforce_customer_controls


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    async def incrby(self, key: str, amount: int) -> int:
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    async def expire(self, key: str, ttl: int) -> bool:
        return bool(key and ttl)

    async def decr(self, key: str) -> int:
        self.values[key] = self.values.get(key, 0) - 1
        return self.values[key]

    async def delete(self, key: str) -> int:
        self.values.pop(key, None)
        return 1


def fake_request(redis: FakeRedis) -> Request:
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(redis=redis))))


def principal(kind: str = "customer") -> Principal:
    identifier = UUID("10000000-0000-0000-0000-000000000001")
    return Principal(
        kind=kind,  # type: ignore[arg-type]
        organization_id=identifier,
        subject_id=identifier,
        role=None,
        session_id=identifier,
    )


def test_metric_labels_are_bounded_and_payload_excludes_pii_canary() -> None:
    canary = "customer-canary@example.test 91 Private Street ORDER-999"
    assert bounded(canary, {"en", "fr"}) == "other"
    assert (
        safe_route("/conversations/10000000-0000-0000-0000-000000000001") == "/conversations/{id}"
    )
    assert safe_route("/api/v1/webhooks/mock/key-canary") == "/api/v1/webhooks/mock/{endpoint}"
    assert canary.encode() not in metrics_payload()


def test_actual_openai_usage_and_estimated_cost_are_recorded() -> None:
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=100, output_tokens=20))
    record_openai_usage(response, Settings(app_env="test"), "answer")
    payload = metrics_payload()
    assert b'novacart_openai_tokens_total{direction="input"' in payload
    assert b"novacart_openai_estimated_cost_usd_total" in payload
    record_openai_usage(SimpleNamespace(usage=None), Settings(app_env="test"), "answer")
    record_openai_usage(
        SimpleNamespace(usage=SimpleNamespace(input_tokens=object(), output_tokens=object())),
        Settings(app_env="test"),
        "unknown",
    )


def test_correlation_carrier_is_content_free_and_validated() -> None:
    token = correlation_id.set("safe-correlation")
    try:
        carrier = job_carrier()
        assert carrier["x-correlation-id"] == "safe-correlation"
        with continued_job_context(carrier):
            assert correlation_id.get() == "safe-correlation"
        with continued_job_context(None):
            assert correlation_id.get() == "safe-correlation"
    finally:
        correlation_id.reset(token)
    assert new_request_id("too short") != "too short"
    assert new_request_id("safe-request-id") == "safe-request-id"


def test_http_request_continues_w3c_trace_context() -> None:
    trace = "0123456789abcdef0123456789abcdef"
    parent = "0123456789abcdef"
    response = TestClient(create_app(Settings(app_env="test"))).get(
        "/", headers={"traceparent": f"00-{trace}-{parent}-01"}
    )
    assert response.status_code == 200
    assert response.headers["x-trace-id"] == trace


@pytest.mark.asyncio
async def test_actor_rate_limit_returns_safe_429_and_retry_after() -> None:
    redis = FakeRedis()
    request = fake_request(redis)
    settings = Settings(app_env="test", actor_rate_limit_per_minute=1)
    first = enforce_customer_controls(request, principal(), settings)
    await anext(first)
    await cast(AsyncGenerator[None, None], first).aclose()
    second = enforce_customer_controls(request, principal(), settings)
    with pytest.raises(HTTPException) as caught:
        await anext(second)
    assert caught.value.status_code == 429
    assert caught.value.headers == {"Retry-After": "60"}
    assert "customer" not in str(caught.value.detail).lower()


@pytest.mark.asyncio
async def test_staff_bypasses_customer_chat_controls() -> None:
    request = fake_request(FakeRedis())
    controlled = enforce_customer_controls(request, principal("staff"), Settings(app_env="test"))
    await anext(controlled)
    await cast(AsyncGenerator[None, None], controlled).aclose()
    assert not request.app.state.redis.values


@pytest.mark.asyncio
async def test_tenant_rate_token_and_concurrency_limits() -> None:
    redis = FakeRedis()
    request = fake_request(redis)
    rate_settings = Settings(
        app_env="test", customer_rate_limit_per_minute=1, actor_rate_limit_per_minute=10
    )
    first = enforce_customer_controls(request, principal(), rate_settings)
    await anext(first)
    await cast(AsyncGenerator[None, None], first).aclose()
    with pytest.raises(HTTPException):
        await anext(enforce_customer_controls(request, principal(), rate_settings))

    budget_request = fake_request(FakeRedis())
    with pytest.raises(HTTPException) as token_error:
        await anext(
            enforce_customer_controls(
                budget_request,
                principal(),
                Settings(app_env="test", tenant_daily_token_budget=1000),
            )
        )
    assert cast(dict[str, str], token_error.value.detail)["code"] == "capacity_limited"

    cost_request = fake_request(FakeRedis())
    with pytest.raises(HTTPException) as cost_error:
        await anext(
            enforce_customer_controls(
                cost_request,
                principal(),
                Settings(
                    app_env="test",
                    tenant_daily_cost_budget_usd=0.01,
                    openai_output_cost_per_million_usd=100,
                ),
            )
        )
    assert cast(dict[str, str], cost_error.value.detail)["code"] == "capacity_limited"

    concurrent_request = fake_request(FakeRedis())
    concurrency = Settings(app_env="test", tenant_concurrent_runs=1, actor_concurrent_runs=10)
    held = enforce_customer_controls(concurrent_request, principal(), concurrency)
    await anext(held)
    with pytest.raises(HTTPException) as concurrency_error:
        await anext(enforce_customer_controls(concurrent_request, principal(), concurrency))
    assert concurrency_error.value.headers == {"Retry-After": "5"}
    await cast(AsyncGenerator[None, None], held).aclose()


@pytest.mark.asyncio
async def test_deterministic_m14_evaluation_meets_safety_gate() -> None:
    report = await evaluate("deterministic")
    assert report["intent_micro_f1"] == 1
    assert report["correct_tool_rate"] == 1
    assert report["unsafe_write_rate"] == 0
    assert report["duplicate_side_effect_rate"] == 0


@pytest.mark.asyncio
async def test_evaluation_cli_prints_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.argv", ["evaluation"])
    await evaluation.main()
    assert '"provider": "deterministic"' in capsys.readouterr().out


@pytest.mark.asyncio
async def test_evaluation_cli_enforces_deterministic_safety_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_report(_provider: str) -> dict[str, object]:
        return {"intent_micro_f1": 0.5, "unsafe_write_rate": 0.1}

    monkeypatch.setattr("sys.argv", ["evaluation"])
    monkeypatch.setattr(evaluation, "evaluate", failing_report)
    with pytest.raises(SystemExit):
        await evaluation.main()
