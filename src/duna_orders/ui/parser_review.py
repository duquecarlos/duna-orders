from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from duna_orders.demo_catalog import DemoCatalogFile
from duna_orders.domain.models import ParseResult, Product, Weekday


@dataclass(frozen=True)
class DraftCandidateItem:
    product_id: str
    matched_product: Product | None
    quantity: Decimal
    modifications: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DraftCandidate:
    items: list[DraftCandidateItem] = field(default_factory=list)
    inferred_fulfillment_type: str | None = None
    inferred_payment_method: str | None = None
    inferred_customer_notes: str | None = None
    inferred_delivery_zone: str | None = None
    warnings: list[str] = field(default_factory=list)


def parsed_result_to_draft_candidate(
    parse_result: ParseResult,
    catalog: DemoCatalogFile,
    tenant_id: str,
) -> DraftCandidate:
    products_by_id = {product.product_id: product for product in catalog.products}
    today = _current_weekday()
    items: list[DraftCandidateItem] = []
    warnings = list(parse_result.warnings)

    for item_request in parse_result.request.items:
        product = products_by_id.get(item_request.product_id)
        item_warnings: list[str] = []

        if product is None:
            warnings.append(f"Producto no encontrado: {item_request.product_id}")
            continue

        if product.tenant_id != tenant_id:
            item_warnings.append(
                f"Producto pertenece a otro tenant: {item_request.product_id}"
            )

        if product.available_days is not None and today not in product.available_days:
            item_warnings.append(
                f"{product.product_name} no está disponible hoy ({today})."
            )

        items.append(
            DraftCandidateItem(
                product_id=item_request.product_id,
                matched_product=product,
                quantity=item_request.quantity,
                modifications=item_request.modifications,
                warnings=item_warnings,
            )
        )

    if not items:
        warnings.append("No se reconocieron productos para crear un borrador.")

    return DraftCandidate(
        items=items,
        inferred_fulfillment_type=parse_result.request.fulfillment_type,
        inferred_payment_method=parse_result.request.payment_method,
        inferred_customer_notes=parse_result.request.customer_notes,
        inferred_delivery_zone=parse_result.request.delivery_zone,
        warnings=warnings,
    )


def _current_weekday() -> Weekday:
    return datetime.now().strftime("%A").lower()  # type: ignore[return-value]