import asyncio
from collections.abc import Mapping
from typing import Any

import httpx
from opentelemetry.propagate import inject

from app.observability import PROVIDER_LATENCY, PROVIDERS, Timer, bounded, span
from app.providers.errors import ProviderError
from app.providers.models import ProviderErrorCode


class ProviderHttpClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        retries: int,
        headers: Mapping[str, str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.retries = retries
        self._owned = client is None
        self.client = client or httpx.AsyncClient(
            base_url=base_url, timeout=timeout, headers=dict(headers)
        )

    async def request(
        self, method: str, path: str, *, retry_safe: bool = False, **kwargs: Any
    ) -> httpx.Response:
        can_retry = method == "GET" or retry_safe
        provider = bounded(
            str(self.client.base_url.host),
            {"mock-commerce", "mock-crm", "api.shopify.com", "api.hubapi.com"},
        )
        operation = "read" if method == "GET" else "write"
        timer = Timer.start()
        carrier: dict[str, str] = {}
        inject(carrier)
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update(carrier)
        kwargs["headers"] = headers
        for attempt in range(self.retries + 1):
            try:
                with span(
                    "provider.request",
                    **{"provider.name": provider, "provider.operation": operation},
                ):
                    response = await self.client.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                if attempt < self.retries and can_retry:
                    PROVIDERS.labels(provider, operation, "retry").inc()
                    await asyncio.sleep(0)
                    continue
                PROVIDERS.labels(provider, operation, "timeout").inc()
                PROVIDER_LATENCY.labels(provider, operation).observe(timer.seconds())
                raise ProviderError(ProviderErrorCode.TIMEOUT, retryable=True) from exc
            except httpx.HTTPError as exc:
                if attempt < self.retries and can_retry:
                    PROVIDERS.labels(provider, operation, "retry").inc()
                    await asyncio.sleep(0)
                    continue
                PROVIDERS.labels(provider, operation, "failure").inc()
                PROVIDER_LATENCY.labels(provider, operation).observe(timer.seconds())
                raise ProviderError(ProviderErrorCode.UNAVAILABLE, retryable=True) from exc
            if response.status_code >= 500 and attempt < self.retries and can_retry:
                PROVIDERS.labels(provider, operation, "retry").inc()
                await asyncio.sleep(0)
                continue
            if response.is_error:
                PROVIDERS.labels(
                    provider,
                    operation,
                    "rate_limited" if response.status_code == 429 else "failure",
                ).inc()
                PROVIDER_LATENCY.labels(provider, operation).observe(timer.seconds())
                self._raise(response)
            PROVIDERS.labels(provider, operation, "success").inc()
            PROVIDER_LATENCY.labels(provider, operation).observe(timer.seconds())
            return response
        raise ProviderError(ProviderErrorCode.UNAVAILABLE, retryable=True)

    @staticmethod
    def _raise(response: httpx.Response) -> None:
        code = ""
        try:
            code = str(response.json().get("error", {}).get("code", ""))
        except (ValueError, AttributeError):
            pass
        mapped = {
            400: ProviderErrorCode.VALIDATION,
            401: ProviderErrorCode.NOT_AUTHORIZED,
            403: ProviderErrorCode.NOT_AUTHORIZED,
            404: ProviderErrorCode.NOT_FOUND,
            409: ProviderErrorCode.CONFLICT,
            422: ProviderErrorCode.VALIDATION,
            429: ProviderErrorCode.RATE_LIMITED,
            504: ProviderErrorCode.TIMEOUT,
        }.get(response.status_code, ProviderErrorCode.UNAVAILABLE)
        if code in {"version_conflict", "idempotency_conflict", "order_state_conflict"}:
            mapped = ProviderErrorCode.CONFLICT
        retry_after = response.headers.get("Retry-After")
        raise ProviderError(
            mapped,
            retryable=response.status_code in {429, 500, 502, 503, 504},
            retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
        )

    async def close(self) -> None:
        if self._owned:
            await self.client.aclose()
