from __future__ import annotations


def normalize_customer_phone(phone: str | None) -> str | None:
    if phone is None:
        return None

    normalized = phone.strip().replace(" ", "").replace("-", "")

    return normalized or None