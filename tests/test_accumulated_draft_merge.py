from __future__ import annotations

from decimal import Decimal

from duna_orders.domain.models import (
    AccumulatedDraft,
    AccumulatedDraftItem,
    DraftItemRequest,
    DraftOrderRequest,
)
from duna_orders.services.accumulated_draft_merge import merge_parse_result_into_draft


TENANT_ID = "tenant-merge-test"
CONV_ID = "conv-merge-001"


def _parsed(
    items: list[DraftItemRequest] | None = None,
    *,
    customer_name: str = "",
    customer_phone: str | None = None,
    fulfillment_type=None,
    delivery_zone: str | None = None,
    packaging_fee: Decimal = Decimal("0"),
    customer_notes: str | None = None,
    payment_method=None,
) -> DraftOrderRequest:
    return DraftOrderRequest(
        tenant_id=TENANT_ID,
        raw_message="test",
        customer_name=customer_name,
        customer_phone=customer_phone,
        items=items or [],
        fulfillment_type=fulfillment_type,
        delivery_zone=delivery_zone,
        packaging_fee=packaging_fee,
        customer_notes=customer_notes,
        payment_method=payment_method,
    )


def _item(product_id: str, quantity: str = "1", modifications: str | None = None) -> DraftItemRequest:
    return DraftItemRequest(
        tenant_id=TENANT_ID,
        product_id=product_id,
        quantity=Decimal(quantity),
        modifications=modifications,
    )


def _merge(
    prior: AccumulatedDraft | None,
    parsed: DraftOrderRequest,
    turn_count: int = 1,
    warnings: list[str] | None = None,
) -> AccumulatedDraft:
    return merge_parse_result_into_draft(
        prior,
        parsed,
        conversation_id=CONV_ID,
        turn_count=turn_count,
        warnings=warnings,
    )


def test_first_turn_creates_accumulated_draft_from_parsed_snapshot() -> None:
    parsed = _parsed(
        items=[_item("sku-001", "2")],
        customer_name="Ana",
        customer_phone="whatsapp:+573001112233",
    )

    result = _merge(None, parsed, turn_count=1)

    assert result.tenant_id == TENANT_ID
    assert result.conversation_id == CONV_ID
    assert result.turn_count == 1
    assert result.schema_version == "1"
    assert result.customer_name == "Ana"
    assert result.customer_phone == "whatsapp:+573001112233"
    assert len(result.items) == 1
    assert result.items[0].product_id == "sku-001"
    assert result.items[0].quantity == Decimal("2")
    assert result.is_complete is True
    assert result.conflicts == []


def test_second_turn_adds_new_item_while_preserving_prior_item() -> None:
    prior = _merge(None, _parsed(items=[_item("sku-001", "2")]), turn_count=1)

    result = _merge(
        prior,
        _parsed(items=[_item("sku-001", "2"), _item("sku-002", "3")]),
        turn_count=2,
    )

    product_ids = [i.product_id for i in result.items]
    assert "sku-001" in product_ids
    assert "sku-002" in product_ids
    assert len(result.items) == 2
    assert result.conflicts == []
    assert result.is_complete is True


def test_parsed_quantity_for_existing_item_replaces_prior_quantity() -> None:
    prior = _merge(None, _parsed(items=[_item("sku-001", "2")]), turn_count=1)

    result = _merge(prior, _parsed(items=[_item("sku-001", "5")]), turn_count=2)

    assert len(result.items) == 1
    assert result.items[0].product_id == "sku-001"
    assert result.items[0].quantity == Decimal("5")
    assert result.is_complete is True


def test_same_product_different_modifications_remains_separate() -> None:
    parsed = _parsed(
        items=[
            _item("sku-001", "2", modifications=None),
            _item("sku-001", "1", modifications="sin sal"),
        ]
    )

    result = _merge(None, parsed, turn_count=1)

    assert len(result.items) == 2
    mods = {i.modifications for i in result.items}
    assert None in mods
    assert "sin sal" in mods
    assert result.is_complete is True


def test_parsed_none_or_empty_scalar_does_not_erase_prior_scalar() -> None:
    prior = _merge(
        None,
        _parsed(customer_name="Ana", customer_notes="sin picante"),
        turn_count=1,
    )

    # Second turn: customer_name and customer_notes are empty
    result = _merge(
        prior,
        _parsed(customer_name="", customer_notes=None),
        turn_count=2,
    )

    assert result.customer_name == "Ana"
    assert result.customer_notes == "sin picante"


def test_unresolved_product_id_none_item_makes_draft_incomplete() -> None:
    # Empty product_id from parser → product_id=None in AccumulatedDraftItem
    parsed = _parsed(items=[_item("", "2")])

    result = _merge(None, parsed, turn_count=1)

    assert len(result.items) == 1
    assert result.items[0].product_id is None
    assert result.is_complete is False


def test_non_positive_quantity_makes_draft_incomplete_and_records_diagnostic() -> None:
    parsed = _parsed(items=[_item("sku-001", "0")])

    result = _merge(None, parsed, turn_count=1)

    assert result.is_complete is False
    assert any("non-positive quantity" in w for w in result.warnings)


def test_negative_quantity_makes_draft_incomplete_and_records_diagnostic() -> None:
    parsed = _parsed(items=[_item("sku-001", "-1")])

    result = _merge(None, parsed, turn_count=1)

    assert result.is_complete is False
    assert any("non-positive quantity" in w for w in result.warnings)


def test_prior_item_absent_from_parsed_snapshot_is_preserved_not_dropped() -> None:
    prior = _merge(None, _parsed(items=[_item("sku-001", "2")]), turn_count=1)

    # Second turn: only sku-002 in snapshot; sku-001 disappears
    result = _merge(prior, _parsed(items=[_item("sku-002", "1")]), turn_count=2)

    product_ids = [i.product_id for i in result.items]
    assert "sku-001" in product_ids
    assert "sku-002" in product_ids
    assert len(result.conflicts) == 1
    assert "sku-001" in result.conflicts[0]
    assert "kept from prior turn" in result.conflicts[0]
    assert result.is_complete is False


def test_conflict_clears_when_missing_item_reappears_in_next_parse() -> None:
    prior_turn1 = _merge(None, _parsed(items=[_item("sku-001", "2")]), turn_count=1)
    prior_turn2 = _merge(prior_turn1, _parsed(items=[_item("sku-002", "1")]), turn_count=2)
    assert len(prior_turn2.conflicts) == 1

    # Third turn: sku-001 returns in snapshot
    result = _merge(
        prior_turn2,
        _parsed(items=[_item("sku-001", "2"), _item("sku-002", "1")]),
        turn_count=3,
    )

    assert result.conflicts == []
    assert result.is_complete is True


def test_idempotent_replay_of_same_parsed_snapshot_does_not_duplicate_items() -> None:
    parsed = _parsed(items=[_item("sku-001", "2"), _item("sku-002", "1")])
    first = _merge(None, parsed, turn_count=1)

    second = _merge(first, parsed, turn_count=1)

    assert len(second.items) == 2
    qtys = {i.product_id: i.quantity for i in second.items}
    assert qtys["sku-001"] == Decimal("2")
    assert qtys["sku-002"] == Decimal("1")
    assert second.conflicts == []
    assert second.is_complete is True


def test_completeness_true_only_when_all_item_gates_pass_and_no_blocking_conflicts() -> None:
    # Not complete: no items
    r_empty = _merge(None, _parsed(items=[]), turn_count=1)
    assert r_empty.is_complete is False

    # Not complete: product_id=None
    r_unknown = _merge(None, _parsed(items=[_item("", "2")]), turn_count=1)
    assert r_unknown.is_complete is False

    # Not complete: quantity <= 0
    r_zero_qty = _merge(None, _parsed(items=[_item("sku-001", "0")]), turn_count=1)
    assert r_zero_qty.is_complete is False

    # Not complete: conflict present (prior item missing from snapshot)
    prior = _merge(None, _parsed(items=[_item("sku-001", "1")]), turn_count=1)
    r_conflict = _merge(prior, _parsed(items=[_item("sku-002", "1")]), turn_count=2)
    assert r_conflict.is_complete is False

    # Complete: all gates pass
    r_complete = _merge(
        None,
        _parsed(items=[_item("sku-001", "2"), _item("sku-002", "1")]),
        turn_count=1,
    )
    assert r_complete.is_complete is True


def test_packaging_fee_preserved_when_parsed_returns_zero() -> None:
    prior = _merge(
        None,
        _parsed(items=[_item("sku-001", "1")], packaging_fee=Decimal("1500")),
        turn_count=1,
    )

    # Second turn: packaging_fee defaults to 0 (not mentioned)
    result = _merge(
        prior,
        _parsed(items=[_item("sku-001", "1")], packaging_fee=Decimal("0")),
        turn_count=2,
    )

    assert result.packaging_fee == Decimal("1500")


def test_packaging_fee_updated_when_parsed_returns_nonzero() -> None:
    prior = _merge(
        None,
        _parsed(items=[_item("sku-001", "1")], packaging_fee=Decimal("1000")),
        turn_count=1,
    )

    result = _merge(
        prior,
        _parsed(items=[_item("sku-001", "1")], packaging_fee=Decimal("2000")),
        turn_count=2,
    )

    assert result.packaging_fee == Decimal("2000")


def test_duplicate_item_key_in_parsed_snapshot_combines_quantities_and_warns() -> None:
    parsed = _parsed(
        items=[
            _item("sku-001", "2"),
            _item("sku-001", "3"),  # same key as above
        ]
    )

    result = _merge(None, parsed, turn_count=1)

    assert len(result.items) == 1
    assert result.items[0].quantity == Decimal("5")
    assert any("quantities combined" in w for w in result.warnings)


def test_caller_supplied_warnings_are_included_in_result() -> None:
    parsed = _parsed(items=[_item("sku-001", "1")])

    result = _merge(None, parsed, turn_count=1, warnings=["upstream warning"])

    assert "upstream warning" in result.warnings


def test_modifications_normalization_treats_case_variants_as_same_item() -> None:
    prior = _merge(
        None,
        _parsed(items=[_item("sku-001", "2", modifications="Sin Sal")]),
        turn_count=1,
    )

    # Same product with lowercase modifications — same identity key
    result = _merge(
        prior,
        _parsed(items=[_item("sku-001", "4", modifications="sin sal")]),
        turn_count=2,
    )

    assert len(result.items) == 1
    assert result.items[0].quantity == Decimal("4")
    assert result.conflicts == []


def test_canonical_incremental_build_reaches_is_complete_with_no_conflict() -> None:
    # M10 uses full-transcript parsing: the LLM sees ALL prior turns on every call
    # and returns a complete snapshot of everything it knows.  On turn 2, the
    # snapshot includes BOTH the turn-1 item and the turn-2 item — empanada does
    # NOT disappear from the snapshot, so no conflict arises.
    #
    # This test proves the happy-path M10.2 incremental build does not generate
    # spurious conflicts or block is_complete.

    # Turn 1: "quiero dos empanadas" — parser returns [empanada]
    state1 = _merge(None, _parsed(items=[_item("sku-empanada", "2")]), turn_count=1)
    assert len(state1.items) == 1
    assert state1.is_complete is True
    assert state1.conflicts == []

    # Turn 2: "y una gaseosa" — FULL-TRANSCRIPT parse returns [empanada, gaseosa]
    state2 = _merge(
        state1,
        _parsed(items=[_item("sku-empanada", "2"), _item("sku-gaseosa", "1")]),
        turn_count=2,
    )
    assert len(state2.items) == 2
    assert state2.is_complete is True
    assert state2.conflicts == [], (
        "No conflict: empanada is present in the full-transcript turn-2 snapshot"
    )

    # Turn 3: "eso es todo" — parser returns same full snapshot (no new items)
    state3 = _merge(
        state2,
        _parsed(items=[_item("sku-empanada", "2"), _item("sku-gaseosa", "1")]),
        turn_count=3,
    )
    product_ids = {i.product_id for i in state3.items}
    assert product_ids == {"sku-empanada", "sku-gaseosa"}
    assert len(state3.items) == 2
    assert state3.is_complete is True
    assert state3.conflicts == []


def test_merge_does_not_mutate_caller_supplied_warnings_list() -> None:
    caller_warnings = ["upstream warning"]
    original = list(caller_warnings)

    _merge(None, _parsed(items=[_item("sku-001", "1")]), turn_count=1, warnings=caller_warnings)

    assert caller_warnings == original


def test_merge_does_not_mutate_prior_draft() -> None:
    prior = _merge(
        None,
        _parsed(items=[_item("sku-001", "2")], customer_name="Ana"),
        turn_count=1,
    )
    prior_items_before = list(prior.items)
    prior_name_before = prior.customer_name

    _merge(prior, _parsed(items=[_item("sku-001", "5"), _item("sku-002", "1")], customer_name="Bob"), turn_count=2)

    assert prior.items == prior_items_before
    assert prior.customer_name == prior_name_before
