"""Redis-backed customer-chat controls isolated from staff and webhook traffic."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Annotated, cast

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis

from app.api.dependencies import CurrentPrincipal, RequestSettings
from app.observability import RATE_LIMITS, span


async def _increment(redis: Redis, key: str, ttl: int) -> int:
    with span("redis.control", **{"db.system": "redis", "db.operation": "increment"}):
        value = int(await redis.incr(key))
        if value == 1:
            await redis.expire(key, ttl)
    return value


def _rejected(control: str, scope: str, retry_after: int) -> HTTPException:
    RATE_LIMITS.labels(control, scope).inc()
    return HTTPException(
        status_code=429,
        detail={"code": "capacity_limited", "message": "Please retry later."},
        headers={"Retry-After": str(max(1, retry_after))},
    )


async def enforce_customer_controls(
    request: Request, principal: CurrentPrincipal, settings: RequestSettings
) -> AsyncIterator[None]:
    """Reserve bounded customer capacity; never used by staff resolution or webhook ingress."""
    if principal.kind != "customer":
        yield
        return
    redis_state = getattr(request.app.state, "redis", None)
    if redis_state is None and settings.app_env == "test":
        # Unit/in-process integration apps intentionally run without lifespan.
        # Real E2E/CI services start Redis and exercise the controls directly.
        yield
        return
    if redis_state is None:
        raise _rejected("dependency", "tenant", 5)
    redis = cast(Redis, redis_state)
    tenant = str(principal.organization_id)
    actor = str(principal.subject_id)
    minute = int(datetime.now(timezone.utc).timestamp() // 60)
    tenant_rate = await _increment(redis, f"control:rate:tenant:{tenant}:{minute}", 120)
    actor_rate = await _increment(redis, f"control:rate:actor:{tenant}:{actor}:{minute}", 120)
    if tenant_rate > settings.customer_rate_limit_per_minute:
        raise _rejected("rate", "tenant", 60)
    if actor_rate > settings.actor_rate_limit_per_minute:
        raise _rejected("rate", "actor", 60)

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reserved_tokens = min(4096, settings.agent_max_steps * 256)
    reserved_cost_microusd = int(reserved_tokens * settings.openai_output_cost_per_million_usd)
    token_total = await _increment(redis, f"control:tokens:{tenant}:{day}", 172800)
    if reserved_tokens > 1:
        token_total = int(await redis.incrby(f"control:tokens:{tenant}:{day}", reserved_tokens - 1))
    cost_total = int(await redis.incrby(f"control:cost:{tenant}:{day}", reserved_cost_microusd))
    await redis.expire(f"control:cost:{tenant}:{day}", 172800)
    if token_total > settings.tenant_daily_token_budget:
        raise _rejected("token_budget", "tenant", 3600)
    if cost_total > int(settings.tenant_daily_cost_budget_usd * 1_000_000):
        raise _rejected("cost_budget", "tenant", 3600)

    tenant_key = f"control:concurrent:tenant:{tenant}"
    actor_key = f"control:concurrent:actor:{tenant}:{actor}"
    tenant_active = await _increment(redis, tenant_key, 120)
    actor_active = await _increment(redis, actor_key, 120)
    acquired = True
    try:
        if tenant_active > settings.tenant_concurrent_runs:
            raise _rejected("concurrency", "tenant", 5)
        if actor_active > settings.actor_concurrent_runs:
            raise _rejected("concurrency", "actor", 5)
        yield
    finally:
        if acquired:
            for key in (tenant_key, actor_key):
                value = await redis.decr(key)
                if int(value) <= 0:
                    await redis.delete(key)


CustomerControls = Annotated[None, Depends(enforce_customer_controls)]
