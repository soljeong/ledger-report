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
    opening_amount: int | float,
    purchase_amount: int | float,
    sales_amount: int | float,
    inventory_amount: int | float,
    backfill_amount: int | float,
    prior_shortage_settlement_amount: int | float,
    gross_profit: int | float,
) -> str:
    """Render the stated FIFO identity rather than a misleading four-block residual."""
    left_total = sales_amount + inventory_amount + prior_shortage_settlement_amount
    right_total = opening_amount + purchase_amount + backfill_amount + gross_profit
    if any(value < 0 for value in (sales_amount, inventory_amount, opening_amount, purchase_amount, backfill_amount, prior_shortage_settlement_amount, gross_profit)):
        return '<div class="balance-chart-empty" role="note">원가 미확정·오류 또는 음수 이익이 있어 금액 밸런스 차트는 확정값으로 표시하지 않는다.</div>'
    if round(left_total) != round(right_total):
        return f'<div class="balance-chart-empty" role="note">금액 밸런스 불일치: 왼쪽 {_base.money(left_total)}원 / 오른쪽 {_base.money(right_total)}원</div>'
    total = left_total or 1
    def segments(x: int, values: list[tuple[str, int | float]], color: str) -> str:
        y, result = 302.0, []
        for label, value in values:
            height = value / total * 220
            y -= height
            result.append(f'<rect x="{x}" y="{y:.1f}" width="190" height="{height:.1f}" rx="3" fill="{color}" opacity=".82"></rect><text x="{x + 95}" y="{y + height / 2:.1f}" text-anchor="middle">{escape(label)} {_base.money(value)}원</text>')
        return "".join(result)
    return f'''<div class="chart-wrap balance-chart-wrap" aria-label="금액 대사 밸런스 블록 차트">
      <svg id="amount-balance-chart" viewBox="0 0 980 410" role="img"><title>FIFO 금액 밸런스 차트</title><desc>매출과 종료일 FIFO 재고는 기초재고, 기간 순매입원가, 후속 소급배정원가 및 매출총이익의 합계와 같다.</desc>
      <text x="490" y="34" text-anchor="middle">양쪽 합계 {_base.money(left_total)}원</text>
      {segments(170, [('매출 공급가액', sales_amount), ('종료일 재고', inventory_amount), ('이전기간 부족 보충', prior_shortage_settlement_amount)], '#2f6f7e')}
      {segments(620, [('기초재고', opening_amount), ('기간 순매입', purchase_amount), ('후속 소급배정', backfill_amount), ('매출총이익', gross_profit)], '#c47a23')}
      <text x="265" y="350" text-anchor="middle">매출 공급가액 + 재고 + 이전기간 보충</text><text x="715" y="350" text-anchor="middle">기초재고 + 매입 + 소급배정 + 이익</text></svg></div>'''


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

    purchase_events = pd.DataFrame((reconciliation or {}).get("purchase_cost_events", [])).copy()
    sales_events = pd.DataFrame((reconciliation or {}).get("sales_cost_events", [])).copy()
    inventory_events = pd.DataFrame((reconciliation or {}).get("inventory_cost_events", [])).copy()
    for frame in (purchase_events, sales_events, inventory_events):
        if "date" not in frame:
            frame["date"] = pd.Series(dtype="datetime64[ns]")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
        frame.dropna(subset=["date"], inplace=True)
        if period_start and period_end:
            frame.drop(frame[(frame["date"] < pd.Timestamp(period_start)) | (frame["date"] > pd.Timestamp(period_end))].index, inplace=True)
    if purchase_events.empty:
        purchase_df["purchase_cost"] = purchase_df["quantity"] * purchase_df.get("unit_price", 0) if "unit_price" in purchase_df else purchase_df["supply_amount"]
    else:
        purchase_events["purchase_cost"] = pd.to_numeric(purchase_events["cost_amount"], errors="coerce").fillna(0)
    if sales_events.empty:
        fifo_costs = {
            row["sale_id"]: float(row.get("fifo_cost_amount") or 0)
            for row in (reconciliation or {}).get("sales_allocations", [])
            if row.get("in_analysis_period")
        }
        sales_df["sale_id"] = sales_df.apply(
            lambda row: f"{row.get('date').date().isoformat()}|{row.get('voucher')}|{row.get('excel_row')}", axis=1
        ) if not sales_df.empty else pd.Series(dtype="object")
        sales_df["outbound_cost"] = sales_df["sale_id"].map(fifo_costs).fillna(0) if not sales_df.empty else 0.0
        sales_df["inventory_cost_delta"] = -sales_df["outbound_cost"]
    else:
        sales_events["inventory_cost_amount"] = pd.to_numeric(sales_events.get("inventory_cost_amount", 0), errors="coerce").fillna(0)
        sales_events["cost_amount"] = pd.to_numeric(sales_events["cost_amount"], errors="coerce").fillna(0)
        sales_events["inventory_cost_delta"] = sales_events.apply(lambda row: row["inventory_cost_amount"] if row.get("event_type") == "sale_cancellation" else -row["inventory_cost_amount"], axis=1)
        sales_events["outbound_cost"] = sales_events.apply(lambda row: -row["cost_amount"] if row.get("event_type") == "sale_cancellation" else row["cost_amount"], axis=1)

    has_inventory_events = not inventory_events.empty
    if has_inventory_events:
        inventory_events["cost_amount"] = pd.to_numeric(inventory_events["cost_amount"], errors="coerce").fillna(0)
        purchase_flow = inventory_events[inventory_events["event_type"].isin(["purchase", "purchase_cancellation"])]
        outbound_flow = inventory_events[~inventory_events["event_type"].isin(["purchase", "purchase_cancellation"])]
    else:
        purchase_flow = purchase_events if not purchase_events.empty else purchase_df
        outbound_flow = sales_events
    daily = pd.DataFrame(index=pd.date_range(min(date_starts), max(date_ends), freq="D"))
    daily["purchase_increase"] = purchase_flow.groupby("date")["cost_amount" if "cost_amount" in purchase_flow else "purchase_cost"].sum()
    sales_column = "supply_amount" if "supply_amount" in sales_df else "total_amount"
    daily["sales_amount"] = sales_df.groupby("date")[sales_column].sum()
    daily["outbound_cost_estimate"] = (
        -outbound_flow.groupby("date")["cost_amount"].sum()
        if has_inventory_events and not outbound_flow.empty
        else (sales_events.groupby("date")["outbound_cost"].sum() if not sales_events.empty else sales_df.groupby("date")["outbound_cost"].sum())
    )
    daily = daily.fillna(0)
    inventory_cost_delta = (
        outbound_flow.groupby("date")["cost_amount"].sum()
        if has_inventory_events and not outbound_flow.empty
        else (sales_events.groupby("date")["inventory_cost_delta"].sum() if not sales_events.empty else sales_df.groupby("date")["inventory_cost_delta"].sum())
    )
    daily["net_change"] = (daily["purchase_increase"] + inventory_cost_delta).fillna(0)

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


def render_problem_item_rows(payload: dict[str, Any]) -> str:
    error_codes: dict[Any, list[str]] = {}
    for issue in [*payload.get("errors", []), *payload.get("warnings", [])]:
        error_codes.setdefault(issue.get("product_id"), []).append(str(issue.get("code") or "오류"))
    problems = [
        row for row in payload.get("rows", [])
        if row.get("cost_status") in {"backfilled", "unconfirmed", "error"}
        or row.get("quantity_reconciliation_status") != "match"
        or row.get("product_id") in error_codes
    ]
    if not problems:
        return '<tr><td colspan="10">없음</td></tr>'
    return "\n".join(
        "<tr>"
        f"<td>{escape(str(row.get('product_id') if row.get('product_id') is not None else '-'))}</td>"
        f"<td>{escape(str(row.get('item_name') or '-'))}</td>"
        f"<td><code>{escape(str(row.get('cost_status') or '-'))}</code></td>"
        f"<td class=\"money\">{_base.number(row.get('unconfirmed_quantity') or 0)}</td>"
        f"<td class=\"money\">{_base.number(row.get('ending_negative_stock_quantity') or 0)}</td>"
        f"<td class=\"money\">{_base.number(row.get('inventory_book_quantity') or 0)}</td>"
        f"<td class=\"money\">{_base.number(row.get('inventory_sheet_quantity')) if row.get('inventory_sheet_quantity') is not None else '-'}</td>"
        f"<td class=\"money\">{_base.number(row.get('inventory_quantity_difference')) if row.get('inventory_quantity_difference') is not None else '-'}</td>"
        f"<td><code>{escape(str(row.get('quantity_reconciliation_status') or '-'))}</code></td>"
        f"<td>{escape(', '.join(error_codes.get(row.get('product_id'), [])) or '-')}</td>"
        "</tr>"
        for row in problems
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
    period_start = reconciliation_payload.get("metadata", {}).get("period_start")
    period_end = reconciliation_payload.get("metadata", {}).get("period_end")
    period_purchases = [row for row in purchase_records if not period_start or period_start <= row.get("date", "") <= period_end]
    period_sales = [row for row in sales_records if not period_start or period_start <= row.get("date", "") <= period_end]

    summary_rows_html = "\n".join(
        [
            _base.summary_row("분석 기간", reconciliation_payload.get("metadata", {}).get("period_start", _base.period_label(purchase_meta, sales_meta)) + " ~ " + reconciliation_payload.get("metadata", {}).get("period_end", "")),
            _base.summary_row("재고 기준일", reconciliation_payload.get("metadata", {}).get("inventory_date", "-")),
            _base.summary_row("입력 데이터 최종 거래일", reconciliation_payload.get("metadata", {}).get("input_data_last_transaction_date", "-")),
            _base.summary_row(
                "후속 원가보충 최종 매입일",
                reconciliation_payload.get("metadata", {}).get("backfill_last_purchase_date") or "없음",
            ),
            _base.summary_row(
                "거래 규모",
                f"매입 상세 {_base.number(len(period_purchases))}건 / 매출 상세 {_base.number(len(period_sales))}건",
            ),
            _base.summary_row(
                "분석 대상",
                f"매입처 {_base.number(_unique_value_count(period_purchases, 'company'))}곳 / "
                f"매출처 {_base.number(_unique_value_count(period_sales, 'company'))}곳",
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
    sales_amount = reconciliation.get("period_sales_supply_amount", reconciliation["sales_amount"])
    inventory_amount = reconciliation.get("inventory_amount_at_fifo", reconciliation.get("ending_fifo_inventory_amount", 0))
    reconciliation_remainder = reconciliation.get("gross_profit", 0)
    opening_amount = reconciliation.get("opening_stock_amount", 0)
    backfill_amount = reconciliation.get("post_period_backfill_amount", reconciliation.get("backfilled_amount", 0))
    prior_shortage_settlement_amount = reconciliation.get("prior_period_shortage_settlement_amount", 0)
    fifo_sales_cost = reconciliation.get("fifo_sales_cost_amount", 0)
    gross_profit_label = "매출총이익" if reconciliation.get("gross_profit_status") == "confirmed" else "잠정 매출총이익"
    vat_settlement = reconciliation.get("vat_settlement_amount", 0)
    vat_label = "부가세 납부 예상액" if vat_settlement > 0 else ("부가세 환급 예상액" if vat_settlement < 0 else "부가세 정산금")
    status_rows = "".join(
        f"<li><code>{escape(status)}</code>: {_base.number(count)}개 품목</li>"
        for status, count in reconciliation.get("cost_status_counts", {}).items()
    )
    reconciliation_rows = "".join(
        f"<li>{escape(status)}: {_base.number(count)}개</li>"
        for status, count in reconciliation.get("reconciliation_status_counts", {}).items()
    )
    problem_rows = render_problem_item_rows(reconciliation_payload)

    if reconciliation.get("gross_profit_status") != "confirmed":
        amount_balance_chart = '<div class="balance-chart-empty" role="note">원가 미확정 또는 계산 오류가 있어 금액 밸런스 차트는 잠정값으로만 검토해야 한다.</div>'
    else:
        amount_balance_chart = render_amount_balance_chart(
            opening_amount,
            purchase_amount,
            sales_amount,
            inventory_amount,
            backfill_amount,
            prior_shortage_settlement_amount,
            reconciliation_remainder,
        )
    chart_start = '<div class="chart-wrap balance-chart-wrap" aria-label="금액 대사 밸런스 블록 차트">'
    chart_end = "      </div>"
    chart_start_index = html.find(chart_start)
    chart_end_index = html.find(chart_end, chart_start_index)
    if chart_start_index < 0 or chart_end_index < 0:
        raise RuntimeError("amount balance chart replacement marker not found")
    html = html[:chart_start_index] + amount_balance_chart + html[chart_end_index + len(chart_end):]

    amount_rows = [
        ("기초재고금액", "분석 시작일 직전 FIFO 잔여 원가층", _base.money(opening_amount)),
        ("기간 순매입원가", "실제 매입·매입취소 원가 이벤트 합계", _base.money(purchase_amount)),
        ("후속 매입 소급배정원가", "종료일 이후 매입으로 확정된 현재 기간 미확정 출고 원가", _base.money(backfill_amount)),
        ("이전 기간 미확정 출고 보충원가", "기간 매입이 분석 시작일 이전 부족분에 배정된 금액", _base.money(prior_shortage_settlement_amount)),
        ("매출 공급가액", "기간 내 매출의 공급가액 합계 (부가세 제외)", _base.money(sales_amount)),
        ("FIFO 매출원가", "각 매출에 실제 배정된 FIFO 원가층 합계", _base.money(fifo_sales_cost)),
        ("FIFO 재고금액", "분석 종료일 정상 잔여 원가층 합계", _base.money(inventory_amount)),
        (
            gross_profit_label,
            "매출 공급가액 - FIFO 매출원가",
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
        '<p class="formula">매출총이익은 매출 공급가액 - FIFO 매출원가다. FIFO 재고금액은 분석 종료일 정상 잔여 원가층 합계다.</p>\n'
        '        <p class="section-note">후속 매입은 종료일 이전 미확정 출고를 확정하는 데만 사용하며 종료일 재고금액에는 포함하지 않는다.</p>',
        "amount reconciliation formula",
    )
    vat_section = f"""
        <section aria-labelledby="vat-settlement-title">
          <h3 id="vat-settlement-title">부가세 정산</h3>
          <p class="section-note">매출총이익은 부가세 제외 손익 지표다. 부가세 정산 후 잔여금액은 현금 관점의 참고값이며 손익이 아니다.</p>
          <table class="amount-table"><tbody>
            <tr><th>매출 공급가액</th><td class="money">{_base.money(reconciliation.get('period_sales_supply_amount', 0))}</td></tr>
            <tr><th>매출 부가세</th><td class="money">{_base.money(reconciliation.get('period_sales_vat_amount', 0))}</td></tr>
            <tr><th>매출 합계금액</th><td class="money">{_base.money(reconciliation.get('period_sales_total_amount', 0))}</td></tr>
            <tr><th>매입 공급가액</th><td class="money">{_base.money(reconciliation.get('period_purchase_supply_amount', 0))}</td></tr>
            <tr><th>매입 부가세</th><td class="money">{_base.money(reconciliation.get('period_purchase_vat_amount', 0))}</td></tr>
            <tr><th>매입 합계금액</th><td class="money">{_base.money(reconciliation.get('period_purchase_total_amount', 0))}</td></tr>
            <tr><th>{escape(vat_label)}</th><td class="money">{_base.money(abs(vat_settlement))}</td></tr>
            <tr><th>{'잠정 참고값' if reconciliation.get('gross_profit_status') != 'confirmed' else '부가세 정산 후 잔여금액'}</th><td class="money">{_base.money(reconciliation.get('post_vat_reference_amount', 0))}</td></tr>
          </tbody></table>
          <p class="section-note">거래금액 검증 오류 {escape(_base.number(reconciliation.get('amount_validation_error_count', 0)))}건 · 정산 상태 <code>{escape(str(reconciliation.get('vat_settlement_status', '-')))}</code></p>
        </section>
    """
    html = _replace_once(html, "    <!-- REPORT_EXTRA_PAGES -->", f"      {vat_section}\n    <!-- REPORT_EXTRA_PAGES -->", "VAT section insertion")

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
        <section aria-labelledby="problem-items-title">
          <h3 id="problem-items-title">문제 품목 상세</h3>
          <div class="table-scroll"><table><thead><tr><th>product_id</th><th>품명</th><th>원가 상태</th><th>미확정 수량</th><th>종료일 음수재고</th><th>기준일 장부수량</th><th>재고 시트 수량</th><th>수량 차이</th><th>수량 대사 상태</th><th>오류/경고</th></tr></thead><tbody>{problem_rows}</tbody></table></div>
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
