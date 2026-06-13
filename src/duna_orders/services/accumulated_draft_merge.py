from __future__ import annotations

from decimal import Decimal

from duna_orders.domain.models import (
    AccumulatedDraft,
    AccumulatedDraftItem,
    DraftOrderRequest,
)


def _nonempty(val: str | None) -> str | None:
    return val if val and val.strip() else None


def _normalize_modifications(modifications: str | None) -> str | None:
    if not modifications:
        return None
    stripped = modifications.strip()
    return stripped.lower() if stripped else None


def _item_key(
    product_id: str | None,
    modifications: str | None,
) -> tuple[str | None, str | None]:
    return (product_id if product_id else None, _normalize_modifications(modifications))


def _compute_is_complete(
    items: list[AccumulatedDraftItem],
    conflicts: list[str],
) -> bool:
    return (
        len(items) > 0
        and all(item.product_id for item in items)
        and all(item.quantity > 0 for item in items)
        and len(conflicts) == 0
    )


def merge_parse_result_into_draft(
    prior: AccumulatedDraft | None,
    parsed: DraftOrderRequest,
    *,
    conversation_id: str,
    turn_count: int,
    warnings: list[str] | None = None,
) -> AccumulatedDraft:
    """Pure merge: prior state + parser snapshot → next AccumulatedDraft. No I/O.

    INVARIANT: `parsed` is a FULL-TRANSCRIPT snapshot. The LLM receives the
    complete conversation history on every turn and returns all items it knows
    about, not just the items mentioned in the latest message. This means a
    well-behaved parse of turn N includes every item from turns 1..N. The
    "prior item missing from snapshot → conflict" policy is safe under this
    invariant: a missing prior item indicates a parser omission, not a genuine
    customer retraction. If the snapshot were current-message-only, this policy
    would block every incremental order and must not be used.

    The caller-supplied `warnings` list is never mutated.
    """
    merge_warnings: list[str] = list(warnings or [])

    # Scalar field merge — non-empty parsed value wins; None/empty keeps prior
    tenant_id = prior.tenant_id if prior is not None else parsed.tenant_id

    if prior is None:
        customer_name = _nonempty(parsed.customer_name)
        customer_phone = _nonempty(parsed.customer_phone)
        fulfillment_type = parsed.fulfillment_type
        delivery_zone = _nonempty(parsed.delivery_zone)
        packaging_fee = parsed.packaging_fee
        customer_notes = _nonempty(parsed.customer_notes)
        payment_method = parsed.payment_method
    else:
        customer_name = _nonempty(parsed.customer_name) or prior.customer_name
        customer_phone = _nonempty(parsed.customer_phone) or prior.customer_phone
        fulfillment_type = parsed.fulfillment_type or prior.fulfillment_type
        delivery_zone = _nonempty(parsed.delivery_zone) or prior.delivery_zone
        # packaging_fee: Decimal("0") from parser means "not mentioned" — keep prior
        packaging_fee = (
            parsed.packaging_fee
            if parsed.packaging_fee != Decimal("0")
            else prior.packaging_fee
        )
        customer_notes = _nonempty(parsed.customer_notes) or prior.customer_notes
        payment_method = parsed.payment_method or prior.payment_method

    # Convert parsed DraftItemRequests → AccumulatedDraftItems, build identity map
    # Empty product_id from parser → None (unrecognized catalog item)
    parsed_item_map: dict[tuple[str | None, str | None], AccumulatedDraftItem] = {}
    for raw_item in parsed.items:
        pid = raw_item.product_id if raw_item.product_id else None
        item = AccumulatedDraftItem(
            product_id=pid,
            quantity=raw_item.quantity,
            modifications=raw_item.modifications,
        )
        key = _item_key(pid, raw_item.modifications)
        if key in parsed_item_map:
            # Parser error: two items share same identity key — combine quantities
            existing = parsed_item_map[key]
            parsed_item_map[key] = AccumulatedDraftItem(
                product_id=existing.product_id,
                quantity=existing.quantity + item.quantity,
                modifications=existing.modifications,
            )
            merge_warnings.append(
                f"Duplicate item {repr(pid or '(unknown)')} in parse snapshot; quantities combined"
            )
        else:
            parsed_item_map[key] = item

    # Validate quantities of parsed items
    for item in parsed_item_map.values():
        if item.quantity <= 0:
            merge_warnings.append(
                f"Item {repr(item.product_id or '(unknown)')} has non-positive quantity"
                f" {item.quantity}; draft incomplete"
            )

    # Prior item map (empty on first turn)
    prior_item_map: dict[tuple[str | None, str | None], AccumulatedDraftItem] = {}
    if prior is not None:
        for prior_item in prior.items:
            key = _item_key(prior_item.product_id, prior_item.modifications)
            prior_item_map[key] = prior_item

    # Merged items: parsed items first, then prior-only items (with conflicts)
    merged_items: list[AccumulatedDraftItem] = list(parsed_item_map.values())
    conflicts: list[str] = []

    for key, prior_item in prior_item_map.items():
        if key not in parsed_item_map:
            label = repr(prior_item.product_id or "(unknown)")
            conflicts.append(
                f"Item {label} missing from latest parse snapshot; kept from prior turn"
            )
            merged_items.append(prior_item)

    return AccumulatedDraft(
        schema_version="1",
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        turn_count=turn_count,
        items=merged_items,
        customer_name=customer_name,
        customer_phone=customer_phone,
        fulfillment_type=fulfillment_type,
        delivery_zone=delivery_zone,
        packaging_fee=packaging_fee,
        customer_notes=customer_notes,
        payment_method=payment_method,
        is_complete=_compute_is_complete(merged_items, conflicts),
        conflicts=conflicts,
        warnings=merge_warnings,
    )
