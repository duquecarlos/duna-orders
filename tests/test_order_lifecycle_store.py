from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from duna_orders.domain.models import Order, OrderStatusTransition
from duna_orders.storage.order_lifecycle import PostgresOrderLifecycleStore
from duna_orders.storage.postgres_base import Base
from duna_orders.storage.postgres_session import make_engine, make_session_factory
from tests.conftest import DEFAULT_TEST_TENANT_ID


def _store(tmp_path: Path) -> PostgresOrderLifecycleStore:
    database_path = tmp_path / "order_lifecycle.db"
    engine = make_engine(f"sqlite:///{database_path}")
    Base.metadata.create_all(engine)

    return PostgresOrderLifecycleStore(make_session_factory(engine))


def _order(
    *,
    order_id: str = "ord_lifecycle",
    status: str = "draft",
) -> Order:
    created_at = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)

    return Order(
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=order_id,
        created_at=created_at,
        updated_at=created_at,
        raw_message="Pedido lifecycle test",
        status=status,
        status_updated_at=created_at,
    )


def _transition(
    *,
    transition_id: str = "ost_1",
    order_id: str = "ord_lifecycle",
    from_status: str | None = None,
    to_status: str = "draft",
    occurred_at: datetime | None = None,
    source: str = "system",
) -> OrderStatusTransition:
    return OrderStatusTransition(
        transition_id=transition_id,
        tenant_id=DEFAULT_TEST_TENANT_ID,
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
        occurred_at=occurred_at
        or datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc),
        source=source,
    )


def test_create_order_with_transition_persists_order_and_initial_transition(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    order = _order()
    transition = _transition()

    created = store.create_order_with_transition(
        order=order,
        transition=transition,
    )

    transitions = store.list_order_status_transitions(
        order_id=order.order_id,
        tenant_id=order.tenant_id,
    )

    assert created.order_id == order.order_id
    assert created.status == "draft"
    assert len(transitions) == 1
    assert transitions[0].tenant_id == DEFAULT_TEST_TENANT_ID
    assert transitions[0].order_id == order.order_id
    assert transitions[0].from_status is None
    assert transitions[0].to_status == "draft"
    assert transitions[0].source == "system"


def test_update_order_status_with_transition_updates_order_and_appends_transition(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    order = _order()
    initial_transition = _transition()
    changed_at = datetime(2026, 6, 7, 12, 5, tzinfo=timezone.utc)

    store.create_order_with_transition(
        order=order,
        transition=initial_transition,
    )

    updated = store.update_order_status_with_transition(
        order_id=order.order_id,
        status="confirmed",
        confirmed_at=changed_at,
        transition=_transition(
            transition_id="ost_2",
            from_status="draft",
            to_status="confirmed",
            occurred_at=changed_at,
            source="operator",
        ),
    )

    transitions = store.list_order_status_transitions(
        order_id=order.order_id,
        tenant_id=order.tenant_id,
    )

    assert updated.status == "confirmed"
    assert updated.confirmed_at == changed_at
    assert updated.status_updated_at == changed_at
    assert [transition.to_status for transition in transitions] == [
        "draft",
        "confirmed",
    ]
    assert transitions[1].from_status == "draft"
    assert transitions[1].source == "operator"


def test_list_order_status_transitions_returns_ordered_history(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    order = _order()
    base_time = datetime(2026, 6, 7, 12, 0, tzinfo=timezone.utc)

    store.create_order_with_transition(
        order=order,
        transition=_transition(
            transition_id="ost_1",
            from_status=None,
            to_status="draft",
            occurred_at=base_time,
        ),
    )

    store.update_order_status_with_transition(
        order_id=order.order_id,
        status="confirmed",
        confirmed_at=base_time + timedelta(minutes=1),
        transition=_transition(
            transition_id="ost_2",
            from_status="draft",
            to_status="confirmed",
            occurred_at=base_time + timedelta(minutes=1),
            source="operator",
        ),
    )

    store.update_order_status_with_transition(
        order_id=order.order_id,
        status="in_preparation",
        status_updated_at=base_time + timedelta(minutes=5),
        transition=_transition(
            transition_id="ost_3",
            from_status="confirmed",
            to_status="in_preparation",
            occurred_at=base_time + timedelta(minutes=5),
            source="operator",
        ),
    )

    transitions = store.list_order_status_transitions(
        order_id=order.order_id,
        tenant_id=order.tenant_id,
    )

    assert [(item.from_status, item.to_status) for item in transitions] == [
        (None, "draft"),
        ("draft", "confirmed"),
        ("confirmed", "in_preparation"),
    ]


def test_failed_transition_insert_rolls_back_status_update(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    order = _order()
    changed_at = datetime(2026, 6, 7, 12, 5, tzinfo=timezone.utc)

    store.create_order_with_transition(
        order=order,
        transition=_transition(transition_id="ost_duplicate"),
    )

    with pytest.raises(IntegrityError):
        store.update_order_status_with_transition(
            order_id=order.order_id,
            status="confirmed",
            confirmed_at=changed_at,
            transition=_transition(
                transition_id="ost_duplicate",
                from_status="draft",
                to_status="confirmed",
                occurred_at=changed_at,
                source="operator",
            ),
        )

    transitions = store.list_order_status_transitions(
        order_id=order.order_id,
        tenant_id=order.tenant_id,
    )

    assert len(transitions) == 1
    assert transitions[0].to_status == "draft"

    updated = store.update_order_status_with_transition(
        order_id=order.order_id,
        status="confirmed",
        confirmed_at=changed_at,
        transition=_transition(
            transition_id="ost_after_rollback",
            from_status="draft",
            to_status="confirmed",
            occurred_at=changed_at,
            source="operator",
        ),
    )

    assert updated.status == "confirmed"