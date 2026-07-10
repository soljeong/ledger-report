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


def _unique_value_count(records: list[dict[str, Any]], key: str) -> int:
    return len(
        {
            str(value).strip()
            for row in records
            if (value := row.get(key)) not in (None, "") and str(value).strip()
        }
    )


def _replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise RuntimeError(f"{label} replacement marker not found")
    return text.replace(old, new, 1)


def _replace_between(
    text: str,
    start_marker: str,
    end_marker: str,
    replacement: str,
    *,
    start_at: int = 0,
    label: str,
) -> str:
    start = text.find(start_marker, start_at)
    if start < 0:
        raise RuntimeError(f"{label} start marker not found")
    content_start = start + len(start_marker)
    end = text.find(end_marker, content_start)
    if end < 0:
        raise RuntimeError(f"{label} end marker not found")
    return text[:content_start] + replacement + text[end:]


def render_amount_balance_chart(
    purchase_amount: int | float,
    sales_amount: int | float,
    inventory_amount: int | float,
    remainder_amount: int | float,
) -> str:
    """Render the balance chart while naming the residual as a reconciliation amount."""
    chart = _base.render_amount_balance_chart(
        purchase_amount,
        sales_amount,
        inventory_amount,
        remainder_amount,
    )
    replacements = (
        ("매입·매출·재고·이익", "매입·매출·재고·대사 잔여금액"),
        ("매출과 재고의 합계, 이익과 매입의 합계", "매출과 재고의 합계, 대사 잔여금액과 매입의 합계"),
        ("오른쪽은 이익금액 위에 매입금액을", "오른쪽은 대사 잔여금액 위에 매입금액을"),
        (">이익</text>", ">대사 잔여</text>"),
        (">이익 + 매입</text>", ">대사 잔여 + 매입</text>"),
        (">남는 금액과 투입액</text>", ">대사 차이와 투입액</text>"),
    )
    for old, new in replacements:
        chart = chart.replace(old, new)
    return chart


def weekly_inventory_flow_rows(
    purchase_records: list[dict[str, Any]],
    sales_records: list[dict[str, Any]],
    inventory_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Estimate weekly inventory value using current average cost as the outbound cost proxy."""
    purchase_df = _numeric_frame(purchase_records, ("quantity", "supply_amount", "product_id"))
    sales_df = _numeric_frame(sales_records, ("quantity", "supply_amount", "total_amount", "product_id"))
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
    daily["sales_amount"] = sales_df.groupby("date")["total_amount"].sum()
    daily["outbound_cost_estimate"] = sales_cost.groupby("date")["sales_cost_proxy"].sum()
    daily = daily.fillna(0)
    daily["net_change"] = daily["purchase_increase"] - daily["outbound_cost_estimate"]

    opening_stock_amount = current_stock_amount - float(daily["net_change"].sum())
    daily["estimated_inventory_amount"] = opening_stock_amount + daily["net_change"].cumsum()
    daily["week_start"] = [
        _base.week_start(timestamp.date().isoformat()).isoformat() for timestamp in daily.index.to_pydatetime()
    ]
    weekly = daily.groupby("week_start", sort=True).agg(
        {
            "purchase_increase": "sum",
            "sales_amount": "sum",
            "outbound_cost_estimate": "sum",
            "net_change": "sum",
            "estimated_inventory_amount": "last",
        }
    )

    return [
        {
            "week_start": week_start,
            "label": f"{pd.Timestamp(week_start).month}/{pd.Timestamp(week_start).day}",
            "purchase_increase": float(row["purchase_increase"]),
            "sales_amount": float(row["sales_amount"]),
            "outbound_cost_estimate": float(row["outbound_cost_estimate"]),
            "net_change": float(row["net_change"]),
            "estimated_inventory_amount": float(row["estimated_inventory_amount"]),
        }
        for week_start, row in weekly.iterrows()
    ]


def render_weekly_inventory_flow_table_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <th>{escape(row['label'])}</th>
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

    purchase_meta = sources["purchase"]["metadata"]
    sales_meta = sources["sales"]["metadata"]
    inventory_meta = sources["inventory"]["metadata"]
    purchase_records = sources["purchase"]["records"]
    sales_records = sources["sales"]["records"]
    inventory_records = sources["inventory"]["records"]
    reconciliation = sources["reconciliation"]["summary"]

    summary_rows_html = "\n".join(
        [
            _base.summary_row("분석 기간", _base.period_label(purchase_meta, sales_meta)),
            _base.summary_row(
                "거래 규모",
                f"매입 상세 {_base.number(purchase_meta['record_count'])}건 / 매출 상세 {_base.number(sales_meta['record_count'])}건",
            ),
            _base.summary_row(
                "분석 대상",
                f"매입처 {_base.number(_unique_value_count(purchase_records, 'company'))}곳 / "
                f"매출처 {_base.number(_unique_value_count(sales_records, 'company'))}곳",
                f"재고 품목 {_base.number(inventory_meta['unique_product_ids'])}개",
            ),
        ]
    )
    html = _replace_between(
        html,
        '        <dl class="summary-list">\n',
        "        </dl>",
        f"          {summary_rows_html}\n",
        label="summary list",
    )

    purchase_amount = reconciliation["purchase_amount"]
    sales_amount = reconciliation["sales_amount"]
    inventory_amount = reconciliation["inventory_amount_at_average_cost"]
    reconciliation_remainder = reconciliation["remainder_at_average_cost"]
    inventory_cost_by_product = {
        row.get("product_id"): float(row.get("average_cost") or 0)
        for row in inventory_records
        if row.get("product_id") is not None
    }
    estimated_sales_cost = int(
        round(
            sum(
                (row.get("quantity") or 0) * inventory_cost_by_product.get(row.get("product_id"), 0)
                for row in sales_records
            )
        )
    )

    original_balance_chart = _base.render_amount_balance_chart(
        purchase_amount,
        sales_amount,
        inventory_amount,
        reconciliation_remainder,
    )
    amount_balance_chart = render_amount_balance_chart(
        purchase_amount,
        sales_amount,
        inventory_amount,
        reconciliation_remainder,
    )
    html = _replace_once(html, original_balance_chart, amount_balance_chart, "amount balance chart")

    amount_rows = [
        ("총 매입금액", "기간 내 매입 상세 합계", _base.money(purchase_amount)),
        ("총 매출금액", "기간 내 매출 상세 합계", _base.money(sales_amount)),
        ("추정 매출원가", "매출수량 × 현재 평균원가", _base.money(estimated_sales_cost)),
        ("현재 재고금액", "현재 재고수량 × 평균원가", _base.money(inventory_amount)),
        (
            "대사 잔여금액",
            "총 매출금액 + 현재 재고금액 - 총 매입금액",
            _base.money(reconciliation_remainder),
        ),
    ]
    amount_table = "\n".join(
        f"""
          <tr>
            <th>{escape(label)}</th>
            <td>{escape(description)}</td>
            <td class="money">{escape(value)}</td>
          </tr>
        """
        for label, description, value in amount_rows
    )
    amount_table_start = html.index('<table class="amount-table">')
    html = _replace_between(
        html,
        "          <tbody>\n",
        "          </tbody>",
        f"            {amount_table}\n",
        start_at=amount_table_start,
        label="amount table body",
    )
    html = _replace_once(
        html,
        '<p class="formula">이익 = 매출금액 + 재고금액 - 매입금액. 재고금액은 평균원가 기준이다.</p>',
        '<p class="formula">추정 매출원가 = 매출수량 × 현재 평균원가. '
        '대사 잔여금액 = 총 매출금액 + 현재 재고금액 - 총 매입금액.</p>\n'
        '        <p class="section-note">추정 매출원가는 매출수량 × 현재 평균원가 기준이며 확정 회계 원가와 다를 수 있다.</p>',
        "amount reconciliation formula",
    )

    chart_spec = spec["charts"]["weekly_inventory_flow"]
    rows = weekly_inventory_flow_rows(
        purchase_records,
        sales_records,
        inventory_records,
    )
    include_plotlyjs = _base.include_plotlyjs_option(spec.get("plotly", {}).get("include_plotlyjs", "inline"))
    chart = _base.figure_html(
        weekly_inventory_flow_figure(pd.DataFrame(rows), chart_spec),
        include_plotlyjs=include_plotlyjs,
    )
    inserted = f"""
    <article class="report-page report-page--analysis">
      <section class="page-panel" aria-labelledby="inventory-flow-title">
        <h2 id="inventory-flow-title">{escape(chart_spec['title'])}</h2>
        <p class="section-note">매입은 공급금액으로 증가, 출고는 매출수량 × 현재 평균원가로 감소시켰다. 현재재고에서 기간 순증감을 역산해 기초재고를 추정한다.</p>
        <p class="section-note">주 시작일 기준으로 묶었고, 막대는 주간 매입 증가·주간 출고 감소·주간 매출금액, 선은 주말 추정 재고금액이다.</p>
        <p class="section-note">표시 단위: {escape(chart_spec.get('unit_label', '백만원'))}</p>
        <div class="legend">
          <span><i class="purchase-chip"></i>{escape(chart_spec["series"]["purchase_increase"]["label"])}</span>
          <span><i class="outbound-chip"></i>{escape(chart_spec["series"]["outbound_cost_estimate"]["label"])}</span>
          <span><i class="revenue-chip"></i>{escape(chart_spec["series"]["sales_amount"]["label"])}</span>
          <span><i class="inventory-chip"></i>{escape(chart_spec["series"]["estimated_inventory_amount"]["label"])}</span>
        </div>
        {chart}
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>주 시작일</th>
                <th class="money">매입 증가</th>
                <th class="money">출고 감소(추정원가)</th>
                <th class="money">순증감</th>
                <th class="money">추정 재고금액</th>
              </tr>
            </thead>
            <tbody>{render_weekly_inventory_flow_table_rows(rows)}</tbody>
          </table>
        </div>
      </section>
    </article>
    """
    marker = "    <!-- REPORT_EXTRA_PAGES -->"
    if marker not in html:
        raise RuntimeError("inventory page insertion marker not found")
    return html.replace(marker, inserted, 1)


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
