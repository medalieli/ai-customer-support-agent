from typing import Any

from app.providers.errors import ProviderError
from app.providers.http import ProviderHttpClient
from app.providers.models import (
    Address,
    Money,
    Order,
    ProviderContext,
    ProviderErrorCode,
    RefundRequest,
    Tracking,
)
from app.providers.order_numbers import InvalidOrderNumber, normalize_order_number


class MockCommerceAdapter:
    def __init__(self, http: ProviderHttpClient) -> None:
        self.http = http

    @staticmethod
    def _headers(
        context: ProviderContext, *, write: bool = False, version: str = ""
    ) -> dict[str, str]:
        if not context.customer_ref:
            raise ProviderError(ProviderErrorCode.VALIDATION)
        headers = {
            "X-Organization-Id": str(context.organization_id),
            "X-External-Customer-Id": context.customer_ref,
            "X-Correlation-Id": context.correlation_id,
        }
        if write:
            if not context.idempotency_key:
                raise ProviderError(ProviderErrorCode.VALIDATION)
            headers.update({"Idempotency-Key": context.idempotency_key, "If-Match": version})
        return headers

    async def get_order(self, context: ProviderContext, order_ref: str) -> Order:
        response = await self.http.request(
            "GET", f"/v1/orders/{order_ref}", headers=self._headers(context)
        )
        return self._order(response.json())

    async def get_tracking(self, context: ProviderContext, order_ref: str) -> Tracking:
        response = await self.http.request(
            "GET", f"/v1/orders/{order_ref}/tracking", headers=self._headers(context)
        )
        return Tracking.model_validate(response.json())

    async def update_address(
        self, context: ProviderContext, order_ref: str, address: Address, version: str
    ) -> Order:
        response = await self.http.request(
            "PATCH",
            f"/v1/orders/{order_ref}/shipping-address",
            headers=self._headers(context, write=True, version=version),
            json={"address": address.model_dump(mode="json")},
        )
        return self._order(response.json())

    async def create_refund_request(
        self, context: ProviderContext, order_ref: str, amount: Money, reason: str, version: str
    ) -> RefundRequest:
        response = await self.http.request(
            "POST",
            f"/v1/orders/{order_ref}/refund-requests",
            headers=self._headers(context, write=True, version=version),
            json={"amount": amount.model_dump(mode="json"), "reason": reason},
        )
        order = self._order(response.json())
        if not order.refund_requests:
            raise ProviderError(ProviderErrorCode.UNAVAILABLE)
        return order.refund_requests[-1]

    @staticmethod
    def _order(data: dict[str, Any]) -> Order:
        data["version"] = str(data["version"])
        data["provider_status"] = data["status"]
        return Order.model_validate(data)

    async def resolve_order(self, context: ProviderContext, order_number: str) -> Order:
        try:
            wanted = normalize_order_number(order_number)
        except InvalidOrderNumber as exc:
            raise ProviderError(ProviderErrorCode.VALIDATION) from exc
        matches: list[str] = []
        cursor: str | None = None
        while True:
            response = await self.http.request(
                "GET",
                "/v1/orders",
                headers=self._headers(context),
                params={"limit": 50, **({"cursor": cursor} if cursor else {})},
            )
            payload = response.json()
            for item in payload.get("items", []):
                try:
                    if normalize_order_number(str(item["order_number"])) == wanted:
                        matches.append(str(item["external_ref"]))
                except (InvalidOrderNumber, KeyError):
                    continue
            cursor = payload.get("next_cursor")
            if not cursor:
                break
        if not matches:
            raise ProviderError(ProviderErrorCode.NOT_FOUND)
        if len(matches) != 1:
            raise ProviderError(ProviderErrorCode.CONFLICT)
        return await self.get_order(context, matches[0])
