from __future__ import annotations

from decimal import Decimal

from duna_orders.demo_catalog import DemoCatalogFile, load_demo_catalog
from duna_orders.domain.models import DraftItemRequest, DraftOrderRequest, ParseResult
from duna_orders.ui import parser_review
from duna_orders.ui.parser_review import parsed_result_to_draft_candidate


def _parse_result(
    *,
    catalog: DemoCatalogFile,
    item_requests: list[DraftItemRequest],
    warnings: list[str] | None = None,
    fulfillment_type: str | None = None,
    payment_method: str | None = None,
    customer_notes: str | None = None,
    delivery_zone: str | None = None,
) -> ParseResult:
    return ParseResult(
        request=DraftOrderRequest(
            tenant_id=catalog.business.tenant_id,
            raw_message="test message",
            customer_name="",
            customer_phone=None,
            fulfillment_type=fulfillment_type,
            delivery_zone=delivery_zone,
            packaging_fee=Decimal("0"),
            customer_notes=customer_notes,
            payment_method=payment_method,
            items=item_requests,
        ),
        warnings=warnings or [],
        model="test-model",
        latency_ms=1,
        raw_response="{}",
    )


def test_empty_parse_result_returns_empty_candidate_with_warning() -> None:
    catalog = load_demo_catalog()
    result = _parse_result(catalog=catalog, item_requests=[])

    candidate = parsed_result_to_draft_candidate(
        result,
        catalog,
        catalog.business.tenant_id,
    )

    assert candidate.items == []
    assert "No se reconocieron productos" in candidate.warnings[0]


def test_matched_products_populate_candidate_items() -> None:
    catalog = load_demo_catalog()
    product = catalog.products[0]
    result = _parse_result(
        catalog=catalog,
        item_requests=[
            DraftItemRequest(
                tenant_id=catalog.business.tenant_id,
                product_id=product.product_id,
                quantity=Decimal("2"),
                modifications="sin cebolla",
            )
        ],
    )

    candidate = parsed_result_to_draft_candidate(
        result,
        catalog,
        catalog.business.tenant_id,
    )

    assert len(candidate.items) == 1
    assert candidate.items[0].matched_product == product
    assert candidate.items[0].quantity == Decimal("2")
    assert candidate.items[0].modifications == "sin cebolla"
    assert candidate.warnings == []


def test_unmatched_product_returns_top_level_warning() -> None:
    catalog = load_demo_catalog()
    result = _parse_result(
        catalog=catalog,
        item_requests=[
            DraftItemRequest(
                tenant_id=catalog.business.tenant_id,
                product_id="prd_missing",
                quantity=Decimal("1"),
            )
        ],
        warnings=["No reconocí un producto del mensaje."],
    )

    candidate = parsed_result_to_draft_candidate(
        result,
        catalog,
        catalog.business.tenant_id,
    )

    assert candidate.items == []
    assert "No reconocí un producto del mensaje." in candidate.warnings
    assert "Producto no encontrado: prd_missing" in candidate.warnings


def test_day_restricted_product_on_wrong_day_returns_item_warning(
    monkeypatch,
) -> None:
    catalog = load_demo_catalog()
    product = next(product for product in catalog.products if product.available_days)

    wrong_day = next(
        day
        for day in [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ]
        if day not in product.available_days
    )

    monkeypatch.setattr(parser_review, "_current_weekday", lambda: wrong_day)

    result = _parse_result(
        catalog=catalog,
        item_requests=[
            DraftItemRequest(
                tenant_id=catalog.business.tenant_id,
                product_id=product.product_id,
                quantity=Decimal("1"),
            )
        ],
    )

    candidate = parsed_result_to_draft_candidate(
        result,
        catalog,
        catalog.business.tenant_id,
    )

    assert len(candidate.items) == 1
    assert product.product_name in candidate.items[0].warnings[0]
    assert wrong_day in candidate.items[0].warnings[0]


def test_inferred_fields_are_preserved() -> None:
    catalog = load_demo_catalog()
    result = _parse_result(
        catalog=catalog,
        item_requests=[],
        fulfillment_type="delivery",
        payment_method="nequi",
        customer_notes="llamar al llegar",
        delivery_zone="cra 16 con 145",
    )

    candidate = parsed_result_to_draft_candidate(
        result,
        catalog,
        catalog.business.tenant_id,
    )

    assert candidate.inferred_fulfillment_type == "delivery"
    assert candidate.inferred_payment_method == "nequi"
    assert candidate.inferred_customer_notes == "llamar al llegar"
    assert candidate.inferred_delivery_zone == "cra 16 con 145"
