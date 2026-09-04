from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.providers.errors import ProviderError
from app.providers.factory import create_commerce_provider, create_crm_provider
from app.providers.http import ProviderHttpClient
from app.providers.hubspot import HubSpotAdapter
from app.providers.mock_commerce import MockCommerceAdapter
from app.providers.mock_crm import MockCrmAdapter
from app.providers.models import (
    Address,
    ContactUpsert,
    Money,
    ProviderContext,
    ProviderErrorCode,
    SalesLeadUpsert,
)
from app.providers.shopify import ShopifyAdapter

ORG = UUID("10000000-0000-0000-0000-000000000001")


def context(*, write: bool = False) -> ProviderContext:
    return ProviderContext(
        organization_id=ORG,
        actor_ref="customer-1",
        customer_ref="gid://shopify/Customer/1",
        correlation_id="correlation-001",
        idempotency_key="idempotency-001" if write else None,
    )


def order_payload() -> dict[str, Any]:
    money = {"amount": "10.00", "currency": "USD"}
    return {
        "external_ref": "order-1",
        "order_number": "NC-1001",
        "version": 1,
        "status": "open",
        "fulfillment_status": "unfulfilled",
        "placed_at": "2026-01-01T00:00:00Z",
        "delivered_at": None,
        "line_items": [
            {
                "sku": "SKU-1",
                "name": "Cable",
                "quantity": 1,
                "unit_price": money,
                "final_sale": False,
                "fulfillment_status": "unfulfilled",
            }
        ],
        "subtotal": money,
        "shipping": money,
        "tax": money,
        "total": money,
        "shipping_address": {
            "recipient": "A Customer",
            "line1": "1 Main St",
            "line2": None,
            "city": "Boston",
            "region": "MA",
            "postal_code": "02101",
            "country_code": "US",
        },
        "tracking": {
            "carrier": "UPS",
            "tracking_number": "1Z",
            "tracking_url": "https://example.test/1Z",
            "events": [],
        },
        "refund_requests": [
            {
                "external_ref": "refund-1",
                "status": "requested",
                "requested_at": "2026-01-02T00:00:00Z",
                "amount": money,
                "reason": "Duplicate order",
            }
        ],
    }


def shopify_order() -> dict[str, Any]:
    money = {"shopMoney": {"amount": "10.00", "currencyCode": "USD"}}
    return {
        "id": "gid://shopify/Order/1",
        "name": "#1001",
        "updatedAt": "2026-01-02T00:00:00Z",
        "createdAt": "2026-01-01T00:00:00Z",
        "displayFinancialStatus": "PAID",
        "displayFulfillmentStatus": "UNFULFILLED",
        "cancelledAt": None,
        "customer": {"id": "gid://shopify/Customer/1"},
        "shippingAddress": {
            "name": "A Customer",
            "address1": "1 Main St",
            "address2": None,
            "city": "Boston",
            "provinceCode": "MA",
            "zip": "02101",
            "countryCodeV2": "US",
        },
        "currentSubtotalPriceSet": money,
        "currentShippingPriceSet": money,
        "currentTotalTaxSet": money,
        "currentTotalPriceSet": money,
        "lineItems": {
            "nodes": [
                {
                    "name": "Cable",
                    "quantity": 1,
                    "sku": "SKU-1",
                    "originalUnitPriceSet": money,
                    "fulfillmentStatus": "UNFULFILLED",
                }
            ]
        },
        "fulfillments": [
            {
                "createdAt": "2026-01-02T00:00:00Z",
                "status": "SUCCESS",
                "trackingInfo": [
                    {"company": "UPS", "number": "1Z", "url": "https://example.test/1Z"}
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_mock_commerce_normalization_writes_and_headers() -> None:
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/tracking"):
            return httpx.Response(200, json=order_payload()["tracking"])
        return httpx.Response(200 if request.method != "POST" else 201, json=order_payload())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://mock")
    adapter = MockCommerceAdapter(
        ProviderHttpClient(
            base_url="http://mock",
            timeout=1,
            retries=0,
            headers={"X-Internal-API-Key": "secret"},
            client=client,
        )
    )
    assert (await adapter.get_order(context(), "order-1")).version == "1"
    assert (await adapter.get_tracking(context(), "order-1")).carrier == "UPS"
    address = Address.model_validate(order_payload()["shipping_address"])
    assert (
        await adapter.update_address(context(write=True), "order-1", address, "1")
    ).external_ref == "order-1"
    refund = await adapter.create_refund_request(
        context(write=True),
        "order-1",
        Money(amount=Decimal("10"), currency="USD"),
        "Duplicate order",
        "1",
    )
    assert refund.external_ref == "refund-1"
    assert seen[-1].headers["idempotency-key"] == "idempotency-001"
    assert seen[-1].headers["if-match"] == "1"
    await client.aclose()


@pytest.mark.asyncio
async def test_http_error_mapping_retries_and_timeout() -> None:
    calls = 0

    async def retry_handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(retry_handler), base_url="http://test")
    http = ProviderHttpClient(
        base_url="http://test", timeout=1, retries=1, headers={}, client=client
    )
    assert (await http.request("GET", "/retry")).status_code == 200 and calls == 2

    async def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"Retry-After": "3"}, json={"error": {"code": "rate_limited"}}
        )

    limited_client = httpx.AsyncClient(
        transport=httpx.MockTransport(limited), base_url="http://test"
    )
    with pytest.raises(ProviderError) as error:
        await ProviderHttpClient(
            base_url="http://test", timeout=1, retries=0, headers={}, client=limited_client
        ).request("GET", "/")
    assert error.value.code == ProviderErrorCode.RATE_LIMITED and error.value.retry_after == 3

    async def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("safe", request=request)

    timeout_client = httpx.AsyncClient(
        transport=httpx.MockTransport(timeout), base_url="http://test"
    )
    with pytest.raises(ProviderError, match="timeout"):
        await ProviderHttpClient(
            base_url="http://test", timeout=1, retries=0, headers={}, client=timeout_client
        ).request("GET", "/")
    await client.aclose()
    await limited_client.aclose()
    await timeout_client.aclose()


@pytest.mark.asyncio
async def test_provider_validation_and_unavailable_branches() -> None:
    missing_customer = context().model_copy(update={"customer_ref": None})
    adapter = MockCommerceAdapter(
        ProviderHttpClient(base_url="http://unused", timeout=1, retries=0, headers={})
    )
    with pytest.raises(ProviderError, match="validation"):
        await adapter.get_order(missing_customer, "order-1")
    with pytest.raises(ProviderError, match="validation"):
        await adapter.update_address(
            context(), "order-1", Address.model_validate(order_payload()["shipping_address"]), "1"
        )
    await adapter.http.close()

    async def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"not-json")

    broken_client = httpx.AsyncClient(transport=httpx.MockTransport(broken), base_url="http://test")
    with pytest.raises(ProviderError) as unavailable:
        await ProviderHttpClient(
            base_url="http://test", timeout=1, retries=0, headers={}, client=broken_client
        ).request("POST", "/")
    assert unavailable.value.code == ProviderErrorCode.UNAVAILABLE
    await broken_client.aclose()


@pytest.mark.asyncio
async def test_shopify_contract_ownership_update_and_safe_refund() -> None:
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = request.read().decode()
        key = "orderUpdate" if "orderUpdate" in body else "order"
        return httpx.Response(
            200,
            json={
                "data": {
                    key: {"order": shopify_order(), "userErrors": []}
                    if key == "orderUpdate"
                    else shopify_order()
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://shop.test")
    adapter = ShopifyAdapter(
        ProviderHttpClient(
            base_url="https://shop.test",
            timeout=1,
            retries=1,
            headers={"X-Shopify-Access-Token": "redacted"},
            client=client,
        )
    )
    order = await adapter.get_order(context(), "gid://shopify/Order/1")
    assert (
        order.total.currency == "USD"
        and (await adapter.get_tracking(context(), order.external_ref)).carrier == "UPS"
    )
    address = Address.model_validate(order_payload()["shipping_address"])
    assert (
        await adapter.update_address(
            context(write=True), order.external_ref, address, order.version
        )
    ).order_number == "#1001"
    with pytest.raises(ProviderError, match="unsupported"):
        await adapter.create_refund_request(
            context(write=True), order.external_ref, order.total, "reason", order.version
        )
    denied = context().model_copy(update={"customer_ref": "gid://shopify/Customer/other"})
    with pytest.raises(ProviderError, match="not_found"):
        await adapter.get_order(denied, order.external_ref)
    assert calls >= 4
    await client.aclose()


@pytest.mark.asyncio
async def test_shopify_conflict_graphql_error_and_no_tracking() -> None:
    raw = shopify_order()
    raw["fulfillments"] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"order": raw}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://shop.test")
    adapter = ShopifyAdapter(
        ProviderHttpClient(
            base_url="https://shop.test", timeout=1, retries=0, headers={}, client=client
        )
    )
    with pytest.raises(ProviderError, match="not_found"):
        await adapter.get_tracking(context(), raw["id"])
    with pytest.raises(ProviderError, match="conflict"):
        await adapter.update_address(
            context(write=True),
            raw["id"],
            Address.model_validate(order_payload()["shipping_address"]),
            "stale-version",
        )
    await client.aclose()

    async def graphql_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errors": [{"message": "sanitized"}]})

    error_client = httpx.AsyncClient(
        transport=httpx.MockTransport(graphql_error), base_url="https://shop.test"
    )
    failing = ShopifyAdapter(
        ProviderHttpClient(
            base_url="https://shop.test", timeout=1, retries=0, headers={}, client=error_client
        )
    )
    with pytest.raises(ProviderError, match="unavailable"):
        await failing.get_order(context(), raw["id"])
    await error_client.aclose()


@pytest.mark.asyncio
async def test_mock_and_hubspot_crm_normalized_contract() -> None:
    now = datetime.now(timezone.utc).isoformat()

    async def mock_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(204)
        if request.url.path.endswith("notes"):
            return httpx.Response(
                200,
                json={
                    "external_ref": "note-1",
                    "contact_ref": "contact-1",
                    "body": "Summary",
                    "created_at": now,
                },
            )
        data = request.read()
        return httpx.Response(
            200,
            json={
                **__import__("json").loads(data),
                "external_ref": "contact-1",
                "provider_status": "active",
                "version": "1",
                "created_at": now,
                "updated_at": now,
            },
        )

    mock_client = httpx.AsyncClient(
        transport=httpx.MockTransport(mock_handler), base_url="http://mock"
    )
    mock = MockCrmAdapter(
        ProviderHttpClient(
            base_url="http://mock", timeout=1, retries=0, headers={}, client=mock_client
        )
    )
    assert await mock.find_contact(context(), "none@example.test") is None
    lead = ContactUpsert(email="lead@example.test", first_name="Lead", last_name="Person")
    with pytest.raises(ProviderError, match="validation"):
        await mock.upsert_contact(context(), lead)
    with pytest.raises(ProviderError, match="validation"):
        await mock.create_conversation_note(context(), "contact-1", "Summary")
    assert (await mock.upsert_contact(context(write=True), lead)).external_ref == "contact-1"
    assert (
        await mock.create_conversation_note(context(write=True), "contact-1", "Summary")
    ).external_ref == "note-1"
    sales = SalesLeadUpsert(
        contact_ref="contact-1",
        interest="Enterprise API",
        business_need="Scale support",
        preferred_contact_method="email",
    )
    assert await mock.find_lead(context(), "contact-1") is None
    with pytest.raises(ProviderError, match="validation"):
        await mock.upsert_lead(context(), sales)
    assert (await mock.upsert_lead(context(write=True), sales)).external_ref == "contact-1"

    async def hub_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("search"):
            return httpx.Response(200, json={"results": []})
        if "/leads/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "lead-1",
                            "createdAt": now,
                            "updatedAt": now,
                            "archived": False,
                            "properties": {
                                "hs_associated_contact_id": "contact-1",
                                "novacart_interest": "Enterprise API",
                                "novacart_business_need": "Scale support",
                                "novacart_preferred_contact_method": "email",
                            },
                        }
                    ]
                },
            )
        if request.url.path.endswith("upsert"):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "contact-1",
                            "createdAt": now,
                            "updatedAt": now,
                            "archived": False,
                            "properties": {
                                "email": "lead@example.test",
                                "firstname": "Lead",
                                "lastname": "Person",
                                "lifecyclestage": "lead",
                            },
                        }
                    ]
                },
            )
        return httpx.Response(201, json={"id": "note-1"})

    hub_client = httpx.AsyncClient(
        transport=httpx.MockTransport(hub_handler), base_url="https://api.hubapi.com"
    )
    hub = HubSpotAdapter(
        ProviderHttpClient(
            base_url="https://api.hubapi.com",
            timeout=1,
            retries=0,
            headers={"Authorization": "Bearer redacted"},
            client=hub_client,
        )
    )
    assert await hub.find_contact(context(), "none@example.test") is None
    assert (await hub.upsert_contact(context(write=True), lead)).external_ref == "contact-1"
    assert (
        await hub.create_conversation_note(context(write=True), "contact-1", "Summary")
    ).external_ref == "note-1"
    assert await hub.find_lead(context(), "contact-1") is None
    with pytest.raises(ProviderError, match="validation"):
        await hub.upsert_lead(context(), sales)
    assert (await hub.upsert_lead(context(write=True), sales)).external_ref == "lead-1"
    await mock_client.aclose()
    await hub_client.aclose()


def test_provider_factories_and_credential_gates() -> None:
    mock = Settings(app_env="test", embedding_provider="fake", reranker_provider="deterministic")
    assert isinstance(create_commerce_provider(mock), MockCommerceAdapter)
    assert isinstance(create_crm_provider(mock), MockCrmAdapter)
    integration = Settings(
        app_env="test",
        embedding_provider="fake",
        reranker_provider="deterministic",
        commerce_provider="shopify",
        crm_provider="hubspot",
        shopify_store_domain="shop.test",
        shopify_access_token=SecretStr("shop-token"),
        hubspot_access_token=SecretStr("hub-token"),
    )
    assert isinstance(create_commerce_provider(integration), ShopifyAdapter)
    assert isinstance(create_crm_provider(integration), HubSpotAdapter)
    with pytest.raises(ValidationError, match="Shopify mode requires"):
        Settings(app_env="test", commerce_provider="shopify")
    with pytest.raises(ValidationError, match="HubSpot mode requires"):
        Settings(app_env="test", crm_provider="hubspot")
