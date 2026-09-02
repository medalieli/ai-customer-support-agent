import asyncio
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Response

from app.config import Settings, get_settings
from app.errors import CommerceError, register_handlers
from app.schemas import (
    Address,
    AddressUpdate,
    ErrorResponse,
    Order,
    OrderPage,
    OrderSummary,
    RefundCreate,
    RefundRequest,
    Tracking,
)
from app.store import CommerceStore


@dataclass(frozen=True)
class Scope:
    organization_id: str
    customer_ref: str


def settings_dependency() -> Settings:
    return get_settings()


SettingsDependency = Annotated[Settings, Depends(settings_dependency)]


def store_dependency(settings: SettingsDependency) -> CommerceStore:
    return CommerceStore(settings.database_path)


StoreDependency = Annotated[CommerceStore, Depends(store_dependency)]


def authenticated_scope(
    settings: SettingsDependency,
    api_key: Annotated[str | None, Header(alias="X-Internal-API-Key")] = None,
    organization_id: Annotated[str | None, Header(alias="X-Organization-Id")] = None,
    customer_ref: Annotated[str | None, Header(alias="X-External-Customer-Id")] = None,
) -> Scope:
    expected = settings.internal_api_key.get_secret_value()
    if not api_key or not hmac.compare_digest(api_key, expected):
        raise CommerceError(401, "unauthenticated", "Internal authentication is required.")
    if not organization_id or not customer_ref:
        raise CommerceError(401, "unauthenticated", "Trusted commerce scope is required.")
    try:
        normalized_organization = str(UUID(organization_id))
    except ValueError as exc:
        raise CommerceError(422, "validation", "The organization identifier is invalid.") from exc
    return Scope(normalized_organization, customer_ref)


ScopeDependency = Annotated[Scope, Depends(authenticated_scope)]


async def simulate_failure(
    settings: SettingsDependency,
    scope: ScopeDependency,
    failure: Annotated[
        Literal["timeout", "rate_limit", "temporary", "not_found", "invalid", "version_conflict"]
        | None,
        Header(alias="X-Mock-Failure"),
    ] = None,
) -> None:
    del scope
    if failure is None:
        return
    if not settings.failure_simulation_enabled or settings.app_env == "production":
        raise CommerceError(403, "forbidden", "Failure simulation is disabled.")
    if failure == "timeout":
        await asyncio.sleep(settings.simulated_timeout_seconds)
        raise CommerceError(504, "timeout", "The simulated request timed out.", retryable=True)
    if failure == "rate_limit":
        raise CommerceError(
            429,
            "rate_limited",
            "The simulated rate limit was reached.",
            retryable=True,
            retry_after=2,
        )
    if failure == "temporary":
        raise CommerceError(
            503, "unavailable", "The simulated service is unavailable.", retryable=True
        )
    if failure == "not_found":
        raise CommerceError(404, "not_found", "The requested order was not found.")
    if failure == "invalid":
        raise CommerceError(422, "validation", "The simulated request is invalid.")
    raise CommerceError(409, "version_conflict", "The simulated resource version is stale.")


FailureDependency = Annotated[None, Depends(simulate_failure)]


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    store = CommerceStore(resolved.database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        store.initialize()
        yield

    app = FastAPI(
        title="NovaCart Mock Commerce API",
        version="1.0.0",
        summary="Synthetic external commerce platform for adapter development",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.dependency_overrides[settings_dependency] = lambda: resolved
    app.dependency_overrides[store_dependency] = lambda: store
    register_handlers(app)

    @app.get("/", tags=["health"])
    async def root() -> dict[str, str]:
        return {"name": "NovaCart Mock Commerce API", "version": "v1"}

    @app.get("/health/live", tags=["health"])
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    async def ready() -> dict[str, str]:
        try:
            store.initialize()
        except OSError as exc:
            raise CommerceError(
                503, "not_ready", "Commerce storage is unavailable.", retryable=True
            ) from exc
        return {"status": "ready", "storage": "available"}

    common_responses: dict[int | str, dict[str, Any]] = {
        401: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
        504: {"model": ErrorResponse},
    }

    @app.get("/v1/orders", response_model=OrderPage, responses=common_responses)
    async def list_orders(
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
        limit: int = Query(default=5, ge=1, le=50),
        cursor: str | None = None,
    ) -> OrderPage:
        del failure_check
        orders, next_cursor = database.list_orders(
            scope.organization_id, scope.customer_ref, limit, cursor
        )
        return OrderPage(
            items=[OrderSummary.model_validate(item) for item in orders],
            next_cursor=next_cursor,
        )

    @app.get("/v1/orders/{order_ref}", response_model=Order, responses=common_responses)
    async def get_order(
        order_ref: str,
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
    ) -> Order:
        del failure_check
        return database.get_order(scope.organization_id, scope.customer_ref, order_ref)

    @app.get("/v1/orders/{order_ref}/fulfillment", responses=common_responses)
    async def fulfillment(
        order_ref: str,
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
    ) -> dict[str, object]:
        del failure_check
        order = database.get_order(scope.organization_id, scope.customer_ref, order_ref)
        return {
            "order_ref": order.external_ref,
            "status": order.fulfillment_status,
            "version": order.version,
        }

    @app.get("/v1/orders/{order_ref}/tracking", response_model=Tracking, responses=common_responses)
    async def get_tracking(
        order_ref: str,
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
    ) -> Tracking:
        del failure_check
        order = database.get_order(scope.organization_id, scope.customer_ref, order_ref)
        if order.tracking is None:
            raise CommerceError(404, "not_found", "Tracking is not available for this order.")
        return order.tracking

    @app.get(
        "/v1/orders/{order_ref}/shipping-address",
        response_model=Address,
        responses=common_responses,
    )
    async def shipping_address(
        order_ref: str,
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
    ) -> Address:
        del failure_check
        return database.get_order(
            scope.organization_id, scope.customer_ref, order_ref
        ).shipping_address

    @app.get(
        "/v1/orders/{order_ref}/returns",
        response_model=list[RefundRequest],
        responses=common_responses,
    )
    async def returns(
        order_ref: str,
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
    ) -> list[RefundRequest]:
        del failure_check
        return database.get_order(
            scope.organization_id, scope.customer_ref, order_ref
        ).refund_requests

    @app.patch(
        "/v1/orders/{order_ref}/shipping-address", response_model=Order, responses=common_responses
    )
    async def update_shipping_address(
        order_ref: str,
        command: AddressUpdate,
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        if_match: Annotated[int | None, Header(alias="If-Match")] = None,
    ) -> Order:
        del failure_check
        if not idempotency_key or len(idempotency_key) > 200 or if_match is None:
            raise CommerceError(422, "validation", "Idempotency-Key and If-Match are required.")
        return database.update_address(
            scope.organization_id, scope.customer_ref, order_ref, command, if_match, idempotency_key
        )

    @app.post(
        "/v1/orders/{order_ref}/refund-requests",
        response_model=Order,
        status_code=201,
        responses=common_responses,
    )
    async def create_refund_request(
        order_ref: str,
        command: RefundCreate,
        scope: ScopeDependency,
        database: StoreDependency,
        failure_check: FailureDependency,
        response: Response,
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
        if_match: Annotated[int | None, Header(alias="If-Match")] = None,
    ) -> Order:
        del failure_check
        if not idempotency_key or len(idempotency_key) > 200 or if_match is None:
            raise CommerceError(422, "validation", "Idempotency-Key and If-Match are required.")
        result = database.create_refund(
            scope.organization_id, scope.customer_ref, order_ref, command, if_match, idempotency_key
        )
        response.headers["ETag"] = str(result.version)
        return result

    return app
