from __future__ import annotations

from decimal import Decimal

import pandas as pd
import streamlit as st

from duna_orders.services.dashboard import (
    CustomerMix,
    StatusBreakdown,
    TodaysPulse,
    TopCustomersResult,
    TopItemsResult,
    WeekTrendDay,
)
def _money(value: Decimal) -> str:
    return f"COP {value:,.0f}".replace(",", ".")

def _pct(value: Decimal) -> str:
    return f"{value * Decimal('100'):.0f}%"


def render_todays_pulse(result: TodaysPulse) -> None:
    st.subheader("Today's pulse")

    col_orders, col_revenue, col_aov = st.columns(3)

    with col_orders:
        st.metric("Orders", result.orders_count)

    with col_revenue:
        st.metric("Revenue", _money(result.revenue))

    with col_aov:
        st.metric("AOV", _money(result.aov))


def render_week_trend(result: list[WeekTrendDay]) -> None:
    st.subheader("Week trend")

    rows = [
        {
            "date": item.date.isoformat(),
            "orders_count": item.orders_count,
            "revenue": float(item.revenue),
        }
        for item in result
    ]

    chart_data = pd.DataFrame(rows)

    st.line_chart(
        chart_data,
        x="date",
        y=["orders_count", "revenue"],
    )
    st.dataframe(chart_data, hide_index=True, use_container_width=True)


def render_status_breakdown(result: StatusBreakdown) -> None:
    st.subheader("Status breakdown")

    chart_data = pd.DataFrame(
        [
            {"status": "draft", "orders_count": result.draft},
            {"status": "confirmed", "orders_count": result.confirmed},
            {"status": "completed", "orders_count": result.completed},
            {"status": "cancelled", "orders_count": result.cancelled},
        ]
    )

    st.bar_chart(chart_data, x="status", y="orders_count")
    st.dataframe(chart_data, hide_index=True, use_container_width=True)


def render_customer_mix(result: CustomerMix) -> None:
    st.subheader("Customer mix")

    col_new, col_repeat = st.columns(2)

    with col_new:
        st.metric("New customers", result.new_customers, _pct(result.new_pct))

    with col_repeat:
        st.metric("Repeat customers", result.repeat_customers, _pct(result.repeat_pct))

    chart_data = pd.DataFrame(
        [
            {"type": "new", "customers": result.new_customers},
            {"type": "repeat", "customers": result.repeat_customers},
        ]
    )

    st.bar_chart(chart_data, x="type", y="customers")

def render_top_customers(result: TopCustomersResult) -> None:
    st.subheader("Top customers")

    rows = [
        {
            "Name": entry.customer_name,
            "Orders": entry.order_count,
            "Total spend": _money(entry.total_spend),
        }
        for entry in result.entries
    ]

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )


def render_top_items(result: TopItemsResult) -> None:
    st.subheader("Top items this week")

    rows = [
        {
            "Product": entry.product_name,
            "Quantity": entry.quantity,
            "Revenue": _money(entry.revenue),
        }
        for entry in result.entries
    ]

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )