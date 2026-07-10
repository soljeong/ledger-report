#!/usr/bin/env python3
from __future__ import annotations

import copy
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

import generate_analysis_report_html_core as _base
from generate_analysis_report_html_core import *  # noqa: F401,F403
from src.charts import weekly_inventory_flow_figure


def _numeric_frame(records: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.DataFrame(records).copy()
    for column in columns:
        if column not in frame:
            frame[column] = 0
        frame[column] = pd.to_numeric(frame[column], errors="coerce").fillna(0)
    return frame


def weekly_inventory_flow_rows(
    purchase_records: list[dict[str, Any]],
    sales_records: list[dict[str, Any]],
    inventory_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Estimate weekly inventory value using current average cost as the outbound cost proxy."""
    purchase_df = _numeric_frame(purchase_records, ("quantity", "supply_amount", "product_id"))
    sales_df = _numeric_frame(sales_records, ("quantity", "supply_amount", "product_id"))
    inventory_df = _numeric_frame(inventory_records, ("stock_quantity", "average_cost", "product_id"))

    for frame in (purchase_df, sales_df):
        if "date" not in frame:
            frame["date"] = pd.Series(dtype="datetime64[ns]")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame.dropna(subset=["date"], inplace=True)

    date_starts = [
        value
        for value in (
            purchase_df["date"].min() if not purchase_df.empty else pd.NaT,
            sales_df["date"].min() if not sales_df.empty else pd.NaT,
        )
        if not pd.isna(value)
    ]
    date_ends = [
        value
        for value in (
            purchase_df["date"].max() if not purchase_df.empty else pd.NaT,
            sales_df["date"].max() if not sales_df.empty else pd.NaT,
        )
        if not pd.isna(value)
    ]
    if not date_starts or not date_ends:
        return []

    inventory_df["current_stock_amount"] = inventory_df["stock_quantity"] * inventory_df["average_cost"]
    current_stock_amount = float(inventory_df["current_stock_amount"].sum())

    if sales_df.empty:
        sales_cost = sales_df.copy()
        sales_cost["sales_cost_proxy"] = pd.Series(dtype="float64")
    else:
        sales_cost = sales_df.merge(
            inventory_df[["product_id", "average_cost"]],
            on="product_id",
            how="left",
        )
        sales_cost["average_cost"] = pd.to_numeric(sales_cost["average_cost"], errors="coerce").fillna(0)
        sales_cost["sales_cost_proxy"] = sales_cost["quantity"] * sales_cost["average_cost"]

    daily = pd.DataFrame(index=pd.date_range(min(date_starts), max(date_ends), freq="D"))
    daily["purchase_increase"] = purchase_df.groupby("date")["supply_amount"].sum()
    daily["outbound_cost_estimate"] = sales_cost.groupby("date")["sales_cost_proxy"].sum()
    daily = daily.fillna(0)
    daily["net_change"] = daily["purchase_increase"] - daily["outbound_cost_estimate"]

    opening_stock_amount = current_stock_amount - float(daily["net_change"].sum())
    daily["estimated_inventory_amount"] = opening_stock_amount + daily["net_change"].cumsum()
    weekly = daily.resample("W-MON").agg(
        {
            "purchase_increase": "sum",
            "outbound_cost_estimate": "sum",
            "net_change": "sum",
            "estimated_inventory_amount": "last",
        }
    )

    return [
        {
            "week_end": week_end.date().isoformat(),
            "label": week_end.strftime("%m/%d"),
            "purchase_increase": float(row["purchase_increase"]),
            "outbound_cost_estimate": float(row["outbound_cost_estimate"]),
            "net_change": float(row["net_change"]),
            "estimated_inventory_amount": float(row["estimated_inventory_amount"]),
        }
        for week_end, row in weekly.iterrows()
    ]


def render_weekly_inventory_flow_table_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <th>{escape(row['week_end'])}</th>
            <td class="money">{_base.money(row['purchase_increase'])}</td>
            <td class="money">{_base.money(row['outbound_cost_estimate'])}</td>
            <td class="money">{_base.money(row['net_change'])}</td>
            <td class="money">{_base.money(row['estimated_inventory_amount'])}</td>
          </tr>
        """
        for row in rows
    )


def render_report_html(sources: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    spec = spec or _base.load_report_spec()
    base_spec = copy.deepcopy(spec)
    base_spec.setdefault("plotly", {})["include_plotlyjs"] = False
    html = _base.render_report_html(sources, base_spec)

    chart_spec = spec["charts"]["weekly_inventory_flow"]
    rows = weekly_inventory_flow_rows(
        sources["purchase"]["records"],
        sources["sales"]["records"],
        sources["inventory"]["records"],
    )
    include_plotlyjs = _base.include_plotlyjs_option(spec.get("plotly", {}).get("include_plotlyjs", "inline"))
    chart = _base.figure_html(
        weekly_inventory_flow_figure(pd.DataFrame(rows), chart_spec),
        include_plotlyjs=include_plotlyjs,
    )
    inserted = f"""
      <h3 id="inventory-flow-title" style="margin:28px 0 10px;font-size:17px;">{escape(chart_spec['title'])}</h3>
      <p class="section-note">매입은 공급금액으로 증가, 출고는 매출수량 × 현재 평균원가로 감소시켰다. 현재재고에서 기간 순증감을 역산해 기초재고를 추정한다.</p>
      <p class="section-note">주 종료일은 W-MON 기준이며, 막대는 주간 증감·선은 주말 추정 재고금액이다.</p>
      <p class="section-note">표시 단위: {escape(chart_spec.get('unit_label', '백만원'))}</p>
      {chart}
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>주 종료일</th>
              <th class="money">매입 증가</th>
              <th class="money">출고 감소(추정원가)</th>
              <th class="money">순증감</th>
              <th class="money">추정 재고금액</th>
            </tr>
          </thead>
          <tbody>{render_weekly_inventory_flow_table_rows(rows)}</tbody>
        </table>
      </div>
    """
    marker = (
        '      <p class="formula">나머지 = 매출금액 + 재고금액 - 매입금액. '
        '재고금액은 평균원가 기준이다.</p>\n    </section>'
    )
    if marker not in html:
        raise RuntimeError("amount reconciliation section marker not found")
    return html.replace(marker, marker.removesuffix("\n    </section>") + inserted + "\n    </section>", 1)


def generate_report(input_dir: Path, output_path: Path, spec_path: Path = _base.DEFAULT_SPEC) -> None:
    sources = _base.load_sources(input_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report_html(sources, _base.load_report_spec(spec_path)), encoding="utf-8")


def main() -> int:
    args = _base.parse_args()
    generate_report(args.input_dir, args.output, args.spec)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
