from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.agent.address_parser import extract_proposed_address, looks_like_address_change
from app.providers.models import Address, Money, Order
from app.services.address_actions import (
    AddressActionError,
    canonical_address,
    eligible,
    mask_address,
    validate_address,
)


def test_english_and_french_labelled_addresses_are_strictly_parsed() -> None:
    english = extract_proposed_address(
        "Change shipping address for NC-1001; recipient: Ada Test; "
        "line1: 10 New Street; city: Boston; region: MA; postal code: 02111; country: us"
    )
    assert english.country_code == "US"
    assert english.line2 is None
    french = extract_proposed_address(
        "Changer l'adresse de livraison NC-1001; destinataire: Jean Test; "
        "adresse: 8 rue Neuve; ville: Montréal; province: QC; "
        "code postal: H2Y 1C6; pays: ca"
    )
    assert french.city == "Montréal"
    assert french.country_code == "CA"
    assert looks_like_address_change("Changer mon adresse de livraison")


def test_json_address_parsing_and_canonicalization() -> None:
    value = extract_proposed_address(
        'change delivery address {"recipient":"Test User","line1":"1 Main",'
        '"line2":"  ","city":"Paris","region":"IDF",'
        '"postal_code":"75001","country_code":"fr"}'
    )
    assert value.line2 is None
    assert b'"country_code":"FR"' in canonical_address(value)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"recipient": "A", "line1": "B", "city": "C", "region": "D", "postal_code": "E"},
        {
            "recipient": "A",
            "line1": "B",
            "city": "C",
            "region": "D",
            "postal_code": "E",
            "country_code": "USA",
        },
    ],
)
def test_missing_or_invalid_address_is_rejected(value: object) -> None:
    with pytest.raises(AddressActionError):
        validate_address(value)


def _order(status: str = "open", fulfillment: str = "unfulfilled") -> Order:
    amount = Money(amount=Decimal("1.00"), currency="USD")
    return Order(
        external_ref="ord-1",
        order_number="NC-1",
        version="1",
        status=status,
        fulfillment_status=fulfillment,
        placed_at=datetime.now(timezone.utc),
        line_items=[],
        subtotal=amount,
        shipping=amount,
        tax=amount,
        total=amount,
        shipping_address=Address(
            recipient="Synthetic User",
            line1="42 Synthetic Avenue",
            city="Boston",
            region="MA",
            postal_code="02110",
            country_code="US",
        ),
    )


def test_eligibility_and_masking_are_deterministic() -> None:
    order = _order()
    assert eligible(order)
    assert not eligible(_order(fulfillment="shipped"))
    assert not eligible(_order(status="cancelled"))
    masked = mask_address(order.shipping_address)
    assert "Synthetic" not in str(masked)
    assert "42" not in str(masked)
    assert masked["country_code"] == "US"
