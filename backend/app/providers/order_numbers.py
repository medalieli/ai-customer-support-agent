import re


class InvalidOrderNumber(ValueError):
    pass


def normalize_order_number(value: str) -> str:
    """Normalize a single customer-facing order number, never a provider ID."""

    candidate = value.strip().upper().removeprefix("#").strip()
    candidate = re.sub(r"\s+", "-", candidate)
    if re.fullmatch(r"[A-Z]{2,8}-?\d{3,12}", candidate):
        match = re.fullmatch(r"([A-Z]{2,8})-?(\d{3,12})", candidate)
        assert match is not None
        return f"{match.group(1)}-{match.group(2)}"
    if re.fullmatch(r"\d{3,12}", candidate):
        return candidate
    raise InvalidOrderNumber("invalid_public_order_number")


def extract_order_number(message: str) -> str | None:
    candidates = re.findall(
        r"(?<![A-Za-z0-9])#?\s*(?:[A-Za-z]{2,8}[\s-]?\d{3,12}|\d{3,12})(?![A-Za-z0-9])",
        message,
    )
    normalized: set[str] = set()
    for candidate in candidates:
        try:
            cleaned = candidate.strip().lstrip("#").strip()
            if re.match(r"^(?:order|commande)\s+\d", cleaned, re.IGNORECASE):
                cleaned = re.sub(r"^(?:order|commande)\s+", "", cleaned, flags=re.IGNORECASE)
            normalized.add(normalize_order_number(cleaned))
        except InvalidOrderNumber:
            continue
    if len(normalized) != 1:
        return None
    return normalized.pop()
