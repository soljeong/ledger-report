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
    reconciliation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build weekly inventory flow from period purchase costs and FIFO sale costs."""
    purchase_df = _numeric_frame(purchase_records, ("quantity", "supply_amount", "unit_price", "product_id"))
    sales_df = _numeric_frame(sales_records, ("quantity", "supply_amount", "total_amount", "product_id"))

    for frame in (purchase_df, sales_df):
        if "date" not in frame:
            frame["date"] = pd.Series(dtype="datetime64[ns]")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame.dropna(subset=["date"], inplace=True)

    metadata = (reconciliation or {}).get("metadata", {})
    period_start = metadata.get("period_start")
    period_end = metadata.get("period_end")
    if period_start and period_end:
        purchase_df = purchase_df[(purchase_df["date"] >= pd.Timestamp(period_start)) & (purchase_df["date"] <= pd.Timestamp(period_end))].copy()
        sales_df = sales_df[(sales_df["date"] >= pd.Timestamp(period_start)) & (sales_df["date"] <= pd.Timestamp(period_end))].copy()

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

    fifo_costs = {
        row["sale_id"]: float(row.get("fifo_cost_amount") or 0)
        for row in (reconciliation or {}).get("sales_allocations", [])
        if row.get("in_analysis_period")
    }
    sales_df["sale_id"] = sales_df.apply(
        lambda row: f"{row.get('date').date().isoformat()}|{row.get('voucher')}|{row.get('excel_row')}", axis=1
    ) if not sales_df.empty else pd.Series(dtype="object")
    sales_df["fifo_cost"] = sales_df["sale_id"].map(fifo_costs).fillna(0) if not sales_df.empty else pd.Series(dtype="float64")
    purchase_df["purchase_cost"] = purchase_df["quantity"] * purchase_df.get("unit_price", 0) if "unit_price" in purchase_df else purchase_df["supply_amount"]

    daily = pd.DataFrame(index=pd.date_range(min(date_starts), max(date_ends), freq="D"))
    daily["purchase_increase"] = purchase_df.groupby("date")["purchase_cost"].sum()
    daily["sales_amount"] = sales_df.groupby("date")["total_amount"].sum()
    daily["outbound_cost_estimate"] = sales_df.groupby("date")["fifo_cost"].sum()
    daily = daily.fillna(0)
    daily["net_change"] = daily["purchase_increase"] - daily["outbound_cost_estimate"]

    opening_stock_amount = float((reconciliation or {}).get("summary", {}).get("opening_stock_amount", 0))
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
    reconciliation_payload = sources["reconciliation"]
    reconciliation = reconciliation_payload["summary"]

    summary_rows_html = "\n".join(
        [
            _base.summary_row("분석 기간", reconciliation_payload.get("metadata", {}).get("period_start", _base.period_label(purchase_meta, sales_meta)) + " ~ " + reconciliation_payload.get("metadata", {}).get("period_end", "")),
            _base.summary_row("재고 기준일", reconciliation_payload.get("metadata", {}).get("inventory_date", "-")),
            _base.summary_row("입력 데이터 최종 거래일", reconciliation_payload.get("metadata", {}).get("input_data_last_transaction_date", "-")),
            _base.summary_row("후속 원가보충 최종 매입일", reconciliation_payload.get("metadata", {}).get("backfill_last_purchase_date", "없음")),
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

    purchase_amount = reconciliation.get("period_purchase_cost_amount", reconciliation["purchase_amount"])
    sales_amount = reconciliation["sales_amount"]
    inventory_amount = reconciliation.get("inventory_amount_at_fifo", reconciliation.get("ending_fifo_inventory_amount", 0))
    reconciliation_remainder = reconciliation.get("remainder_at_fifo", reconciliation.get("gross_profit", 0))
    fifo_sales_cost = reconciliation.get("fifo_sales_cost_amount", 0)
    status_rows = "".join(
        f"<li><code>{escape(status)}</code>: {_base.number(count)}개 품목</li>"
        for status, count in reconciliation.get("cost_status_counts", {}).items()
    )
    reconciliation_rows = "".join(
        f"<li>{escape(status)}: {_base.number(count)}개</li>"
        for status, count in reconciliation.get("reconciliation_status_counts", {}).items()
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
        ("총 매입원가", "분석기간 매입수량 × 매입단가", _base.money(purchase_amount)),
        ("총 매출금액", "기간 내 매출 상세 합계", _base.money(sales_amount)),
        ("FIFO 매출원가", "각 매출에 실제 배정된 FIFO 원가층 합계", _base.money(fifo_sales_cost)),
        ("FIFO 재고금액", "분석 종료일 정상 잔여 원가층 합계", _base.money(inventory_amount)),
        (
            "매출총이익",
            "매출금액 + FIFO 재고금액 - 매입원가",
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
        '<p class="formula">FIFO 매출원가와 FIFO 잔여 원가층을 사용했다. 재고 시트의 평균원가·최종매입가는 계산에 사용하지 않는다.</p>',
        '<p class="formula">FIFO 매출원가는 매출별 원가배정 합계다. FIFO 재고금액은 분석 종료일 정상 잔여 원가층 합계다.</p>\n'
        '        <p class="section-note">후속 매입은 종료일 이전 미확정 출고를 확정하는 데만 사용하며 종료일 재고금액에는 포함하지 않는다.</p>',
        "amount reconciliation formula",
    )

    chart_spec = spec["charts"]["weekly_inventory_flow"]
    rows = weekly_inventory_flow_rows(
        purchase_records,
        sales_records,
        inventory_records,
        reconciliation_payload,
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
        <p class="section-note">매입은 매입단가 기준 원가로 증가, 출고는 매출별 FIFO 배정원가로 감소시켰다.</p>
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
                <th class="money">출고 감소(FIFO 원가)</th>
                <th class="money">순증감</th>
                <th class="money">추정 재고금액</th>
              </tr>
            </thead>
            <tbody>{render_weekly_inventory_flow_table_rows(rows)}</tbody>
          </table>
        </div>
        <section aria-labelledby="fifo-status-title">
          <h3 id="fifo-status-title">FIFO 원가 상태</h3>
          <ul>{status_rows}</ul>
          <p class="section-note">미확정 출고 {escape(_base.number(reconciliation.get('unconfirmed_quantity', 0)))}개 · 오류 {_base.number(reconciliation.get('error_count', 0))}건</p>
        </section>
        <section aria-labelledby="stock-reconciliation-title">
          <h3 id="stock-reconciliation-title">재고 기준일 수량 대사</h3>
          <p class="section-note">장부상 계산수량과 재고 시트의 stock_quantity만 비교했다. 재고 시트 단가는 사용하지 않았다.</p>
          <ul>{reconciliation_rows}</ul>
          <p class="section-note">불일치 품목 {_base.number(reconciliation.get('quantity_reconciliation_mismatch_count', 0))}개</p>
        </section>
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
