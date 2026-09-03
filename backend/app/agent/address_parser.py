import json
import re

from app.providers.models import Address
from app.services.address_actions import AddressActionError, validate_address

ADDRESS_TERMS = re.compile(
    r"\b(?:shipping address|delivery address|adresse de livraison|"
    r"changer.*adresse|change.*address)\b",
    re.IGNORECASE,
)


def looks_like_address_change(message: str) -> bool:
    return bool(ADDRESS_TERMS.search(message))


def extract_proposed_address(message: str) -> Address:
    match = re.search(r"\{.*\}", message, re.DOTALL)
    if match:
        try:
            raw = json.loads(match.group(0))
            if isinstance(raw, dict) and isinstance(raw.get("address"), dict):
                raw = raw["address"]
            return validate_address(raw)
        except (json.JSONDecodeError, AddressActionError):
            pass
    aliases = {
        "recipient": ("recipient", "name", "nom", "destinataire"),
        "line1": ("line1", "address line 1", "adresse ligne 1", "adresse"),
        "line2": ("line2", "address line 2", "adresse ligne 2"),
        "city": ("city", "ville"),
        "region": ("region", "state", "province", "état"),
        "postal_code": ("postal code", "postcode", "zip", "code postal"),
        "country_code": ("country code", "country", "code pays", "pays"),
    }
    values: dict[str, str] = {}
    labels = [re.escape(label) for group in aliases.values() for label in group]
    boundary = "|".join(sorted(labels, key=len, reverse=True))
    for field, names in aliases.items():
        names_pattern = "|".join(re.escape(name) for name in sorted(names, key=len, reverse=True))
        found = re.search(
            rf"(?:^|[,;\n])\s*(?:{names_pattern})\s*[:=]\s*(.+?)(?=\s*(?:[,;\n]\s*(?:{boundary})\s*[:=]|$))",
            message,
            re.IGNORECASE | re.DOTALL,
        )
        if found:
            values[field] = found.group(1).strip()
    return validate_address(values)
