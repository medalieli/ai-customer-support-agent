from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.main import create_app
from app.schemas import AddressUpdate
from app.seed import AMIRA, LUCAS, NORA, NOVACART, ORBIT
from app.store import CommerceStore

KEY = "synthetic-internal-key-12345"


def headers(customer: str = AMIRA, organization: str = NOVACART, key: str = KEY) -> dict[str, str]:
    return {
        "X-Internal-API-Key": key,
        "X-Organization-Id": organization,
        "X-External-Customer-Id": customer,
    }


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[AsyncClient]:
    settings = Settings(
        app_env="test",
        database_path=str(tmp_path / "commerce.db"),
        internal_api_key=SecretStr(KEY),
        failure_simulation_enabled=True,
        simulated_timeout_seconds=0,
    )
    CommerceStore(settings.database_path).initialize()
    app = create_app(settings)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://commerce"
    ) as value:
        yield value


@pytest.mark.asyncio
async def test_health_openapi_and_authentication(client: AsyncClient) -> None:
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/health/live")).json() == {"status": "alive"}
    assert (await client.get("/health/ready")).status_code == 200
    schema = (await client.get("/openapi.json")).json()
    assert schema["info"]["title"] == "NovaCart Mock Commerce API"
    denied = await client.get("/v1/orders")
    assert denied.status_code == 401
    assert KEY not in denied.text
    wrong = await client.get("/v1/orders", headers=headers(key="wrong-key-value-123456"))
    assert wrong.status_code == 401
    missing_scope = await client.get("/v1/orders", headers={"X-Internal-API-Key": KEY})
    assert missing_scope.status_code == 401
    invalid_scope = await client.get("/v1/orders", headers=headers(organization="bad"))
    assert invalid_scope.status_code == 422


@pytest.mark.asyncio
async def test_seed_scenarios_listing_pagination_and_isolation(client: AsyncClient) -> None:
    first = await client.get("/v1/orders", headers=headers(), params={"limit": 2})
    assert first.status_code == 200
    assert len(first.json()["items"]) == 2
    assert first.json()["next_cursor"] == "2"
    second = await client.get(
        "/v1/orders",
        headers=headers(),
        params={"limit": 50, "cursor": first.json()["next_cursor"]},
    )
    assert second.status_code == 200
    assert second.json()["next_cursor"] is None
    assert len(first.json()["items"] + second.json()["items"]) == 5
    invalid_cursor = await client.get("/v1/orders", headers=headers(), params={"cursor": "x"})
    assert invalid_cursor.status_code == 422

    lucas = await client.get("/v1/orders", headers=headers(LUCAS), params={"limit": 50})
    assert len(lucas.json()["items"]) == 4
    all_orders = first.json()["items"] + second.json()["items"] + lucas.json()["items"]
    statuses = {(item["status"], item["fulfillment_status"]) for item in all_orders}
    assert ("open", "unfulfilled") in statuses
    assert ("open", "shipped") in statuses
    assert ("open", "delayed") in statuses
    assert ("open", "delivered") in statuses
    assert ("open", "partially_fulfilled") in statuses
    assert ("cancelled", "cancelled") in statuses
    assert ("refunded", "delivered") in statuses

    cross_customer = await client.get("/v1/orders/ord-address", headers=headers(LUCAS))
    cross_tenant = await client.get("/v1/orders/ord-address", headers=headers(NORA, ORBIT))
    assert cross_customer.status_code == cross_tenant.status_code == 404
    lookalike = await client.get("/v1/orders/ord-orbit-lookalike", headers=headers(NORA, ORBIT))
    assert lookalike.status_code == 200
    assert lookalike.json()["order_number"] == "NC-1001"


@pytest.mark.asyncio
async def test_fulfillment_tracking_addresses_and_return_status(client: AsyncClient) -> None:
    fulfillment = await client.get("/v1/orders/ord-delayed/fulfillment", headers=headers())
    assert fulfillment.json()["status"] == "delayed"
    tracking = await client.get("/v1/orders/ord-shipped/tracking", headers=headers())
    assert tracking.status_code == 200
    assert tracking.json()["carrier"] == "NovaPost"
    no_tracking = await client.get("/v1/orders/ord-address/tracking", headers=headers())
    assert no_tracking.status_code == 404
    current_address = await client.get("/v1/orders/ord-address/shipping-address", headers=headers())
    assert current_address.json()["city"] == "Boston"
    returns = await client.get("/v1/orders/ord-refunded/returns", headers=headers(LUCAS))
    assert returns.json()[0]["status"] == "refunded"
    final_sale = await client.get("/v1/orders/ord-final", headers=headers(LUCAS))
    assert final_sale.json()["line_items"][0]["final_sale"] is True
    old = await client.get("/v1/orders/ord-old", headers=headers())
    recent = await client.get("/v1/orders/ord-recent", headers=headers())
    assert old.json()["delivered_at"] < recent.json()["delivered_at"]


def address_body(city: str = "Cambridge") -> dict[str, object]:
    return {
        "address": {
            "recipient": "Amira Haddad",
            "line1": "88 Test Harbor Road",
            "line2": "Unit 4",
            "city": city,
            "region": "MA",
            "postal_code": "02139",
            "country_code": "US",
        }
    }


@pytest.mark.asyncio
async def test_address_update_idempotency_version_and_state_restrictions(
    client: AsyncClient,
) -> None:
    write_headers = headers() | {"Idempotency-Key": "address-key-1", "If-Match": "1"}
    updated = await client.patch(
        "/v1/orders/ord-address/shipping-address",
        headers=write_headers,
        json=address_body(),
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["shipping_address"]["city"] == "Cambridge"
    replay = await client.patch(
        "/v1/orders/ord-address/shipping-address",
        headers=write_headers,
        json=address_body(),
    )
    assert replay.json() == updated.json()
    conflict = await client.patch(
        "/v1/orders/ord-address/shipping-address",
        headers=write_headers,
        json=address_body("Somerville"),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    stale = await client.patch(
        "/v1/orders/ord-address/shipping-address",
        headers=headers() | {"Idempotency-Key": "address-key-2", "If-Match": "1"},
        json=address_body(),
    )
    assert stale.json()["error"]["code"] == "version_conflict"
    shipped = await client.patch(
        "/v1/orders/ord-shipped/shipping-address",
        headers=headers() | {"Idempotency-Key": "address-key-3", "If-Match": "1"},
        json=address_body(),
    )
    assert shipped.json()["error"]["code"] == "order_state_conflict"
    missing_headers = await client.patch(
        "/v1/orders/ord-address/shipping-address", headers=headers(), json=address_body()
    )
    assert missing_headers.status_code == 422


@pytest.mark.asyncio
async def test_refund_request_idempotency_validation_and_restrictions(client: AsyncClient) -> None:
    write_headers = headers() | {"Idempotency-Key": "refund-key-1", "If-Match": "1"}
    body = {"amount": {"amount": "25.00", "currency": "USD"}, "reason": "Changed mind"}
    created = await client.post(
        "/v1/orders/ord-recent/refund-requests", headers=write_headers, json=body
    )
    assert created.status_code == 201
    assert created.headers["etag"] == "2"
    replay = await client.post(
        "/v1/orders/ord-recent/refund-requests", headers=write_headers, json=body
    )
    assert replay.json() == created.json()
    changed = body | {"reason": "Different request"}
    conflict = await client.post(
        "/v1/orders/ord-recent/refund-requests", headers=write_headers, json=changed
    )
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    invalid_amount = await client.post(
        "/v1/orders/ord-old/refund-requests",
        headers=headers() | {"Idempotency-Key": "refund-key-2", "If-Match": "1"},
        json={"amount": {"amount": "9999", "currency": "USD"}, "reason": "Too much"},
    )
    assert invalid_amount.status_code == 422
    cancelled = await client.post(
        "/v1/orders/ord-cancelled/refund-requests",
        headers=headers(LUCAS) | {"Idempotency-Key": "refund-key-3", "If-Match": "1"},
        json=body,
    )
    assert cancelled.json()["error"]["code"] == "order_state_conflict"
    refunded = await client.post(
        "/v1/orders/ord-refunded/refund-requests",
        headers=headers(LUCAS) | {"Idempotency-Key": "refund-key-4", "If-Match": "1"},
        json=body,
    )
    assert refunded.json()["error"]["code"] == "order_state_conflict"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        ("timeout", 504, "timeout"),
        ("rate_limit", 429, "rate_limited"),
        ("temporary", 503, "unavailable"),
        ("not_found", 404, "not_found"),
        ("invalid", 422, "validation"),
        ("version_conflict", 409, "version_conflict"),
    ],
)
async def test_authenticated_failure_simulation(
    client: AsyncClient, failure: str, status: int, code: str
) -> None:
    response = await client.get("/v1/orders", headers=headers() | {"X-Mock-Failure": failure})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response.json()["error"]["retryable"] is (status in {429, 503, 504})
    if failure == "rate_limit":
        assert response.headers["retry-after"] == "2"


def test_production_disables_failure_simulation() -> None:
    with pytest.raises(ValidationError, match="Failure simulation"):
        Settings(
            app_env="production",
            internal_api_key=SecretStr(KEY),
            failure_simulation_enabled=True,
        )
    with pytest.raises(ValidationError, match="at least 16"):
        Settings(app_env="test", internal_api_key=SecretStr("short"))


def test_write_persists_when_store_is_reopened(tmp_path: Path) -> None:
    path = str(tmp_path / "persistent.db")
    first_process = CommerceStore(path)
    first_process.initialize()
    updated = first_process.update_address(
        NOVACART,
        AMIRA,
        "ord-address",
        AddressUpdate.model_validate(address_body("Salem")),
        1,
        "restart-proof-key",
    )
    second_process = CommerceStore(path)
    assert second_process.get_order(NOVACART, AMIRA, "ord-address").version == updated.version
    replay = second_process.update_address(
        NOVACART,
        AMIRA,
        "ord-address",
        AddressUpdate.model_validate(address_body("Salem")),
        1,
        "restart-proof-key",
    )
    assert replay.shipping_address.city == "Salem"
