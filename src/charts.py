from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go


def _series_label(spec: dict[str, Any], key: str, default: str) -> str:
    return spec.get("series", {}).get(key, {}).get("label", default)


def _series_color(spec: dict[str, Any], key: str, default: str) -> str:
    return spec.get("series", {}).get(key, {}).get("color", default)


def _money_hover(label: str, unit_label: str) -> str:
    return f"{label}<br>%{{x}}<br>%{{y:,}} {unit_label}<extra></extra>"


def weekly_purchase_sales_figure(df: pd.DataFrame, spec: dict[str, Any]) -> go.Figure:
    unit_label = spec.get("unit_label", "원")
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=df["label"] if "label" in df else [],
            y=df["purchase_amount"] if "purchase_amount" in df else [],
            name=_series_label(spec, "purchase_amount", "매입금액"),
            marker_color=_series_color(spec, "purchase_amount", "#2f6f7e"),
            hovertemplate=_money_hover(_series_label(spec, "purchase_amount", "매입금액"), unit_label),
        )
    )
    figure.add_trace(
        go.Bar(
            x=df["label"] if "label" in df else [],
            y=df["sales_amount"] if "sales_amount" in df else [],
            name=_series_label(spec, "sales_amount", "매출금액"),
            marker_color=_series_color(spec, "sales_amount", "#c47a23"),
            hovertemplate=_money_hover(_series_label(spec, "sales_amount", "매출금액"), unit_label),
        )
    )
    figure.update_layout(
        title=spec.get("title", "주간 매입/매출 금액"),
        xaxis_title=spec.get("x_axis_title", "주간"),
        yaxis_title=spec.get("y_axis_title", "금액"),
        barmode="group",
        template="plotly_white",
        height=spec.get("height", 380),
        margin={"l": 60, "r": 20, "t": 58, "b": 90},
        legend={"orientation": "h", "y": 1.12, "x": 0},
    )
    figure.update_yaxes(tickformat=",")
    return figure


def principal_margin_figure(df: pd.DataFrame, spec: dict[str, Any]) -> go.Figure:
    unit_label = spec.get("unit_label", "원")
    principals = df["principal"] if "principal" in df else []
    margin_text = (
        df["margin_rate"].map(lambda value: "-" if pd.isna(value) else f"{value:.1f}%")
        if "margin_rate" in df
        else []
    )
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            y=principals,
            x=df["sales_amount"] if "sales_amount" in df else [],
            orientation="h",
            name=_series_label(spec, "sales_amount", "매출금액"),
            marker_color=_series_color(spec, "sales_amount", "#2f6f7e"),
            text=margin_text,
            textposition="outside",
            cliponaxis=False,
            hovertemplate=f"%{{y}}<br>{_series_label(spec, 'sales_amount', '매출금액')}: %{{x:,}} {unit_label}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Bar(
            y=principals,
            x=df["cost_amount"] if "cost_amount" in df else [],
            orientation="h",
            name=_series_label(spec, "cost_amount", "매출원가"),
            marker_color=_series_color(spec, "cost_amount", "#7b8794"),
            hovertemplate=f"%{{y}}<br>{_series_label(spec, 'cost_amount', '매출원가')}: %{{x:,}} {unit_label}<extra></extra>",
        )
    )
    figure.update_layout(
        title=spec.get("title", "원청별 매출/원가/마진"),
        xaxis_title=spec.get("x_axis_title", "금액"),
        yaxis_title=spec.get("y_axis_title", "원청"),
        barmode="group",
        template="plotly_white",
        height=spec.get("height", 420),
        margin={"l": 150, "r": 130, "t": 58, "b": 45},
        legend={"orientation": "h", "y": 1.12, "x": 0},
    )
    figure.update_xaxes(tickformat=",")
    figure.update_yaxes(autorange="reversed")
    return figure
