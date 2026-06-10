from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from inspect import signature
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from duna_orders.domain.models import DraftItemRequest, DraftOrderRequest, Order, OrderItem
from duna_orders.storage.base import StorageInterface
from duna_orders.storage.conversation_orders import PostgresConversationOrderLookup
from duna_orders.storage.postgres import PostgresStorage
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


def test_draft_order_request_accepts_optional_conversation_id() -> None:
    base = {
        "tenant_id": DEFAULT_TEST_TENANT_ID,
        "raw_message": "quiero 2 empanadas",
        "customer_name": "Cliente Test",
        "items": [
            DraftItemRequest(
                tenant_id=DEFAULT_TEST_TENANT_ID,
                product_id="prd_empanada",
                quantity=Decimal("2"),
            )
        ],
    }

    without_link = DraftOrderRequest(**base)
    with_link = DraftOrderRequest(**base, conversation_id="conv_domain_link")

    assert without_link.conversation_id is None
    assert with_link.conversation_id == "conv_domain_link"


def test_order_accepts_and_preserves_optional_conversation_id() -> None:
    without_link = _make_order(order_id="ord_without_link")
    with_link = _make_order(
        order_id="ord_with_link",
        conversation_id="conv_order_link",
    )

    assert without_link.conversation_id is None
    assert with_link.conversation_id == "conv_order_link"
    assert with_link.model_copy(deep=True).conversation_id == "conv_order_link"


def test_postgres_order_conversation_id_is_nullable_and_unique_when_present(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)

    storage.create_order(_make_order(order_id="ord_null_a"))
    storage.create_order(_make_order(order_id="ord_null_b"))
    storage.create_order(
        _make_order(order_id="ord_link_a", conversation_id="conv_unique")
    )

    with pytest.raises(IntegrityError):
        storage.create_order(
            _make_order(order_id="ord_link_b", conversation_id="conv_unique")
        )


def test_postgres_order_conversation_id_uniqueness_is_not_status_dependent(
    tmp_path: Path,
) -> None:
    storage = _postgres_storage(tmp_path)

    storage.create_order(
        _make_order(order_id="ord_link_confirmed", conversation_id="conv_lifecycle")
    )
    storage.update_order_status(
        "ord_link_confirmed",
        "confirmed",
        confirmed_at=datetime.now(timezone.utc),
    )

    with pytest.raises(IntegrityError):
        storage.create_order(
            _make_order(order_id="ord_link_reuse", conversation_id="conv_lifecycle")
        )


def test_conversation_order_lookup_finds_order_by_tenant_and_conversation_id(
    tmp_path: Path,
) -> None:
    storage, lookup = _postgres_storage_and_lookup(tmp_path)
    order = _make_order(order_id="ord_lookup", conversation_id="conv_lookup")

    storage.create_order(order)

    found = lookup.get_order_by_conversation_id(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        conversation_id="conv_lookup",
    )

    assert found is not None
    assert found.order_id == "ord_lookup"
    assert found.conversation_id == "conv_lookup"


def test_conversation_order_lookup_returns_none_for_missing_or_wrong_tenant(
    tmp_path: Path,
) -> None:
    storage, lookup = _postgres_storage_and_lookup(tmp_path)
    storage.create_order(
        _make_order(order_id="ord_lookup_tenant", conversation_id="conv_lookup_tenant")
    )

    assert (
        lookup.get_order_by_conversation_id(
            tenant_id=DEFAULT_TEST_TENANT_ID,
            conversation_id="conv_missing",
        )
        is None
    )
    assert (
        lookup.get_order_by_conversation_id(
            tenant_id="other-tenant",
            conversation_id="conv_lookup_tenant",
        )
        is None
    )


def test_conversation_order_lookup_does_not_mutate_order(tmp_path: Path) -> None:
    storage, lookup = _postgres_storage_and_lookup(tmp_path)
    storage.create_order(
        _make_order(order_id="ord_lookup_readonly", conversation_id="conv_readonly")
    )

    found = lookup.get_order_by_conversation_id(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        conversation_id="conv_readonly",
    )
    saved = storage.get_order("ord_lookup_readonly")

    assert found is not None
    assert saved is not None
    assert saved.status == "draft"
    assert saved.updated_at == found.updated_at


def test_conversation_order_lookup_stays_outside_storage_interface() -> None:
    storage_methods = set(StorageInterface.__abstractmethods__)
    lookup_source = Path("src/duna_orders/storage/conversation_orders.py").read_text()

    assert "get_order_by_conversation_id" not in storage_methods
    assert "StorageInterface" not in lookup_source
    assert "OrderService" not in lookup_source
    assert "ParsingService" not in lookup_source
    assert "PROMPT_VERSION" not in lookup_source
    assert "create_draft" not in lookup_source
    assert "create_order(" not in lookup_source
    assert "update_order" not in lookup_source


def test_storage_interface_signatures_are_unchanged() -> None:
    create_order_parameters = list(signature(StorageInterface.create_order).parameters)
    get_order_parameters = list(signature(StorageInterface.get_order).parameters)

    assert create_order_parameters == ["self", "order"]
    assert get_order_parameters == ["self", "order_id"]
    assert "conversation_id" not in Path("src/duna_orders/storage/base.py").read_text()


def _postgres_storage(tmp_path: Path) -> PostgresStorage:
    storage, _ = _postgres_storage_and_lookup(tmp_path)
    return storage


def _postgres_storage_and_lookup(
    tmp_path: Path,
) -> tuple[PostgresStorage, PostgresConversationOrderLookup]:
    database_path = tmp_path / "conversation_draft_link.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)
    session_factory = make_session_factory(engine)
    return PostgresStorage(session_factory), PostgresConversationOrderLookup(
        session_factory
    )


def _make_order(
    *,
    order_id: str,
    tenant_id: str = DEFAULT_TEST_TENANT_ID,
    conversation_id: str | None = None,
) -> Order:
    item = OrderItem(
        tenant_id=tenant_id,
        order_item_id=f"oit_{order_id}",
        order_id=order_id,
        product_id="prd_empanada",
        product_name_snapshot="Empanada",
        unit_snapshot="unidad",
        quantity=Decimal("2"),
        unit_price_snapshot=Decimal("3000"),
        line_total=Decimal("6000"),
        validation_status="ok",
    )
    return Order(
        tenant_id=tenant_id,
        order_id=order_id,
        conversation_id=conversation_id,
        raw_message="Quiero 2 empanadas",
        status="draft",
        items=[item],
        subtotal=Decimal("6000"),
        delivery_fee=Decimal("0"),
        packaging_fee=Decimal("0"),
        total=Decimal("6000"),
    )
