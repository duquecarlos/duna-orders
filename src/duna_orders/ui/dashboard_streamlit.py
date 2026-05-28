from __future__ import annotations

from decimal import Decimal

import altair as alt
import pandas as pd
import streamlit as st

from duna_orders.services.dashboard import (
    CustomerMix,
    ProductPairsResult,
    StatusBreakdown,
    TimeOfDayHeatmapResult,
    TodaysPulse,
    TopCustomersResult,
    TopItemsResult,
    WeekTrendDay,
    TodaysStatusStrip,
    TopItemsByCategoryResult,
    WeekOverWeekMetric,
    WeekOverWeekResult,
)


EMPTY_TODAY = "No data for today."
EMPTY_WEEK = "No data for this week."
EMPTY_PERIOD = "No data for this period."

WEEKDAY_LABELS = {
    0: "Mon",
    1: "Tue",
    2: "Wed",
    3: "Thu",
    4: "Fri",
    5: "Sat",
    6: "Sun",
}

WEEKDAY_SORT = [WEEKDAY_LABELS[index] for index in range(7)]


def _money(value: Decimal) -> str:
    return f"COP {value:,.0f}".replace(",", ".")


def _count(value: int) -> str:
    return f"{value:,}".replace(",", ".")


def _quantity(value: Decimal) -> str:
    if value == value.to_integral_value():
        return _count(int(value))

    return f"{value:,.1f}".replace(",", ".")


def _pct(value: Decimal) -> str:
    return f"{value * Decimal('100'):.1f}%"
def _date_window(start, end) -> str:
    return f"{start.isoformat()} to {end.isoformat()}"


def _category_label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()
def _compact_money(value: Decimal) -> str:
    absolute = abs(value)

    if absolute >= Decimal("1000000"):
        return f"COP {value / Decimal('1000000'):.1f}M"

    if absolute >= Decimal("1000"):
        return f"COP {value / Decimal('1000'):.0f}K"

    return _money(value)


def _format_wow_value(metric: WeekOverWeekMetric) -> str:
    if metric.value_format == "count":
        return _count(int(metric.value))

    if metric.value_format == "money":
        return _compact_money(metric.value)

    if metric.value_format == "percent":
        return _pct(metric.value)

    return str(metric.value)


def _format_wow_delta(metric: WeekOverWeekMetric) -> str | None:
    if metric.delta is None:
        return None

    if metric.delta_unit == "percentage_points":
        return f"{metric.delta * Decimal('100'):+.1f} pp"

    if metric.value_format == "count":
        return f"{int(metric.delta):+d}"

    if metric.value_format == "money":
        return _compact_money(metric.delta)

    if metric.value_format == "percent":
        return _pct(metric.delta)

    return str(metric.delta)


def _wow_delta_color(metric: WeekOverWeekMetric) -> str:
    if metric.color == "neutral":
        return "off"

    return "normal" if metric.higher_is_better else "inverse"
def render_dashboard_load_error(error: Exception) -> None:
    st.error(
        "Dashboard data could not be loaded. "
        "Refresh the page or check the Sheets connection."
    )
    st.caption(f"Technical detail: {type(error).__name__}")


def render_todays_pulse(
    result: TodaysPulse,
    status_strip: TodaysStatusStrip | None = None,
) -> None:
    st.subheader("Today's pulse")

    if result.orders_count == 0:
        st.caption(EMPTY_TODAY)

    orders_label = _count(result.orders_count)
    revenue_label = _money(result.revenue)
    aov_label = _money(result.aov)

    rows = [
        {"Metric": "Orders", "Value": orders_label},
        {"Metric": "Revenue", "Value": revenue_label},
        {"Metric": "AOV", "Value": aov_label},
    ]

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )

    if status_strip is not None:
        st.caption(
            "Today status: "
            f"completed {_count(status_strip.completed)} \u00b7 "
            f"pending {_count(status_strip.pending)} · "
            f"cancelled {_count(status_strip.cancelled)}"
        )


def render_week_trend(result: list[WeekTrendDay]) -> None:
    st.subheader("Week trend")

    if not result:
        st.caption(EMPTY_WEEK)
        return

    window_start = result[0].date
    window_end = result[-1].date
    st.caption(f"Window: {_date_window(window_start, window_end)}")

    if all(item.orders_count == 0 for item in result):
        st.caption(EMPTY_WEEK)
        return

    chart_rows = [
        {
            "date": item.date.isoformat(),
            "weekday": WEEKDAY_LABELS[item.date.weekday()],
            "orders_count": item.orders_count,
            "revenue": float(item.revenue),
        }
        for item in result
    ]
    table_rows = [
        {
            "Date": item.date.isoformat(),
            "Weekday": WEEKDAY_LABELS[item.date.weekday()],
            "Orders": _count(item.orders_count),
            "Revenue": _money(item.revenue),
        }
        for item in result
    ]

    chart_data = pd.DataFrame(chart_rows)

    orders_chart = (
        alt.Chart(chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X("weekday:O", title="Weekday", sort=WEEKDAY_SORT),
            y=alt.Y("orders_count:Q", title="Orders"),
            tooltip=[
                alt.Tooltip("date:O", title="Date"),
                alt.Tooltip("weekday:O", title="Weekday"),
                alt.Tooltip("orders_count:Q", title="Orders"),
            ],
        )
        .properties(height=180)
    )

    revenue_chart = (
        alt.Chart(chart_data)
        .mark_bar()
        .encode(
            x=alt.X("weekday:O", title="Weekday", sort=WEEKDAY_SORT),
            y=alt.Y("revenue:Q", title="Revenue"),
            tooltip=[
                alt.Tooltip("date:O", title="Date"),
                alt.Tooltip("weekday:O", title="Weekday"),
                alt.Tooltip("revenue:Q", title="Revenue", format=",.0f"),
            ],
        )
        .properties(height=180)
    )

    st.altair_chart(orders_chart, use_container_width=True)
    st.altair_chart(revenue_chart, use_container_width=True)
    st.dataframe(
        pd.DataFrame(table_rows),
        hide_index=True,
        use_container_width=True,
    )
def render_week_over_week(result: WeekOverWeekResult) -> None:
    st.subheader("Week over week")
    st.caption(
        f"Current: {_date_window(result.current_period.start_date, result.current_period.end_date)}"
    )

    if result.previous_period is None:
        st.caption("No prior week to compare.")
    else:
        st.caption(
            "Previous: "
            f"{_date_window(result.previous_period.start_date, result.previous_period.end_date)}"
        )

    metric_rows = [
        result.metrics[:2],
        result.metrics[2:],
    ]

    for metric_row in metric_rows:
        columns = st.columns(2)

        for column, metric in zip(columns, metric_row):
            with column:
                st.metric(
                    metric.label,
                    _format_wow_value(metric),
                    delta=_format_wow_delta(metric),
                    delta_color=_wow_delta_color(metric),
                )
def render_status_breakdown(result: StatusBreakdown) -> None:
    st.subheader("Status breakdown")

    total_orders = result.draft + result.confirmed + result.completed + result.cancelled
    if total_orders == 0:
        st.caption(EMPTY_PERIOD)
        return

    chart_data = pd.DataFrame(
        [
            {"status": "draft", "orders_count": result.draft},
            {"status": "confirmed", "orders_count": result.confirmed},
            {"status": "completed", "orders_count": result.completed},
            {"status": "cancelled", "orders_count": result.cancelled},
        ]
    )
    table_data = pd.DataFrame(
        [
            {"Status": "Draft", "Orders": _count(result.draft)},
            {"Status": "Confirmed", "Orders": _count(result.confirmed)},
            {"Status": "Completed", "Orders": _count(result.completed)},
            {"Status": "Cancelled", "Orders": _count(result.cancelled)},
        ]
    )

    st.bar_chart(chart_data, x="status", y="orders_count")
    st.dataframe(table_data, hide_index=True, use_container_width=True)


def render_customer_mix(result: CustomerMix) -> None:
    st.subheader("Customer mix")
    st.caption("Window: current reference week")

    total_customers = result.new_customers + result.repeat_customers
    if total_customers == 0:
        st.caption(EMPTY_WEEK)
        return

    col_new, col_repeat = st.columns(2)

    with col_new:
        st.metric("New customers", _count(result.new_customers), _pct(result.new_pct))

    with col_repeat:
        st.metric(
            "Repeat customers",
            _count(result.repeat_customers),
            _pct(result.repeat_pct),
        )

    chart_data = pd.DataFrame(
        [
            {"type": "new", "customers": result.new_customers},
            {"type": "repeat", "customers": result.repeat_customers},
        ]
    )

    st.bar_chart(chart_data, x="type", y="customers")


def render_top_customers(result: TopCustomersResult) -> None:
    st.subheader("Top customers")
    st.caption("Window: current reference week")

    if not result.entries:
        st.caption(EMPTY_WEEK)
        return

    rows = [
        {
            "Name": entry.customer_name,
            "Orders": _count(entry.order_count),
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

    if not result.entries:
        st.caption(EMPTY_WEEK)
        return

    rows = [
        {
            "Product": entry.product_name,
            "Quantity": _quantity(entry.quantity),
            "Revenue": _money(entry.revenue),
        }
        for entry in result.entries
    ]

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )
def render_top_items_by_category(result: TopItemsByCategoryResult) -> None:
    st.subheader("Top items by category")
    st.caption(f"Window: {_date_window(result.week_start, result.week_end)}")

    if not result.entries:
        st.caption(EMPTY_WEEK)
        return

    rows = [
        {
            "Category": _category_label(entry.category),
            "Product": entry.product_name,
            "Quantity": _quantity(entry.quantity),
            "Revenue": _money(entry.revenue),
        }
        for entry in result.entries
    ]

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )

def render_time_of_day_heatmap(result: TimeOfDayHeatmapResult) -> None:
    st.subheader("Time-of-day heatmap")
    st.caption(
        f"Trailing window: {result.window_start.isoformat()} "
        f"to {result.window_end.isoformat()}"
    )

    if all(cell.order_count == 0 for cell in result.cells):
        st.caption(EMPTY_PERIOD)

    rows = [
        {
            "weekday": cell.weekday,
            "weekday_label": WEEKDAY_LABELS[cell.weekday],
            "hour": cell.hour,
            "order_count": cell.order_count,
        }
        for cell in result.cells
    ]

    chart_data = pd.DataFrame(rows)

    chart = (
        alt.Chart(chart_data)
        .mark_rect()
        .encode(
            x=alt.X("hour:O", title="Hour", sort=list(range(24))),
            y=alt.Y("weekday_label:O", title="Weekday", sort=WEEKDAY_SORT),
            color=alt.Color(
                "order_count:Q",
                title="Orders",
                scale=alt.Scale(scheme="blues"),
            ),
            tooltip=[
                alt.Tooltip("weekday_label:O", title="Weekday"),
                alt.Tooltip("hour:O", title="Hour"),
                alt.Tooltip("order_count:Q", title="Orders"),
            ],
        )
        .properties(height=260)
    )

    st.altair_chart(chart, use_container_width=True)


def render_product_pairs(result: ProductPairsResult) -> None:
    st.subheader("Items frequently ordered together")
    st.caption(f"Window starts: {result.week_start.isoformat()}")

    if not result.pairs:
        st.caption(EMPTY_WEEK)
        return

    rows = [
        {
            "Pair": f"{entry.product_name_a} + {entry.product_name_b}",
            "Count": _count(entry.count),
        }
        for entry in result.pairs
    ]

    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        use_container_width=True,
    )