#!/usr/bin/env python3
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd

import generate_analysis_report_html_core as _base
from generate_analysis_report_html_core import *  # noqa: F401,F403
from src.charts import principal_margin_figure, weekly_inventory_flow_figure, weekly_purchase_sales_figure


def _display_money(value: Any) -> int:
    amount = _base.decimal_amount(value) or Decimal("0")
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _unique_value_count(records: list[dict[str, Any]], key: str) -> int:
    return len(
        {
            str(value).strip()
            for row in records
            if (value := row.get(key)) not in (None, "") and str(value).strip()
        }
    )


def _percent_from_amounts(numerator: Any, denominator: Any) -> float | None:
    numerator_value = _base.decimal_amount(numerator)
    denominator_value = _base.decimal_amount(denominator)
    if numerator_value is None or denominator_value in (None, Decimal("0")):
        return None
    return float(numerator_value / denominator_value * Decimal("100"))


def _period_records(
    records: list[dict[str, Any]],
    period_start: str | None,
    period_end: str | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in records:
        row_date = _base.safe_iso_date(row.get("date"))
        if row_date is None:
            continue
        value = row_date.isoformat()
        if period_start and value < period_start:
            continue
        if period_end and value > period_end:
            continue
        result.append(row)
    return result


def _summary_value(summary: dict[str, Any], *names: str, default: Any = 0) -> Any:
    for name in names:
        value = summary.get(name)
        if value is not None:
            return value
    return default


def _metric_card(label: str, value: str, note: str = "") -> str:
    note_html = f'<span class="metric-note">{escape(note)}</span>' if note else ""
    return f"""
      <div class="metric-card">
        <span class="metric-label">{escape(label)}</span>
        <strong>{escape(value)}</strong>
        {note_html}
      </div>
    """


def _money_metric(label: str, value: Any, note: str = "") -> str:
    return _metric_card(label, f"{_base.money(value)}원", note)


def _quantity_metric(label: str, value: Any, note: str = "") -> str:
    return _metric_card(label, _base.number(value), note)


def _format_percent(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}%"


def render_amount_balance_chart(
    opening_amount: Any,
    purchase_amount: Any,
    sales_amount: Any,
    inventory_amount: Any,
    backfill_amount: Any,
    prior_shortage_settlement_amount: Any,
    gross_profit: Any,
) -> str:
    """Retain the exact cost identity helper for callers that use it directly.

    The main HTML report intentionally does not render this diagnostic chart.
    """
    values = [
        _base.decimal_amount(value)
        for value in (
            opening_amount,
            purchase_amount,
            sales_amount,
            inventory_amount,
            backfill_amount,
            prior_shortage_settlement_amount,
            gross_profit,
        )
    ]
    if any(value is None for value in values):
        return '<div class="balance-chart-empty" role="note">금액 밸런스 exact 구성요소가 없어 차트를 표시할 수 없다.</div>'
    opening, purchase, sales, inventory, backfill, prior_shortage, profit = values
    left_total = sales + inventory + prior_shortage
    right_total = opening + purchase + backfill + profit
    if any(value < 0 for value in values):
        return '<div class="balance-chart-empty" role="note">원가 미확정·오류 또는 음수 이익이 있어 금액 밸런스 차트는 확정값으로 표시하지 않는다.</div>'
    if left_total != right_total:
        return (
            '<div class="balance-chart-empty" role="note">금액 밸런스 불일치: '
            f'왼쪽 {_base.money(_base.rounded_amount(left_total))}원 / '
            f'오른쪽 {_base.money(_base.rounded_amount(right_total))}원</div>'
        )
    total = left_total or Decimal("1")

    def segments(x: int, entries: list[tuple[str, Decimal]], color: str) -> str:
        y = 302.0
        result: list[str] = []
        for label, value in entries:
            height = float(value / total * Decimal("220"))
            y -= height
            result.append(
                f'<rect x="{x}" y="{y:.1f}" width="190" height="{height:.1f}" rx="3" '
                f'fill="{color}" opacity=".82"></rect>'
                f'<text x="{x + 95}" y="{y + height / 2:.1f}" text-anchor="middle">'
                f'{escape(label)} {_base.money(_base.rounded_amount(value))}원</text>'
            )
        return "".join(result)

    return f'''<div class="chart-wrap balance-chart-wrap" aria-label="금액 대사 밸런스 블록 차트">
      <svg id="amount-balance-chart" viewBox="0 0 980 410" role="img"><title>원가 금액 밸런스 차트</title><desc>매출과 종료일 재고는 기초재고, 기간 순매입원가, 후속 소급배정원가 및 매출총이익의 합계와 같다.</desc>
      <text x="490" y="34" text-anchor="middle">양쪽 exact 합계 {_base.money(_base.rounded_amount(left_total))}원</text>
      {segments(170, [('매출 공급가액', sales), ('종료일 재고', inventory), ('이전기간 부족 보충', prior_shortage)], '#2f6f7e')}
      {segments(620, [('기초재고', opening), ('기간 순매입', purchase), ('후속 소급배정', backfill), ('매출총이익', profit)], '#c47a23')}
      <text x="265" y="350" text-anchor="middle">매출 공급가액 + 재고 + 이전기간 보충</text><text x="715" y="350" text-anchor="middle">기초재고 + 매입 + 소급배정 + 이익</text></svg></div>'''


def weekly_inventory_flow_rows(
    purchase_records: list[dict[str, Any]],
    sales_records: list[dict[str, Any]],
    inventory_records: list[dict[str, Any]],
    reconciliation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build weekly inventory flow from published cost events.

    Decimal values are kept through aggregation and rounded only for display.
    """
    metadata = (reconciliation or {}).get("metadata", {})
    period_start, period_end = metadata.get("period_start"), metadata.get("period_end")

    def valid_period_date(row: dict[str, Any]) -> Any:
        value = _base.safe_iso_date(row.get("date"))
        if value is None or (
            period_start
            and period_end
            and not (period_start <= value.isoformat() <= period_end)
        ):
            return None
        return value

    dates = [
        value
        for row in [*purchase_records, *sales_records]
        if (value := valid_period_date(row))
    ]
    if not dates:
        return []

    daily: dict[Any, dict[str, Decimal]] = {}

    def day(value: Any) -> dict[str, Decimal]:
        return daily.setdefault(
            value,
            {
                "purchase": Decimal("0"),
                "sales": Decimal("0"),
                "outbound": Decimal("0"),
                "inventory_delta": Decimal("0"),
            },
        )

    validation_states = (reconciliation or {}).get("transaction_validations", [])
    valid_supply_keys = {
        _base.error_key(state)
        for state in validation_states
        if state.get("source") == "sales"
        and state.get("supply_amount_valid")
        and valid_period_date(state)
    }
    for row in sales_records:
        value = valid_period_date(row)
        amount = _base.decimal_amount(row.get("supply_amount"))
        if value and amount is not None and (
            not validation_states or _base.error_key(row, "sales") in valid_supply_keys
        ):
            day(value)["sales"] += amount

    inventory_events = (reconciliation or {}).get("inventory_cost_events", [])
    if inventory_events:
        for event in inventory_events:
            value = valid_period_date(event)
            if value is None:
                continue
            amount = _base.event_cost_amount(event)
            if event.get("event_type") in {"purchase", "purchase_cancellation"}:
                day(value)["purchase"] += amount
            else:
                day(value)["outbound"] -= amount
                day(value)["inventory_delta"] += amount
    else:
        for row in purchase_records:
            value = valid_period_date(row)
            if value:
                day(value)["purchase"] += (
                    (_base.decimal_amount(row.get("quantity")) or Decimal("0"))
                    * (_base.decimal_amount(row.get("unit_price")) or Decimal("0"))
                )
        for event in (reconciliation or {}).get("sales_cost_events", []):
            value = valid_period_date(event)
            if value:
                amount = _base.event_cost_amount(event)
                if event.get("event_type") == "sale_cancellation":
                    amount = -amount
                day(value)["outbound"] += amount
                day(value)["inventory_delta"] -= amount

    weekly: dict[Any, dict[str, Decimal]] = {}
    running = _base.decimal_amount(
        (reconciliation or {}).get("summary", {}).get("opening_stock_amount")
    ) or Decimal("0")
    current = min(dates)
    while current <= max(dates):
        values = day(current)
        net_change = values["purchase"] + values["inventory_delta"]
        running += net_change
        start = _base.week_start(current.isoformat())
        group = weekly.setdefault(
            start,
            {
                "purchase": Decimal("0"),
                "sales": Decimal("0"),
                "outbound": Decimal("0"),
                "net_change": Decimal("0"),
                "inventory": running,
            },
        )
        group["purchase"] += values["purchase"]
        group["sales"] += values["sales"]
        group["outbound"] += values["outbound"]
        group["net_change"] += net_change
        group["inventory"] = running
        current += _base.timedelta(days=1)

    return [
        {
            "week_start": start.isoformat(),
            "label": f"{start.month}/{start.day}",
            "purchase_increase": _display_money(row["purchase"]),
            "sales_amount": _display_money(row["sales"]),
            "outbound_cost_estimate": _display_money(row["outbound"]),
            "net_change": _display_money(row["net_change"]),
            "estimated_inventory_amount": _display_money(row["inventory"]),
        }
        for start, row in sorted(weekly.items())
    ]


def _top_purchase_companies(records: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "name": None,
            "row_count": 0,
            "quantity": Decimal("0"),
            "total_amount": Decimal("0"),
        }
    )
    for row in records:
        name = row.get("company") or "(매입처 없음)"
        group = grouped[name]
        group["name"] = name
        group["row_count"] += 1
        group["quantity"] += _base.decimal_amount(row.get("quantity")) or Decimal("0")
        group["total_amount"] += _base.decimal_amount(row.get("supply_amount")) or Decimal("0")
    rows = list(grouped.values())
    return sorted(rows, key=lambda row: row["total_amount"], reverse=True)[:limit]


def _item_detail_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in payload.get("rows", []):
        sales = _summary_value(row, "period_sales_supply_amount", "period_sales_amount", default=0)
        cost = _summary_value(row, "fifo_sales_cost_amount", default=0)
        sales_value = _base.decimal_amount(sales) or Decimal("0")
        cost_value = _base.decimal_amount(cost) or Decimal("0")
        rows.append(
            {
                "product_id": row.get("product_id"),
                "item_name": row.get("item_name") or "-",
                "opening_quantity": _summary_value(
                    row, "opening_signed_stock_quantity", "opening_stock_quantity", default=0
                ),
                "purchase_quantity": row.get("period_purchase_quantity", 0),
                "sales_quantity": row.get("period_sales_quantity", 0),
                "ending_quantity": _summary_value(
                    row, "ending_signed_stock_quantity", "inventory_book_quantity", default=0
                ),
                "sales_amount": sales_value,
                "cost_amount": cost_value,
                "margin_amount": sales_value - cost_value,
                "inventory_amount": _summary_value(
                    row, "ending_fifo_inventory_amount", default=0
                ),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            _base.decimal_amount(row["sales_amount"]) or Decimal("0"),
            str(row.get("product_id") or ""),
        ),
        reverse=True,
    )


def _empty_row(colspan: int, message: str = "표시할 데이터가 없습니다.") -> str:
    return f'<tr class="empty-row"><td colspan="{colspan}">{escape(message)}</td></tr>'


def _render_weekly_profit_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(7)
    body: list[str] = []
    total_sales = Decimal("0")
    total_cost = Decimal("0")
    total_quantity = Decimal("0")
    total_count = 0
    for row in rows:
        sales = _base.decimal_amount(row.get("sales_amount_exact"))
        if sales is None:
            sales = _base.decimal_amount(row.get("sales_amount")) or Decimal("0")
        cost = _base.decimal_amount(row.get("cost_amount_exact"))
        if cost is None:
            cost = _base.decimal_amount(row.get("cost_amount")) or Decimal("0")
        margin = sales - cost
        rate = _percent_from_amounts(margin, sales)
        quantity = _base.decimal_amount(row.get("quantity")) or Decimal("0")
        count = int(row.get("row_count") or 0)
        total_sales += sales
        total_cost += cost
        total_quantity += quantity
        total_count += count
        body.append(
            f"""
            <tr>
              <th>{escape(str(row.get('week_start') or row.get('label') or '-'))}</th>
              <td class="num">{_base.number(count)}</td>
              <td class="num">{_base.number(quantity)}</td>
              <td class="money">{_base.money(_display_money(sales))}</td>
              <td class="money">{_base.money(_display_money(cost))}</td>
              <td class="money">{_base.money(_display_money(margin))}</td>
              <td class="num">{escape(_format_percent(rate))}</td>
            </tr>
            """
        )
    total_margin = total_sales - total_cost
    body.append(
        f"""
        <tr class="total-row">
          <th>합계</th>
          <td class="num">{_base.number(total_count)}</td>
          <td class="num">{_base.number(total_quantity)}</td>
          <td class="money">{_base.money(_display_money(total_sales))}</td>
          <td class="money">{_base.money(_display_money(total_cost))}</td>
          <td class="money">{_base.money(_display_money(total_margin))}</td>
          <td class="num">{escape(_format_percent(_percent_from_amounts(total_margin, total_sales)))}</td>
        </tr>
        """
    )
    return "\n".join(body)


def _render_inventory_flow_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(5)
    return "\n".join(
        f"""
        <tr>
          <th>{escape(str(row.get('week_start') or row.get('label') or '-'))}</th>
          <td class="money">{_base.money(row.get('purchase_increase', 0))}</td>
          <td class="money">{_base.money(row.get('outbound_cost_estimate', 0))}</td>
          <td class="money">{_base.money(row.get('net_change', 0))}</td>
          <td class="money">{_base.money(row.get('estimated_inventory_amount', 0))}</td>
        </tr>
        """
        for row in rows
    )


def _render_principal_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(7, "원청 메타데이터가 없으면 전체 매출이 하나의 그룹으로 표시됩니다.")
    body: list[str] = []
    for index, row in enumerate(rows, start=1):
        sales = _base.decimal_amount(row.get("sales_amount_exact"))
        if sales is None:
            sales = _base.decimal_amount(row.get("sales_amount")) or Decimal("0")
        cost = _base.decimal_amount(row.get("cost_amount_exact"))
        if cost is None:
            cost = _base.decimal_amount(row.get("cost_amount")) or Decimal("0")
        margin = sales - cost
        body.append(
            f"""
            <tr>
              <td class="rank">{index}</td>
              <th>{escape(str(row.get('principal') or '(원청 없음)'))}</th>
              <td class="num">{_base.number(row.get('row_count', 0))}</td>
              <td class="num">{_base.number(row.get('quantity', 0))}</td>
              <td class="money">{_base.money(_display_money(sales))}</td>
              <td class="money">{_base.money(_display_money(cost))}</td>
              <td class="money">{_base.money(_display_money(margin))}</td>
            </tr>
            """
        )
    return "\n".join(body)


def _render_top_company_rows(rows: list[dict[str, Any]], label_key: str = "name") -> str:
    if not rows:
        return _empty_row(4)
    return "\n".join(
        f"""
        <tr>
          <td class="rank">{index}</td>
          <th>{escape(str(row.get(label_key) or '-'))}</th>
          <td class="num">{_base.number(row.get('row_count', 0))}</td>
          <td class="money">{_base.money(_display_money(row.get('total_amount', 0)))}</td>
        </tr>
        """
        for index, row in enumerate(rows, start=1)
    )


def _render_item_detail_rows(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return _empty_row(10)
    return "\n".join(
        f"""
        <tr>
          <td>{escape(str(row.get('product_id') or '-'))}</td>
          <th>{escape(str(row.get('item_name') or '-'))}</th>
          <td class="num">{_base.number(row.get('opening_quantity', 0))}</td>
          <td class="num">{_base.number(row.get('purchase_quantity', 0))}</td>
          <td class="num">{_base.number(row.get('sales_quantity', 0))}</td>
          <td class="num">{_base.number(row.get('ending_quantity', 0))}</td>
          <td class="money">{_base.money(_display_money(row.get('sales_amount', 0)))}</td>
          <td class="money">{_base.money(_display_money(row.get('cost_amount', 0)))}</td>
          <td class="money">{_base.money(_display_money(row.get('margin_amount', 0)))}</td>
          <td class="money">{_base.money(_display_money(row.get('inventory_amount', 0)))}</td>
        </tr>
        """
        for row in rows
    )


def _chart_frame(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> pd.DataFrame:
    return pd.DataFrame([{column: row.get(column) for column in columns} for row in rows])


def render_report_html(sources: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    spec = spec or _base.load_report_spec()
    report_spec = spec.get("report", {})
    report_title = report_spec.get("title", "매입/매출 재고 분석 보고서")
    report_scope = report_spec.get(
        "scope",
        "기간 요약, 손익 흐름, 재고 흐름, 원청·거래처·품목별 매출 구성",
    )

    purchase = sources["purchase"]
    sales = sources["sales"]
    inventory = sources["inventory"]
    reconciliation_payload = sources["reconciliation"]
    summary = reconciliation_payload["summary"]
    reconciliation_meta = reconciliation_payload.get("metadata", {})

    purchase_records = purchase.get("records", [])
    sales_records = sales.get("records", [])
    inventory_records = inventory.get("records", [])
    period_start = reconciliation_meta.get("period_start")
    period_end = reconciliation_meta.get("period_end")
    period = (
        f"{period_start} ~ {period_end}"
        if period_start and period_end
        else _base.period_label(purchase.get("metadata", {}), sales.get("metadata", {}))
    )
    inventory_date = reconciliation_meta.get("inventory_date") or "-"
    period_purchases = _period_records(purchase_records, period_start, period_end)
    period_sales = _period_records(sales_records, period_start, period_end)

    weekly_profit = _base.weekly_purchase_sales_amounts(
        purchase_records,
        sales_records,
        inventory_records,
        reconciliation_payload,
    )
    inventory_flow = weekly_inventory_flow_rows(
        purchase_records,
        sales_records,
        inventory_records,
        reconciliation_payload,
    )
    principal_rows_all = _base.principal_sales_cost_rows(
        sales_records,
        purchase_records,
        sources.get("sales_voucher_metadata"),
        reconciliation_payload,
    )
    principal_rows = principal_rows_all[:8]
    top_purchase_companies = _top_purchase_companies(period_purchases, limit=8)
    item_rows_all = _item_detail_rows(reconciliation_payload)
    item_rows = item_rows_all[:12]

    weekly_spec = spec["charts"]["weekly_purchase_sales"]
    inventory_spec = spec["charts"]["weekly_inventory_flow"]
    principal_spec = spec["charts"]["principal_margin"]
    weekly_chart = _base.figure_html(
        weekly_purchase_sales_figure(
            _chart_frame(weekly_profit, ("label", "sales_amount", "cost_amount")),
            weekly_spec,
        ),
        include_plotlyjs=False,
    )
    inventory_chart = _base.figure_html(
        weekly_inventory_flow_figure(pd.DataFrame(inventory_flow), inventory_spec),
        include_plotlyjs=False,
    )
    principal_chart = _base.figure_html(
        principal_margin_figure(
            _chart_frame(principal_rows, ("principal", "sales_amount", "cost_amount")),
            principal_spec,
        ),
        include_plotlyjs=False,
    )

    sales_amount = _summary_value(summary, "period_sales_supply_amount", "sales_amount")
    fifo_cost = _summary_value(summary, "fifo_sales_cost_amount")
    gross_profit = _summary_value(summary, "gross_profit", default=0)
    gross_profit_rate = _percent_from_amounts(gross_profit, sales_amount)
    opening_stock_amount = _summary_value(summary, "opening_stock_amount")
    purchase_cost = _summary_value(summary, "period_purchase_cost_amount", "purchase_amount")
    ending_inventory_amount = _summary_value(
        summary, "inventory_amount_at_fifo", "ending_fifo_inventory_amount"
    )
    ending_quantity = _summary_value(summary, "ending_signed_stock_quantity")
    sales_vat = _summary_value(summary, "period_sales_vat_amount")
    purchase_vat = _summary_value(summary, "period_purchase_vat_amount")
    vat_difference = _summary_value(summary, "vat_settlement_amount", default=0)

    metric_cards = "\n".join(
        [
            _money_metric("매출 공급가액", sales_amount, "부가세 제외"),
            _money_metric("매출원가", fifo_cost, "매출별 원가층 배정"),
            _money_metric("매출총이익", gross_profit, "매출 공급가액 - 매출원가"),
            _metric_card("매출총이익률", _format_percent(gross_profit_rate), "표시 금액 기준"),
            _money_metric("기초 재고금액", opening_stock_amount, "분석 시작일 전 원가층"),
            _money_metric("기말 재고금액", ending_inventory_amount, f"기준일 {inventory_date}"),
        ]
    )

    generated = date.today().isoformat()
    inventory_meta = inventory.get("metadata", {})
    inventory_product_count = inventory_meta.get("unique_product_ids")
    if inventory_product_count is None:
        inventory_product_count = _unique_value_count(inventory_records, "product_id")
    assumed_zero_stock_quantity_count = _summary_value(
        summary, "assumed_zero_stock_quantity_count", default=0
    )
    inventory_assumption_fact = ""
    if assumed_zero_stock_quantity_count:
        inventory_assumption_fact = (
            '<div class="fact"><dt>재고 수량 가정</dt><dd>빈 재고수량 '
            f'{_base.number(assumed_zero_stock_quantity_count)}건을 0으로 해석</dd></div>'
        )

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(report_title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --canvas: #eef1f4;
      --paper: #ffffff;
      --ink: #17212b;
      --muted: #697386;
      --line: #d9e0e7;
      --soft: #f5f7f9;
      --teal: #245d68;
      --teal-soft: #e5f0f2;
      --orange: #a65414;
      --orange-soft: #faeee4;
      --navy: #27364a;
      --page-width: 794px;
      --page-height: 1123px;
      --page-padding: 38px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; background: var(--canvas); }}
    body {{
      color: var(--ink);
      font-family: "Pretendard", "Noto Sans KR", "Apple SD Gothic Neo", "Segoe UI", sans-serif;
      line-height: 1.5;
      overflow-x: auto;
    }}
    main {{
      width: var(--page-width);
      margin: 26px auto 40px;
      display: flex;
      flex-direction: column;
      gap: 22px;
    }}
    .report-page {{
      width: var(--page-width);
      min-height: var(--page-height);
      padding: var(--page-padding);
      background: var(--paper);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 42px rgba(23, 33, 43, .08);
      page-break-after: always;
      break-after: page;
      position: relative;
    }}
    .report-page:last-child {{ page-break-after: auto; break-after: auto; }}
    .page-number {{
      position: absolute;
      right: 38px;
      bottom: 24px;
      color: #98a2b3;
      font-size: 12px;
      letter-spacing: .08em;
    }}
    .eyebrow {{
      margin: 0 0 12px;
      color: var(--teal);
      font-size: 12px;
      font-weight: 800;
      letter-spacing: .16em;
    }}
    h1 {{ margin: 0; max-width: 650px; font-size: 42px; line-height: 1.16; letter-spacing: -.03em; }}
    h2 {{ margin: 0; font-size: 28px; line-height: 1.22; letter-spacing: -.02em; }}
    h3 {{ margin: 0 0 12px; font-size: 18px; line-height: 1.3; }}
    .subtitle {{ margin: 14px 0 0; color: var(--muted); font-size: 16px; }}
    .hero {{ padding: 8px 0 26px; border-bottom: 2px solid var(--teal-soft); }}
    .hero-meta {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-top: 24px;
    }}
    .hero-meta div {{ padding: 14px 16px; border-radius: 12px; background: var(--soft); }}
    .hero-meta span {{ display: block; color: var(--muted); font-size: 12px; font-weight: 700; }}
    .hero-meta strong {{ display: block; margin-top: 5px; font-size: 15px; }}
    .metric-grid {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 12px;
      margin-top: 24px;
    }}
    .metric-card {{
      min-height: 118px;
      padding: 17px 17px 15px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: linear-gradient(180deg, #fff, #fbfcfd);
    }}
    .metric-label {{ display: block; color: var(--muted); font-size: 13px; font-weight: 700; }}
    .metric-card strong {{ display: block; margin-top: 8px; font-size: 24px; line-height: 1.2; font-variant-numeric: tabular-nums; }}
    .metric-note {{ display: block; margin-top: 8px; color: var(--muted); font-size: 12px; }}
    .overview-grid {{ display: grid; grid-template-columns: 1.1fr .9fr; gap: 14px; margin-top: 18px; }}
    .panel {{
      padding: 20px 22px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
    }}
    .panel.soft {{ background: var(--soft); }}
    .facts {{ margin: 0; padding: 0; }}
    .fact {{
      display: grid;
      grid-template-columns: 130px minmax(0, 1fr);
      gap: 12px;
      padding: 11px 0;
      border-bottom: 1px solid var(--line);
    }}
    .fact:last-child {{ border-bottom: 0; }}
    .fact dt {{ color: var(--muted); font-size: 13px; font-weight: 700; }}
    .fact dd {{ margin: 0; text-align: right; font-size: 14px; font-weight: 700; }}
    .method-list {{ margin: 0; padding: 0 0 0 18px; color: var(--muted); font-size: 13px; }}
    .method-list li + li {{ margin-top: 8px; }}
    .page-head {{ display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; padding-bottom: 16px; border-bottom: 2px solid var(--teal-soft); }}
    .page-head p {{ margin: 0; max-width: 340px; color: var(--muted); font-size: 13px; text-align: right; }}
    .section {{ margin-top: 18px; }}
    .report-page--inventory .section {{ margin-top: 8px; }}
    .report-page--sales .section {{ margin-top: 10px; }}
    .report-page--sales th, .report-page--sales td {{ padding-top: 8px; padding-bottom: 8px; }}
    .section-title-row {{ display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 10px; }}
    .section-title-row p {{ margin: 0; color: var(--muted); font-size: 12px; }}
    .chart-frame {{ border: 1px solid var(--line); border-radius: 14px; padding: 8px 10px 2px; background: #fff; overflow: hidden; }}
    .js-plotly-plot, .plot-container, .plotly, .plotly-graph-div {{ width: 100% !important; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 10px 0 8px; color: var(--muted); font-size: 12px; }}
    .legend span {{ display: inline-flex; align-items: center; gap: 6px; }}
    .legend i {{ width: 10px; height: 10px; border-radius: 3px; display: inline-block; }}
    .legend .sales {{ background: #b54708; opacity: .65; }}
    .legend .cost {{ background: #c47a23; }}
    .legend .purchase {{ background: #2f6f7e; }}
    .legend .inventory {{ background: #344054; border-radius: 50%; }}
    .table-wrap {{ width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; font-size: 12.5px; }}
    th, td {{ padding: 9px 9px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: middle; }}
    thead th {{ background: var(--teal-soft); color: #43515f; font-size: 11.5px; white-space: nowrap; }}
    tbody tr:last-child th, tbody tr:last-child td {{ border-bottom: 0; }}
    tbody tr:hover {{ background: #fbfcfd; }}
    .money, .num {{ text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }}
    .money {{ font-weight: 700; }}
    .rank {{ width: 42px; text-align: center; color: var(--muted); }}
    .total-row th, .total-row td {{ background: var(--soft); font-weight: 800; }}
    .empty-row td {{ padding: 24px; text-align: center; color: var(--muted); }}
    .mini-grid {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; }}
    .mini-card {{ padding: 14px; border: 1px solid var(--line); border-radius: 12px; background: var(--soft); }}
    .mini-card span {{ display: block; color: var(--muted); font-size: 11.5px; }}
    .mini-card strong {{ display: block; margin-top: 6px; font-size: 17px; font-variant-numeric: tabular-nums; }}
    .two-column {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
    .two-column .panel {{ padding: 16px; }}
    .caption {{ margin: 8px 0 0; color: var(--muted); font-size: 12px; }}
    .detail-table {{ font-size: 9.5px; table-layout: fixed; }}
    .detail-table th, .detail-table td {{ padding: 8px 5px; }}
    .detail-table th:nth-child(1), .detail-table td:nth-child(1) {{ width: 52px; }}
    .detail-table th:nth-child(2), .detail-table td:nth-child(2) {{ width: 116px; }}
    @page {{ size: A4 portrait; margin: 0; }}
    @media print {{
      html, body {{ background: #fff; }}
      main {{ margin: 0; gap: 0; }}
      .report-page {{ border: 0; border-radius: 0; box-shadow: none; }}
    }}
    @media (max-width: 820px) {{
      main {{ margin-left: 0; margin-right: 0; }}
    }}
  </style>
</head>
<body>
  <main>
    <article class="report-page report-page--overview">
      <header class="hero">
        <p class="eyebrow">LEDGER PERFORMANCE REPORT</p>
        <h1>{escape(report_title)}</h1>
        <p class="subtitle">{escape(report_scope)}</p>
        <div class="hero-meta">
          <div><span>분석 기간</span><strong>{escape(period)}</strong></div>
          <div><span>재고 기준일</span><strong>{escape(str(inventory_date))}</strong></div>
          <div><span>보고서 생성일</span><strong>{escape(generated)}</strong></div>
        </div>
      </header>

      <section class="metric-grid" aria-label="핵심 지표">
        {metric_cards}
      </section>

      <section class="overview-grid">
        <div class="panel">
          <h3>분석 범위</h3>
          <dl class="facts">
            <div class="fact"><dt>매입 상세</dt><dd>{_base.number(len(period_purchases))}건 · {_base.number(_unique_value_count(period_purchases, 'company'))}개 매입처</dd></div>
            <div class="fact"><dt>매출 상세</dt><dd>{_base.number(len(period_sales))}건 · {_base.number(_unique_value_count(period_sales, 'company'))}개 매출처</dd></div>
            <div class="fact"><dt>재고 품목</dt><dd>{_base.number(inventory_product_count)}개</dd></div>
            {inventory_assumption_fact}
            <div class="fact"><dt>기간 매입원가</dt><dd>{_base.money(purchase_cost)}원</dd></div>
          </dl>
        </div>
        <div class="panel soft">
          <h3>계산 기준</h3>
          <ul class="method-list">
            <li>매출은 공급가액, 매출원가와 기말 재고는 원가층 기준으로 표시합니다.</li>
            <li>부가세는 손익과 분리하여 금액 비교 표에서 보여줍니다.</li>
            <li>주간 표는 월요일 시작 주 단위로 집계합니다.</li>
            <li>기말 재고금액은 분석 종료일의 FIFO 잔여 원가층 합계입니다.</li>
          </ul>
        </div>
      </section>
      <span class="page-number">01 / 05</span>
    </article>

    <article class="report-page report-page--profit">
      <header class="page-head">
        <div><p class="eyebrow">PROFIT</p><h2 id="weekly-title">기간 손익 흐름</h2></div>
        <p>주별 매출 공급가액과 매출원가를 같은 축에서 비교합니다.</p>
      </header>

      <section class="section">
        <div class="section-title-row"><h3>{escape(weekly_spec['title'])}</h3><p>표시 단위: {escape(weekly_spec.get('unit_label', '원'))}</p></div>
        <div class="legend"><span><i class="sales"></i>{escape(weekly_spec['series']['sales_amount']['label'])}</span><span><i class="cost"></i>{escape(weekly_spec['series']['cost_amount']['label'])}</span></div>
        <div class="chart-frame">{weekly_chart}</div>
      </section>

      <section class="section">
        <div class="table-wrap">
          <table>
            <thead><tr><th>주 시작일</th><th class="num">건수</th><th class="num">수량</th><th class="money">매출 공급가액</th><th class="money">매출원가</th><th class="money">매출총이익</th><th class="num">이익률</th></tr></thead>
            <tbody>{_render_weekly_profit_rows(weekly_profit)}</tbody>
          </table>
        </div>
      </section>

      <section class="section">
        <div class="section-title-row"><h3>부가세 금액 비교</h3><p>공급가액 손익과 별도 표시</p></div>
        <div class="mini-grid">
          <div class="mini-card"><span>매출 부가세</span><strong>{_base.money(sales_vat)}원</strong></div>
          <div class="mini-card"><span>매입 부가세</span><strong>{_base.money(purchase_vat)}원</strong></div>
          <div class="mini-card"><span>부가세 차액</span><strong>{_base.money(vat_difference)}원</strong></div>
          <div class="mini-card"><span>매출 합계금액</span><strong>{_base.money(_summary_value(summary, 'period_sales_total_amount'))}원</strong></div>
        </div>
      </section>
      <span class="page-number">02 / 05</span>
    </article>

    <article class="report-page report-page--inventory">
      <header class="page-head">
        <div><p class="eyebrow">INVENTORY</p><h2 id="inventory-flow-title">{escape(inventory_spec['title'])}</h2></div>
        <p>매입 원가 유입, 출고 원가, 주말 재고금액의 흐름을 함께 봅니다.</p>
      </header>

      <section class="section">
        <div class="legend"><span><i class="purchase"></i>{escape(inventory_spec['series']['purchase_increase']['label'])}</span><span><i class="cost"></i>{escape(inventory_spec['series']['outbound_cost_estimate']['label'])}</span><span><i class="sales"></i>{escape(inventory_spec['series']['sales_amount']['label'])}</span><span><i class="inventory"></i>{escape(inventory_spec['series']['estimated_inventory_amount']['label'])}</span></div>
        <div class="chart-frame">{inventory_chart}</div>
      </section>

      <section class="section">
        <div class="table-wrap">
          <table>
            <thead><tr><th>주 시작일</th><th class="money">매입 증가</th><th class="money">출고 감소(원가)</th><th class="money">순증감</th><th class="money">주말 재고금액</th></tr></thead>
            <tbody>{_render_inventory_flow_rows(inventory_flow)}</tbody>
          </table>
        </div>
      </section>

      <section class="section">
        <div class="section-title-row"><h3>기간 재고 구성</h3><p>금액과 수량을 분리해 표시</p></div>
        <div class="mini-grid">
          <div class="mini-card"><span>기초 재고금액</span><strong>{_base.money(opening_stock_amount)}원</strong></div>
          <div class="mini-card"><span>기간 매입원가</span><strong>{_base.money(purchase_cost)}원</strong></div>
          <div class="mini-card"><span>매출원가</span><strong>{_base.money(fifo_cost)}원</strong></div>
          <div class="mini-card"><span>기말 재고금액</span><strong>{_base.money(ending_inventory_amount)}원</strong></div>
          <div class="mini-card"><span>기초 재고수량</span><strong>{_base.number(_summary_value(summary, 'opening_signed_stock_quantity', 'opening_stock_quantity'))}</strong></div>
          <div class="mini-card"><span>기간 매입수량</span><strong>{_base.number(_summary_value(summary, 'period_purchase_quantity'))}</strong></div>
          <div class="mini-card"><span>기간 매출수량</span><strong>{_base.number(_summary_value(summary, 'period_sales_quantity'))}</strong></div>
          <div class="mini-card"><span>기말 장부재고수량</span><strong>{_base.number(ending_quantity)}</strong></div>
        </div>
      </section>
      <span class="page-number">03 / 05</span>
    </article>

    <article class="report-page report-page--sales">
      <header class="page-head">
        <div><p class="eyebrow">SALES MIX</p><h2 id="principal-title">{escape(principal_spec['title'])}</h2></div>
        <p>매출 공급가액 상위 원청의 매출원가와 매출총이익을 보여줍니다.</p>
      </header>

      <section class="section">
        <div class="legend"><span><i class="sales"></i>{escape(principal_spec['series']['sales_amount']['label'])}</span><span><i class="cost"></i>{escape(principal_spec['series']['cost_amount']['label'])}</span></div>
        <div class="chart-frame">{principal_chart}</div>
        <div class="table-wrap" style="margin-top: 12px;">
          <table>
            <thead><tr><th class="rank">순위</th><th>원청</th><th class="num">건수</th><th class="num">수량</th><th class="money">매출 공급가액</th><th class="money">매출원가</th><th class="money">매출총이익</th></tr></thead>
            <tbody>{_render_principal_rows(principal_rows)}</tbody>
          </table>
        </div>
      </section>

      <span class="page-number">04 / 05</span>
    </article>

    <article class="report-page report-page--detail">
      <header class="page-head">
        <div><p class="eyebrow">PRODUCT DETAIL</p><h2 id="item-detail-title">주요 품목 손익·재고</h2></div>
        <p>기간 매출 공급가액 상위 {_base.number(len(item_rows))}개 품목을 표시합니다. 전체 품목은 {_base.number(len(item_rows_all))}개입니다.</p>
      </header>

      <section class="section">
        <div class="table-wrap">
          <table class="detail-table">
            <thead><tr><th>ID</th><th>품명</th><th class="num">기초</th><th class="num">매입</th><th class="num">매출</th><th class="num">기말</th><th class="money">매출액</th><th class="money">매출원가</th><th class="money">매출이익</th><th class="money">재고금액</th></tr></thead>
            <tbody>{_render_item_detail_rows(item_rows)}</tbody>
          </table>
        </div>
        <p class="caption">금액 단위: 원 · 수량은 장부의 부호를 유지합니다.</p>
      </section>

      <section class="section">
        <div class="section-title-row"><h3>매입처별 기간 매입 TOP 8</h3><p>공급가액 기준</p></div>
        <div class="table-wrap">
          <table><thead><tr><th class="rank">순위</th><th>매입처</th><th class="num">건수</th><th class="money">공급가액</th></tr></thead><tbody>{_render_top_company_rows(top_purchase_companies)}</tbody></table>
        </div>
      </section>
      <span class="page-number">05 / 05</span>
    </article>
  </main>
</body>
</html>
"""


def generate_report(
    input_dir: Path,
    output_path: Path,
    spec_path: Path = _base.DEFAULT_SPEC,
) -> None:
    sources = _base.load_sources(input_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_report_html(sources, _base.load_report_spec(spec_path)),
        encoding="utf-8",
    )


def main() -> int:
    args = _base.parse_args()
    generate_report(args.input_dir, args.output, args.spec)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
