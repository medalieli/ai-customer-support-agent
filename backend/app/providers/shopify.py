from datetime import datetime
from decimal import Decimal
from typing import Any

from app.providers.errors import ProviderError
from app.providers.http import ProviderHttpClient
from app.providers.models import (
    Address,
    LineItem,
    Money,
    Order,
    ProviderContext,
    ProviderErrorCode,
    RefundRequest,
    Tracking,
    TrackingEvent,
)
from app.providers.order_numbers import InvalidOrderNumber, normalize_order_number

ORDER_FIELDS = """
id name updatedAt createdAt displayFinancialStatus displayFulfillmentStatus cancelledAt
customer { id } shippingAddress { name address1 address2 city provinceCode zip countryCodeV2 }
currentSubtotalPriceSet { shopMoney { amount currencyCode } }
currentShippingPriceSet { shopMoney { amount currencyCode } }
currentTotalTaxSet { shopMoney { amount currencyCode } }
currentTotalPriceSet { shopMoney { amount currencyCode } }
lineItems(first: 100) { nodes {
  name quantity sku originalUnitPriceSet { shopMoney { amount currencyCode } } fulfillmentStatus
} }
fulfillments { createdAt status trackingInfo { company number url } }
"""


class ShopifyAdapter:
    def __init__(self, http: ProviderHttpClient) -> None:
        self.http = http

    async def _graphql(
        self,
        query: str,
        variables: dict[str, Any],
        correlation_id: str,
        *,
        retry_safe: bool = False,
    ) -> dict[str, Any]:
        response = await self.http.request(
            "POST",
            "",
            retry_safe=retry_safe,
            headers={"X-Request-ID": correlation_id},
            json={"query": query, "variables": variables},
        )
        payload: dict[str, Any] = response.json()
        if payload.get("errors"):
            raise ProviderError(ProviderErrorCode.UNAVAILABLE, retryable=False)
        return dict(payload.get("data") or {})

    async def get_order(self, context: ProviderContext, order_ref: str) -> Order:
        data = await self._graphql(
            f"query Order($id: ID!) {{ order(id: $id) {{ {ORDER_FIELDS} }} }}",
            {"id": order_ref},
            context.correlation_id,
            retry_safe=True,
        )
        raw = data.get("order")
        if (
            not raw
            or not context.customer_ref
            or raw.get("customer", {}).get("id") != context.customer_ref
        ):
            raise ProviderError(ProviderErrorCode.NOT_FOUND)
        return self._normalize_order(raw)

    async def get_tracking(self, context: ProviderContext, order_ref: str) -> Tracking:
        order = await self.get_order(context, order_ref)
        if order.tracking is None:
            raise ProviderError(ProviderErrorCode.NOT_FOUND)
        return order.tracking

    async def update_address(
        self, context: ProviderContext, order_ref: str, address: Address, version: str
    ) -> Order:
        if not context.idempotency_key:
            raise ProviderError(ProviderErrorCode.VALIDATION)
        current = await self.get_order(context, order_ref)
        if current.version != version:
            raise ProviderError(ProviderErrorCode.CONFLICT)
        mutation = f"""mutation Update($input: OrderInput!) {{ orderUpdate(input: $input) {{
          order {{ {ORDER_FIELDS} }} userErrors {{ field message }}
        }}}}"""
        data = await self._graphql(
            mutation,
            {
                "input": {
                    "id": order_ref,
                    "shippingAddress": {
                        "firstName": address.recipient,
                        "address1": address.line1,
                        "address2": address.line2,
                        "city": address.city,
                        "provinceCode": address.region,
                        "zip": address.postal_code,
                        "countryCode": address.country_code,
                    },
                }
            },
            context.correlation_id,
        )
        result = data.get("orderUpdate") or {}
        if result.get("userErrors"):
            raise ProviderError(ProviderErrorCode.VALIDATION)
        return self._normalize_order(result["order"])

    async def create_refund_request(
        self, context: ProviderContext, order_ref: str, amount: Money, reason: str, version: str
    ) -> RefundRequest:
        del context, order_ref, amount, reason, version
        # Shopify refundCreate moves money. M5 intentionally exposes no automatic real-money path.
        raise ProviderError(ProviderErrorCode.UNSUPPORTED)

    @staticmethod
    def _money(raw: dict[str, Any]) -> Money:
        value = raw["shopMoney"]
        return Money(amount=Decimal(value["amount"]), currency=value["currencyCode"])

    @classmethod
    def _normalize_order(cls, raw: dict[str, Any]) -> Order:
        fulfillments = raw.get("fulfillments") or []
        tracking: Tracking | None = None
        if fulfillments and fulfillments[-1].get("trackingInfo"):
            info = fulfillments[-1]["trackingInfo"][0]
            tracking = Tracking(
                carrier=info.get("company") or "unknown",
                tracking_number=info.get("number") or "",
                tracking_url=info.get("url") or "",
                events=[
                    TrackingEvent(
                        status=fulfillments[-1].get("status") or "unknown",
                        occurred_at=fulfillments[-1]["createdAt"],
                    )
                ],
            )
        address = raw.get("shippingAddress") or {}
        return Order(
            external_ref=raw["id"],
            order_number=raw["name"],
            version=raw["updatedAt"],
            status="cancelled" if raw.get("cancelledAt") else "open",
            fulfillment_status=str(raw.get("displayFulfillmentStatus") or "unfulfilled").lower(),
            provider_status=raw.get("displayFinancialStatus"),
            placed_at=datetime.fromisoformat(raw["createdAt"].replace("Z", "+00:00")),
            line_items=[
                LineItem(
                    sku=item.get("sku") or "unknown",
                    name=item["name"],
                    quantity=item["quantity"],
                    unit_price=cls._money(item["originalUnitPriceSet"]),
                    fulfillment_status=str(item.get("fulfillmentStatus") or "unfulfilled").lower(),
                )
                for item in raw["lineItems"]["nodes"]
            ],
            subtotal=cls._money(raw["currentSubtotalPriceSet"]),
            shipping=cls._money(raw["currentShippingPriceSet"]),
            tax=cls._money(raw["currentTotalTaxSet"]),
            total=cls._money(raw["currentTotalPriceSet"]),
            shipping_address=Address(
                recipient=address.get("name") or "Customer",
                line1=address.get("address1") or "",
                line2=address.get("address2"),
                city=address.get("city") or "",
                region=address.get("provinceCode") or "",
                postal_code=address.get("zip") or "",
                country_code=address.get("countryCodeV2") or "US",
            ),
            tracking=tracking,
        )

    async def resolve_order(self, context: ProviderContext, order_number: str) -> Order:
        try:
            normalized = normalize_order_number(order_number)
        except InvalidOrderNumber as exc:
            raise ProviderError(ProviderErrorCode.VALIDATION) from exc
        query = (
            "query Orders($query: String!) { orders(first: 2, query: $query) "
            f"{{ nodes {{ {ORDER_FIELDS} }} }} }}"
        )
        data = await self._graphql(
            query,
            {"query": f'name:"#{normalized.removeprefix("NC-")}" OR name:"{normalized}"'},
            context.correlation_id,
            retry_safe=True,
        )
        matches = [
            raw
            for raw in (data.get("orders", {}).get("nodes") or [])
            if context.customer_ref
            and raw.get("customer", {}).get("id") == context.customer_ref
            and normalize_order_number(str(raw.get("name", "")).replace("#", "")) == normalized
        ]
        if not matches:
            raise ProviderError(ProviderErrorCode.NOT_FOUND)
        if len(matches) != 1:
            raise ProviderError(ProviderErrorCode.CONFLICT)
        return self._normalize_order(matches[0])
