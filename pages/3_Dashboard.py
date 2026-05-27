from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from duna_orders.config import settings
from duna_orders.demo_catalog import DemoCatalogFile
from duna_orders.services.dashboard import (
    compute_customer_mix,
    compute_product_pairs,
    compute_status_breakdown,
    compute_time_of_day_heatmap,
    compute_todays_pulse,
    compute_top_customers,
    compute_top_items,
    compute_week_trend,
)
from duna_orders.services.dashboard_read_scenario import (
    run_locked_dashboard_read_scenario,
)
from duna_orders.storage.read_context import sheets_request_context
from duna_orders.ui.dashboard_streamlit import (
    render_customer_mix,
    render_product_pairs,
    render_status_breakdown,
    render_time_of_day_heatmap,
    render_todays_pulse,
    render_top_customers,
    render_top_items,
    render_week_trend,
)
from duna_orders.ui.setup import (
    get_demo_catalog,
    get_storage,
    prepare_storage_catalog,
)


def _tenant_id_from_catalog(catalog: DemoCatalogFile) -> str:
    if not catalog.products:
        raise RuntimeError("Demo catalog has no products; cannot infer tenant_id.")

    return catalog.products[0].tenant_id


def _bootstrap_session() -> None:
    if "demo_catalog" not in st.session_state:
        st.session_state.demo_catalog = get_demo_catalog()

    if "storage" not in st.session_state:
        storage = get_storage()
        st.session_state.catalog_ready = prepare_storage_catalog(
            storage,
            st.session_state.demo_catalog,
        )
        st.session_state.storage = storage


def main() -> None:
    st.set_page_config(
        page_title="Duna Orders - Dashboard",
        page_icon="D",
        layout="wide",
    )

    st.title("Dashboard")
    st.caption("Pilot dashboard computed from the locked M7 read scenario.")

    _bootstrap_session()

    storage = st.session_state.storage
    tenant_id = _tenant_id_from_catalog(st.session_state.demo_catalog)

    timezone_name = settings.default_timezone
    timezone = ZoneInfo(timezone_name)
    now = datetime.now(timezone)
    today = now.date()
    week_start = today - timedelta(days=6)

    with sheets_request_context(storage):
        scenario = run_locked_dashboard_read_scenario(
            storage,
            tenant_id=tenant_id,
            now=now,
            timezone_name=timezone_name,
        )

        todays_pulse = compute_todays_pulse(scenario, today=today)
        week_trend = compute_week_trend(scenario, today=today)
        status_breakdown = compute_status_breakdown(scenario)
        customer_mix = compute_customer_mix(scenario, week_start=week_start)
        top_customers = compute_top_customers(scenario, week_start=week_start)
        top_items = compute_top_items(scenario, week_start=week_start)
        time_of_day_heatmap = compute_time_of_day_heatmap(scenario, today=today)
        product_pairs = compute_product_pairs(scenario, week_start=week_start)
        render_todays_pulse(todays_pulse)
        render_time_of_day_heatmap(time_of_day_heatmap)

        left_col, right_col = st.columns(2)

        with left_col:
            render_week_trend(week_trend)
            render_top_customers(top_customers)

        with right_col:
            render_status_breakdown(status_breakdown)
            render_customer_mix(customer_mix)
            render_top_items(top_items)
            render_product_pairs(product_pairs)


if __name__ == "__main__":
    main()