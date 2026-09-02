from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

NOVACART = "10000000-0000-0000-0000-000000000001"
ORBIT = "20000000-0000-0000-0000-000000000001"
AMIRA = "seed-amira-en"
LUCAS = "seed-lucas-fr"
NORA = "seed-nora-en"


def money(amount: str, currency: str = "USD") -> dict[str, str]:
    return {"amount": str(Decimal(amount).quantize(Decimal("0.01"))), "currency": currency}


def address(name: str, city: str = "Boston", country: str = "US") -> dict[str, Any]:
    return {
        "recipient": name,
        "line1": "42 Synthetic Avenue",
        "line2": None,
        "city": city,
        "region": "MA" if country == "US" else "QC",
        "postal_code": "02110" if country == "US" else "H2Y 1C6",
        "country_code": country,
    }


def item(
    sku: str,
    name: str,
    price: str,
    fulfillment: str,
    *,
    quantity: int = 1,
    final_sale: bool = False,
) -> dict[str, Any]:
    return {
        "sku": sku,
        "name": name,
        "quantity": quantity,
        "unit_price": money(price),
        "final_sale": final_sale,
        "fulfillment_status": fulfillment,
    }


def tracking(status: str, delayed: bool = False) -> dict[str, Any]:
    events = [
        {
            "status": "label_created",
            "occurred_at": "2026-08-25T09:00:00Z",
            "location": "Newark, NJ",
            "detail": "Carrier received shipment information",
        },
        {
            "status": status,
            "occurred_at": "2026-08-27T16:30:00Z",
            "location": "Boston, MA",
            "detail": "Weather delay" if delayed else "In transit",
        },
    ]
    return {
        "carrier": "NovaPost",
        "tracking_number": f"NP-{status.upper()}-2026",
        "tracking_url": f"https://tracking.invalid/{status}",
        "estimated_delivery_at": "2026-09-04T20:00:00Z",
        "events": events,
    }


def order(
    ref: str,
    number: str,
    scenario: str,
    fulfillment: str,
    product: dict[str, Any],
    *,
    status: str = "open",
    delivered_at: str | None = None,
    tracking_data: dict[str, Any] | None = None,
    customer_name: str = "Amira Haddad",
) -> dict[str, Any]:
    subtotal = Decimal(product["unit_price"]["amount"]) * product["quantity"]
    shipping_cost = Decimal("8.00")
    tax = (subtotal * Decimal("0.08")).quantize(Decimal("0.01"))
    return {
        "external_ref": ref,
        "order_number": number,
        "version": 1,
        "status": status,
        "fulfillment_status": fulfillment,
        "placed_at": "2026-08-20T14:15:00Z",
        "delivered_at": delivered_at,
        "line_items": [product],
        "subtotal": money(str(subtotal)),
        "shipping": money(str(shipping_cost)),
        "tax": money(str(tax)),
        "total": money(str(subtotal + shipping_cost + tax)),
        "shipping_address": address(customer_name),
        "tracking": tracking_data,
        "refund_requests": [],
        "scenario": scenario,
    }


def seeded_orders() -> list[tuple[str, str, dict[str, Any]]]:
    rows = [
        (
            NOVACART,
            AMIRA,
            order(
                "ord-address",
                "NC-1001",
                "address_changeable",
                "unfulfilled",
                item("KB-75", "NovaKeys 75 Keyboard", "89.00", "unfulfilled"),
            ),
        ),
        (
            NOVACART,
            AMIRA,
            order(
                "ord-shipped",
                "NC-1002",
                "shipped_tracking",
                "shipped",
                item("HUB-8", "Orbit USB-C Hub", "59.00", "fulfilled"),
                tracking_data=tracking("in_transit"),
            ),
        ),
        (
            NOVACART,
            AMIRA,
            order(
                "ord-delayed",
                "NC-1003",
                "delayed",
                "delayed",
                item("CAM-4K", "NovaView 4K Webcam", "129.00", "fulfilled"),
                tracking_data=tracking("delayed", True),
            ),
        ),
        (
            NOVACART,
            AMIRA,
            order(
                "ord-recent",
                "NC-1004",
                "recently_delivered",
                "delivered",
                item("MSE-PRO", "NovaPoint Mouse", "69.00", "fulfilled"),
                delivered_at="2026-08-30T15:00:00Z",
                tracking_data=tracking("delivered"),
            ),
        ),
        (
            NOVACART,
            AMIRA,
            order(
                "ord-old",
                "NC-1005",
                "outside_return_window",
                "delivered",
                item("MON-27", "NovaPixel 27 Monitor", "329.00", "fulfilled"),
                delivered_at="2026-06-01T15:00:00Z",
                tracking_data=tracking("delivered"),
            ),
        ),
        (
            NOVACART,
            LUCAS,
            order(
                "ord-final",
                "NC-1006",
                "final_sale",
                "delivered",
                item("CASE-RED", "Limited Red Phone Case", "19.00", "fulfilled", final_sale=True),
                delivered_at="2026-08-29T10:00:00Z",
                customer_name="Lucas Martin",
            ),
        ),
        (
            NOVACART,
            LUCAS,
            order(
                "ord-partial",
                "NC-1007",
                "partially_fulfilled",
                "partially_fulfilled",
                item("CABLE-2M", "Braided USB-C Cable", "18.00", "fulfilled", quantity=2),
                customer_name="Lucas Martin",
            ),
        ),
        (
            NOVACART,
            LUCAS,
            order(
                "ord-cancelled",
                "NC-1008",
                "cancelled",
                "cancelled",
                item("PAD-XL", "NovaDesk XL Mat", "35.00", "cancelled"),
                status="cancelled",
                customer_name="Lucas Martin",
            ),
        ),
        (
            NOVACART,
            LUCAS,
            order(
                "ord-refunded",
                "NC-1009",
                "already_refunded",
                "delivered",
                item("EAR-BUD", "NovaSound Earbuds", "99.00", "fulfilled"),
                status="refunded",
                delivered_at="2026-08-22T12:00:00Z",
                customer_name="Lucas Martin",
            ),
        ),
        (
            ORBIT,
            NORA,
            order(
                "ord-orbit-lookalike",
                "NC-1001",
                "tenant_isolation",
                "unfulfilled",
                item("KB-75", "NovaKeys 75 Keyboard", "89.00", "unfulfilled"),
                customer_name="Nora Silva",
            ),
        ),
    ]
    refunded = rows[8][2]
    refunded["refund_requests"] = [
        {
            "external_ref": "refund-existing",
            "status": "refunded",
            "requested_at": "2026-08-23T09:00:00Z",
            "amount": money("99.00"),
            "reason": "Synthetic completed refund",
        }
    ]
    partial = rows[6][2]
    partial["line_items"].append(
        item("CHARGER-65", "NovaCharge 65W Adapter", "49.00", "unfulfilled")
    )
    partial["subtotal"] = money("85.00")
    partial["tax"] = money("6.80")
    partial["total"] = money("99.80")
    return deepcopy(rows)


SEEDED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc).isoformat()
