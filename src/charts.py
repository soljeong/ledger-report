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
    margin_text = (
        df["margin_rate"].map(lambda value: "" if pd.isna(value) else f"{value:.1f}%")
        if "margin_rate" in df
        else []
    )
    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=df["label"] if "label" in df else [],
            y=df["sales_amount"] if "sales_amount" in df else [],
            name=_series_label(spec, "sales_amount", "매출금액"),
            marker_color=_series_color(spec, "sales_amount", "#b54708"),
            opacity=0.55,
            text=margin_text,
            textposition="outside",
            cliponaxis=False,
            hovertemplate=_money_hover(_series_label(spec, "sales_amount", "매출금액"), unit_label),
        )
    )
    figure.add_trace(
        go.Bar(
            x=df["label"] if "label" in df else [],
            y=df["cost_amount"] if "cost_amount" in df else [],
            name=_series_label(spec, "cost_amount", "매출원가"),
            marker_color=_series_color(spec, "cost_amount", "#c47a23"),
            hovertemplate=_money_hover(_series_label(spec, "cost_amount", "매출원가"), unit_label),
        )
    )
    figure.update_layout(
        title=None,
        xaxis_title=spec.get("x_axis_title", "주 시작일"),
        yaxis_title=spec.get("y_axis_title", "금액"),
        barmode="overlay",
        template="plotly_white",
        height=spec.get("height", 380),
        font={
            "family": "Pretendard, Noto Sans KR, Apple SD Gothic Neo, Segoe UI, sans-serif",
            "size": 15,
            "color": "#1f2933",
        },
        margin={"l": 60, "r": 20, "t": 24, "b": 90},
        showlegend=False,
    )
    figure.update_yaxes(tickformat=",")
    return figure


def weekly_inventory_flow_figure(df: pd.DataFrame, spec: dict[str, Any]) -> go.Figure:
    divisor = float(spec.get("value_divisor", 1_000_000)) or 1.0
    unit_label = spec.get("unit_label", "백만원")
    empty_numeric = pd.Series(0.0, index=df.index, dtype="float64")
    labels = df["label"] if "label" in df else pd.Series(dtype="object")
    purchase_values = pd.to_numeric(
        df["purchase_increase"] if "purchase_increase" in df else empty_numeric,
        errors="coerce",
    ).fillna(0) / divisor
    outbound_values = pd.to_numeric(
        df["outbound_cost_estimate"] if "outbound_cost_estimate" in df else empty_numeric,
        errors="coerce",
    ).fillna(0) / divisor
    sales_values = pd.to_numeric(
        df["sales_amount"] if "sales_amount" in df else empty_numeric,
        errors="coerce",
    ).fillna(0) / divisor
    inventory_values = pd.to_numeric(
        df["estimated_inventory_amount"] if "estimated_inventory_amount" in df else empty_numeric,
        errors="coerce",
    ).fillna(0) / divisor
    purchase_label = _series_label(spec, "purchase_increase", "주간 매입 증가")
    outbound_label = _series_label(spec, "outbound_cost_estimate", "주간 출고 감소(추정원가)")
    sales_label = _series_label(spec, "sales_amount", "주간 매출금액")
    inventory_label = _series_label(spec, "estimated_inventory_amount", "주말 추정 재고금액")

    figure = go.Figure()
    figure.add_trace(
        go.Bar(
            x=labels,
            y=purchase_values,
            name=purchase_label,
            marker_color=_series_color(spec, "purchase_increase", "#2f6f7e"),
            hovertemplate=f"%{{x}}<br>{purchase_label}: %{{y:,.2f}} {unit_label}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Bar(
            x=labels,
            y=-outbound_values,
            customdata=outbound_values,
            name=outbound_label,
            marker_color=_series_color(spec, "outbound_cost_estimate", "#c47a23"),
            hovertemplate=f"%{{x}}<br>{outbound_label}: %{{customdata:,.2f}} {unit_label}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Bar(
            x=labels,
            y=-sales_values,
            customdata=sales_values,
            name=sales_label,
            marker_color=_series_color(spec, "sales_amount", "#b54708"),
            opacity=0.55,
            hovertemplate=f"%{{x}}<br>{sales_label}: %{{customdata:,.2f}} {unit_label}<extra></extra>",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=labels,
            y=inventory_values,
            yaxis="y2",
            mode="lines+markers",
            name=inventory_label,
            line={"color": _series_color(spec, "estimated_inventory_amount", "#344054"), "width": 3},
            marker={"size": 8},
            hovertemplate=f"%{{x}}<br>{inventory_label}: %{{y:,.2f}} {unit_label}<extra></extra>",
        )
    )

    figure.update_layout(
        title=None,
        xaxis_title=spec.get("x_axis_title", "주 시작일"),
        yaxis={
            "title": spec.get("y_axis_title", f"주간 증감 금액 ({unit_label})"),
            "zeroline": True,
            "zerolinewidth": 1,
            "zerolinecolor": "#667085",
            "gridcolor": "#e4e7ec",
        },
        yaxis2={
            "title": spec.get("secondary_y_axis_title", f"주말 추정 재고금액 ({unit_label})"),
            "overlaying": "y",
            "side": "right",
            "showgrid": False,
        },
        barmode="overlay",
        bargap=0.34,
        template="plotly_white",
        height=spec.get("height", 440),
        font={
            "family": "Pretendard, Noto Sans KR, Apple SD Gothic Neo, Segoe UI, sans-serif",
            "size": 15,
            "color": "#1f2933",
        },
        margin={"l": 70, "r": 80, "t": 24, "b": 70},
        showlegend=False,
        hovermode="x unified",
    )
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
            marker_color=_series_color(spec, "sales_amount", "#b54708"),
            opacity=0.55,
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
            marker_color=_series_color(spec, "cost_amount", "#c47a23"),
            hovertemplate=f"%{{y}}<br>{_series_label(spec, 'cost_amount', '매출원가')}: %{{x:,}} {unit_label}<extra></extra>",
        )
    )
    figure.update_layout(
        title=None,
        xaxis_title=spec.get("x_axis_title", "금액"),
        yaxis_title=spec.get("y_axis_title", "원청"),
        barmode="overlay",
        template="plotly_white",
        height=spec.get("height", 420),
        font={
            "family": "Pretendard, Noto Sans KR, Apple SD Gothic Neo, Segoe UI, sans-serif",
            "size": 15,
            "color": "#1f2933",
        },
        margin={"l": 150, "r": 130, "t": 24, "b": 45},
        showlegend=False,
    )
    figure.update_xaxes(tickformat=",")
    figure.update_yaxes(autorange="reversed")
    return figure
