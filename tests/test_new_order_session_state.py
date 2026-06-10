from __future__ import annotations

from streamlit.testing.v1 import AppTest

from duna_orders.storage.memory import InMemoryStorage


def test_new_order_initializes_missing_catalog_ready_with_existing_storage() -> None:
    app = AppTest.from_file("pages/1_New_Order.py", default_timeout=10)
    app.session_state["storage"] = InMemoryStorage()

    app.run()

    assert app.exception == []
    assert app.session_state["catalog_ready"] is True
