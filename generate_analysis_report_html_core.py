#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from html import escape
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from src.charts import principal_margin_figure, weekly_purchase_sales_figure


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "private_intermediate"
DEFAULT_OUTPUT = BASE_DIR / "private_reports" / "report.html"
DEFAULT_SPEC = BASE_DIR / "report_spec.yaml"
LEGACY_PREFIX = "pe" + "tra_5_7_"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a summary and amount reconciliation HTML report.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_json_candidate(base_dir: Path, names: list[str]) -> dict[str, Any]:
    for name in names:
        path = base_dir / name
        if path.exists():
            return load_json(path)
    raise FileNotFoundError(f"none of these files exist in {base_dir}: {names}")


def load_report_spec(path: Path = DEFAULT_SPEC) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_sources(base_dir: Path) -> dict[str, Any]:
    sources = {
        "purchase": load_json_candidate(base_dir, ["purchase.json", f"{LEGACY_PREFIX}purchase.json"]),
        "sales": load_json_candidate(base_dir, ["sales.json", f"{LEGACY_PREFIX}sales.json"]),
        "inventory": load_json_candidate(base_dir, ["inventory.json", ("pe" + "tra_current_inventory.json")]),
        "reconciliation": load_json_candidate(
            base_dir, ["inventory_reconciliation.json", f"{LEGACY_PREFIX}inventory_reconciliation.json"]
        ),
    }
    voucher_metadata_path = base_dir / "sales_voucher_metadata.json"
    if not voucher_metadata_path.exists():
        voucher_metadata_path = base_dir / f"{LEGACY_PREFIX}sales_voucher_metadata.json"
    if voucher_metadata_path.exists():
        sources["sales_voucher_metadata"] = load_json(voucher_metadata_path)
    return sources


def money(value: Any) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}"


def number(value: Any) -> str:
    if value is None:
        return "-"
    return f"{int(value):,}"


def percent(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f}%"


def period_label(purchase_meta: dict[str, Any], sales_meta: dict[str, Any]) -> str:
    starts = [value for value in [purchase_meta.get("date_min"), sales_meta.get("date_min")] if value]
    ends = [value for value in [purchase_meta.get("date_max"), sales_meta.get("date_max")] if value]
    if not starts or not ends:
        return "-"
    return f"{min(starts)} ~ {max(ends)}"


def summary_row(title: str, value: str, note: str = "") -> str:
    note_html = f"<p>{escape(note)}</p>" if note else ""
    return f"""
      <div class="summary-row">
        <dt>{escape(title)}</dt>
        <dd>
          <strong>{escape(value)}</strong>
          {note_html}
        </dd>
      </div>
    """


def decimal_amount(value: Any) -> Decimal | None:
    """Return a valid amount without treating a VAT-inclusive total as supply."""
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def event_cost_amount(row: dict[str, Any]) -> Decimal:
    return decimal_amount(row.get("cost_amount_exact")) or decimal_amount(row.get("cost_amount")) or Decimal("0")


def rounded_amount(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def error_key(row: dict[str, Any], source: str | None = None) -> tuple[Any, Any, Any, Any, Any]:
    return (source or row.get("source"), row.get("date"), row.get("voucher"), row.get("excel_row"), row.get("product_id"))


def top_sales_by_company(records: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Rank revenue using supply amounts; total_amount is VAT-inclusive cash."""
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"row_count": 0, "quantity": 0, "total_amount": 0})
    for row in records:
        company = row.get("company") or "(거래처 없음)"
        grouped[company]["row_count"] += 1
        grouped[company]["quantity"] += row.get("quantity") or 0
        supply_amount = decimal_amount(row.get("supply_amount"))
        if supply_amount is not None:
            grouped[company]["total_amount"] += supply_amount

    rows = [
        {
            "name": company,
            "row_count": values["row_count"],
            "quantity": values["quantity"],
            "total_amount": values["total_amount"],
        }
        for company, values in grouped.items()
    ]
    return sorted(rows, key=lambda row: row["total_amount"], reverse=True)[:limit]


def top_sales_by_item(records: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    grouped: dict[Any, dict[str, Any]] = defaultdict(
        lambda: {"product_id": None, "item_name": None, "row_count": 0, "quantity": 0, "total_amount": 0}
    )
    for row in records:
        key = row.get("product_id") if row.get("product_id") is not None else row.get("item_name")
        grouped[key]["product_id"] = row.get("product_id")
        grouped[key]["item_name"] = row.get("item_name")
        grouped[key]["row_count"] += 1
        grouped[key]["quantity"] += row.get("quantity") or 0
        supply_amount = decimal_amount(row.get("supply_amount"))
        if supply_amount is not None:
            grouped[key]["total_amount"] += supply_amount

    rows = list(grouped.values())
    return sorted(rows, key=lambda row: row["total_amount"], reverse=True)[:limit]


def week_start(value: str) -> date:
    parsed = date.fromisoformat(value)
    return parsed - timedelta(days=parsed.weekday())


def weekly_purchase_sales_amounts(
    purchase_records: list[dict[str, Any]],
    sales_records: list[dict[str, Any]],
    inventory_records: list[dict[str, Any]],
    reconciliation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    sales_by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    cost_by_day: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    quantity_by_day: dict[date, int | float] = defaultdict(int)
    row_count_by_day: dict[date, int] = defaultdict(int)
    unconfirmed_by_day: dict[date, float] = defaultdict(float)
    date_values: list[date] = []
    metadata = (reconciliation or {}).get("metadata", {})
    period_start, period_end = metadata.get("period_start"), metadata.get("period_end")
    def in_period(row: dict[str, Any]) -> bool:
        return not period_start or not period_end or period_start <= row.get("date", "") <= period_end
    fifo_cost_by_sale = {
        row["transaction_id"]: event_cost_amount(row)
        for row in (reconciliation or {}).get("sales_cost_events", [])
        if in_period(row) and row.get("event_type") in {"sale", "sale_cancellation"}
    }
    for row in (reconciliation or {}).get("unconfirmed_quantity_events", (reconciliation or {}).get("sales_cost_events", [])):
        if in_period(row) and row.get("event_type") in {"sale", "sale_cancellation"}:
            unconfirmed_by_day[date.fromisoformat(row["date"])] += float(row.get("unconfirmed_quantity_delta") or 0)

    error_keys = {
        error_key(row)
        for row in (reconciliation or {}).get("errors", [])
        if row.get("date") and in_period(row) and (row.get("affects_revenue") or row.get("affects_fifo_cost"))
    }
    errors_by_day: dict[date, int] = defaultdict(int)

    for row in purchase_records:
        if in_period(row):
            date_values.append(date.fromisoformat(row["date"]))
    for row in sales_records:
        if not in_period(row):
            continue
        row_date = date.fromisoformat(row["date"])
        supply_amount = decimal_amount(row.get("supply_amount"))
        if supply_amount is not None and error_key(row, "sales") not in error_keys:
            sales_by_day[row_date] += supply_amount
        sale_id = f"{row.get('date')}|{row.get('voucher')}|{row.get('excel_row')}"
        cost_by_day[row_date] += fifo_cost_by_sale.get(sale_id, 0)
        quantity_by_day[row_date] += row.get("quantity") or 0
        row_count_by_day[row_date] += 1
        if error_key(row, "sales") in error_keys:
            errors_by_day[row_date] += 1
        date_values.append(row_date)

    if not date_values:
        return []

    grouped: dict[date, dict[str, Any]] = defaultdict(
        lambda: {"row_count": 0, "quantity": 0, "sales_amount": Decimal("0"), "cost_amount": Decimal("0"), "unconfirmed_quantity": 0, "error_count": 0}
    )
    current = min(date_values)
    end_date = max(date_values)
    while current <= end_date:
        grouped[week_start(current.isoformat())]["row_count"] += row_count_by_day.get(current, 0)
        grouped[week_start(current.isoformat())]["quantity"] += quantity_by_day.get(current, 0)
        grouped[week_start(current.isoformat())]["sales_amount"] += sales_by_day.get(current, 0)
        grouped[week_start(current.isoformat())]["cost_amount"] += cost_by_day.get(current, 0)
        grouped[week_start(current.isoformat())]["unconfirmed_quantity"] += unconfirmed_by_day.get(current, 0)
        grouped[week_start(current.isoformat())]["error_count"] += errors_by_day.get(current, 0)
        current += timedelta(days=1)

    result: list[dict[str, Any]] = []
    for start in sorted(grouped):
        sales_amount = grouped[start]["sales_amount"]
        sales_amount = rounded_amount(sales_amount)
        cost_amount = rounded_amount(grouped[start]["cost_amount"])
        margin_amount = sales_amount - cost_amount
        margin_status = "error" if grouped[start]["error_count"] else ("provisional" if grouped[start]["unconfirmed_quantity"] else "confirmed")
        result.append(
            {
                "week_start": start.isoformat(),
                "label": f"{start.month}/{start.day}",
                "row_count": grouped[start]["row_count"],
                "quantity": grouped[start]["quantity"],
                "sales_amount": sales_amount,
                "cost_amount": cost_amount,
                "margin_amount": margin_amount,
                "margin_rate": (margin_amount / sales_amount * 100) if sales_amount and margin_status == "confirmed" else None,
                "unconfirmed_quantity": grouped[start]["unconfirmed_quantity"],
                "error_count": grouped[start]["error_count"],
                "margin_status": margin_status,
            }
        )
    return result


def render_weekly_chart(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class=\"section-note\">표시할 주간 거래가 없다.</p>"

    chart_width = 960
    chart_height = 360
    left = 70
    top = 24
    bottom = 66
    plot_width = chart_width - left - 24
    plot_height = chart_height - top - bottom
    max_amount = max(max(row["purchase_amount"], row["sales_amount"]) for row in rows) or 1
    group_width = plot_width / len(rows)
    bar_width = min(28, group_width * 0.28)

    def bar_height(value: int | float) -> float:
        return (value / max_amount) * plot_height

    bars: list[str] = []
    labels: list[str] = []
    for index, row in enumerate(rows):
        group_x = left + index * group_width
        purchase_height = bar_height(row["purchase_amount"])
        sales_height = bar_height(row["sales_amount"])
        purchase_x = group_x + group_width * 0.5 - bar_width - 3
        sales_x = group_x + group_width * 0.5 + 3
        purchase_y = top + plot_height - purchase_height
        sales_y = top + plot_height - sales_height
        label_x = group_x + group_width * 0.5
        label = row["week_start"]

        bars.append(
            f"""
            <rect class="bar-purchase" x="{purchase_x:.1f}" y="{purchase_y:.1f}" width="{bar_width:.1f}" height="{purchase_height:.1f}" rx="3">
              <title>{escape(row['label'])} 매입 {money(row['purchase_amount'])}</title>
            </rect>
            <rect class="bar-sales" x="{sales_x:.1f}" y="{sales_y:.1f}" width="{bar_width:.1f}" height="{sales_height:.1f}" rx="3">
              <title>{escape(row['label'])} 매출 {money(row['sales_amount'])}</title>
            </rect>
            """
        )
        labels.append(
            f"""
            <text x="{label_x:.1f}" y="{chart_height - 34}" text-anchor="middle">{escape(label)}</text>
            """
        )

    guide_values = [max_amount, max_amount * 0.5, 0]
    guides = []
    for value in guide_values:
        y = top + plot_height - bar_height(value)
        guides.append(
            f"""
            <line x1="{left}" y1="{y:.1f}" x2="{chart_width - 24}" y2="{y:.1f}" />
            <text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end">{money(round(value / 1000000))}M</text>
            """
        )

    return f"""
      <div class="chart-wrap" aria-label="주간 매입 매출 금액 그래프">
        <svg viewBox="0 0 {chart_width} {chart_height}" role="img" aria-labelledby="weekly-chart-title weekly-chart-desc">
          <title id="weekly-chart-title">주간 매입/매출 금액</title>
          <desc id="weekly-chart-desc">주별 매입금액과 매출금액을 비교한 막대 그래프</desc>
          <g class="chart-guides">
            {''.join(guides)}
          </g>
          <g class="chart-bars">
            {''.join(bars)}
          </g>
          <g class="chart-labels">
            {''.join(labels)}
          </g>
        </svg>
      </div>
    """


def render_amount_balance_chart(
    purchase_amount: int | float,
    sales_amount: int | float,
    inventory_amount: int | float,
    remainder_amount: int | float,
) -> str:
    """Render the amount reconciliation as two equal-height stacked SVG blocks."""
    values = [purchase_amount, sales_amount, inventory_amount, remainder_amount]
    if any(value < 0 for value in values):
        return """
          <div class="balance-chart-empty" role="note">
            밸런스 블록 차트는 매입·매출·재고·이익이 모두 0 이상일 때 표시한다.
          </div>
        """

    total_amount = sales_amount + inventory_amount
    if total_amount <= 0:
        return """
          <div class="balance-chart-empty" role="note">
            밸런스 블록 차트를 표시할 금액이 없다.
          </div>
        """

    chart_width = 980
    chart_height = 410
    plot_top = 74
    plot_height = 238
    baseline_y = plot_top + plot_height
    left_x = 190
    right_x = 630
    block_width = 160

    def height(value: int | float) -> float:
        return (value / total_amount) * plot_height

    inventory_height = height(inventory_amount)
    sales_height = height(sales_amount)
    purchase_height = height(purchase_amount)
    remainder_height = height(remainder_amount)

    inventory_y = baseline_y - inventory_height
    sales_y = inventory_y - sales_height
    remainder_y = baseline_y - remainder_height
    purchase_y = remainder_y - purchase_height

    def segment_label(x: float, y: float, segment_height: float, label: str, value: int | float) -> str:
        center_y = y + segment_height / 2
        return f"""
          <text class="balance-segment-label" x="{x + block_width / 2:.1f}" y="{center_y - 8:.1f}" text-anchor="middle">{escape(label)}</text>
          <text class="balance-segment-value" x="{x + block_width / 2:.1f}" y="{center_y + 15:.1f}" text-anchor="middle">{money(value)}원</text>
        """

    return f"""
      <div class="chart-wrap balance-chart-wrap" aria-label="금액 대사 밸런스 블록 차트">
        <svg id="amount-balance-chart" viewBox="0 0 {chart_width} {chart_height}" role="img"
             aria-labelledby="amount-balance-title amount-balance-desc">
          <title id="amount-balance-title">밸런스 블록 차트: 매출과 재고의 합계, 이익과 매입의 합계</title>
          <desc id="amount-balance-desc">왼쪽은 재고금액 위에 매출금액을, 오른쪽은 이익금액 위에 매입금액을 쌓아 양쪽 합계가 같은지 보여준다.</desc>
          <defs>
            <pattern id="balance-remainder-hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
              <rect width="10" height="10" fill="#fff7e8"></rect>
              <line x1="0" y1="0" x2="0" y2="10" stroke="#c47a23" stroke-width="3"></line>
            </pattern>
          </defs>

          <line class="balance-equality-line" x1="{left_x}" y1="{plot_top}" x2="{right_x + block_width}" y2="{plot_top}"></line>
          <text class="balance-total-label" x="{chart_width / 2:.1f}" y="{plot_top - 18}" text-anchor="middle">양쪽 합계 {money(total_amount)}원</text>

          <rect class="balance-block balance-inventory" x="{left_x}" y="{inventory_y:.1f}" width="{block_width}" height="{inventory_height:.1f}" rx="3"></rect>
          <rect class="balance-block balance-sales" x="{left_x}" y="{sales_y:.1f}" width="{block_width}" height="{sales_height:.1f}" rx="3"></rect>
          {segment_label(left_x, inventory_y, inventory_height, '재고금액', inventory_amount)}
          {segment_label(left_x, sales_y, sales_height, '매출', sales_amount)}

          <rect class="balance-block balance-remainder" x="{right_x}" y="{remainder_y:.1f}" width="{block_width}" height="{remainder_height:.1f}" rx="3"></rect>
          <rect class="balance-block balance-purchase" x="{right_x}" y="{purchase_y:.1f}" width="{block_width}" height="{purchase_height:.1f}" rx="3"></rect>
          {segment_label(right_x, remainder_y, remainder_height, '이익', remainder_amount)}
          {segment_label(right_x, purchase_y, purchase_height, '매입', purchase_amount)}

          <line class="balance-baseline" x1="{left_x - 28}" y1="{baseline_y}" x2="{left_x + block_width + 28}" y2="{baseline_y}"></line>
          <line class="balance-baseline" x1="{right_x - 28}" y1="{baseline_y}" x2="{right_x + block_width + 28}" y2="{baseline_y}"></line>
          <text class="balance-side-title" x="{left_x + block_width / 2}" y="356" text-anchor="middle">매출 + 재고</text>
          <text class="balance-side-note" x="{left_x + block_width / 2}" y="378" text-anchor="middle">회수액과 남아 있는 자산</text>
          <text class="balance-side-title" x="{right_x + block_width / 2}" y="356" text-anchor="middle">이익 + 매입</text>
          <text class="balance-side-note" x="{right_x + block_width / 2}" y="378" text-anchor="middle">남는 금액과 투입액</text>
        </svg>
      </div>
    """


def render_weekly_table_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <th>{escape(row['label'])}</th>
            <td class="money">{number(row['row_count'])}</td>
            <td class="money">{number(row['quantity'])}</td>
            <td class="money">{money(row['sales_amount'])}</td>
            <td class="money">{money(row['cost_amount'])}</td>
            <td class="money">{money(row['margin_amount'])}</td>
            <td><code>{escape(str(row.get('margin_status', 'confirmed')))}</code></td>
            <td class="money">{percent(row['margin_rate']) if row.get('margin_status') == 'confirmed' else ('오류' if row.get('margin_status') == 'error' else '잠정')}</td>
          </tr>
        """
        for row in rows
    )


def include_plotlyjs_option(value: Any) -> bool | str:
    if value in (True, "inline", "embed", "true", None):
        return True
    if value in (False, "false", "none"):
        return False
    if value == "cdn":
        return "cdn"
    raise ValueError(f"unsupported plotly.include_plotlyjs value: {value!r}")


def figure_html(figure: Any, *, include_plotlyjs: bool | str) -> str:
    return figure.to_html(
        full_html=False,
        include_plotlyjs=include_plotlyjs,
        config={"displayModeBar": False, "responsive": True},
    )


def voucher_principal_map(metadata: dict[str, Any] | None) -> dict[str, str]:
    if not metadata:
        return {}
    return {
        row["voucher_key"]: row.get("principal") or "(원청 없음)"
        for row in metadata.get("records", [])
    }


def principal_sales_cost_rows(
    sales_records: list[dict[str, Any]],
    purchase_records: list[dict[str, Any]],
    voucher_metadata: dict[str, Any] | None,
    reconciliation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    principal_by_voucher = voucher_principal_map(voucher_metadata)
    fifo_by_sale = {
        row["transaction_id"]: row
        for row in (reconciliation or {}).get("sales_cost_events", [])
    }
    if not fifo_by_sale:
        fifo_by_sale = {
            row["sale_id"]: {"cost_amount": row.get("fifo_cost_amount", 0), "unconfirmed_quantity": row.get("unconfirmed_quantity", 0), "event_type": "sale"}
            for row in (reconciliation or {}).get("sales_allocations", [])
            if row.get("in_analysis_period")
        }
    unconfirmed_delta_by_transaction: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for event in (reconciliation or {}).get("unconfirmed_quantity_events", (reconciliation or {}).get("sales_cost_events", [])):
        if event.get("event_type") not in {"sale", "sale_cancellation", "shortage_backfill"}:
            continue
        transaction_id = event.get("sale_transaction_id") if event.get("event_type") == "shortage_backfill" else event.get("transaction_id")
        if transaction_id:
            unconfirmed_delta_by_transaction[transaction_id] += decimal_amount(event.get("unconfirmed_quantity_delta")) or Decimal("0")
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "principal": None,
            "row_count": 0,
            "quantity": 0,
            "sales_amount": Decimal("0"),
            "cost_amount": Decimal("0"),
            "missing_cost_row_count": 0,
            "unconfirmed_quantity": 0,
            "unconfirmed_row_count": 0,
            "error_count": 0,
        }
    )
    error_keys = {
        error_key(error)
        for error in (reconciliation or {}).get("errors", [])
        if error.get("source") == "sales" and (error.get("affects_revenue") or error.get("affects_fifo_cost"))
    }
    for row in sales_records:
        if reconciliation:
            period_start = (reconciliation.get("metadata") or {}).get("period_start")
            period_end = (reconciliation.get("metadata") or {}).get("period_end")
            if period_start and period_end and not (period_start <= row.get("date", "") <= period_end):
                continue
        voucher_key = f"{row['date']}-{row['voucher']}"
        principal = principal_by_voucher.get(voucher_key, "(원청 없음)")
        group = grouped[principal]
        group["principal"] = principal
        group["row_count"] += 1
        group["quantity"] += row.get("quantity") or 0
        supply_amount = decimal_amount(row.get("supply_amount"))
        if supply_amount is not None and error_key(row, "sales") not in error_keys:
            group["sales_amount"] += supply_amount
        sale = fifo_by_sale.get(sale_key(row))
        if sale is None:
            group["missing_cost_row_count"] += 1
            continue
        group["cost_amount"] += event_cost_amount(sale)
        unconfirmed_delta = unconfirmed_delta_by_transaction.get(sale_key(row), Decimal("0"))
        if unconfirmed_delta:
            group["unconfirmed_quantity"] += float(unconfirmed_delta)
            group["unconfirmed_row_count"] += 1

    for row in sales_records:
        if error_key(row, "sales") not in error_keys:
            continue
        voucher_key = f"{row['date']}-{row['voucher']}"
        grouped[principal_by_voucher.get(voucher_key, "(원청 없음)")]["error_count"] += 1

    rows: list[dict[str, Any]] = []
    for values in grouped.values():
        sales_amount = rounded_amount(values["sales_amount"])
        cost_amount = rounded_amount(values["cost_amount"])
        margin_amount = sales_amount - cost_amount
        margin_status = "error" if values["error_count"] or values["missing_cost_row_count"] else ("provisional" if values["unconfirmed_quantity"] else "confirmed")
        rows.append(
            {
                "principal": values["principal"],
                "row_count": values["row_count"],
                "quantity": values["quantity"],
                "sales_amount": sales_amount,
                "cost_amount": cost_amount,
                "margin_amount": margin_amount,
                "margin_rate": (margin_amount / sales_amount * 100) if sales_amount and margin_status == "confirmed" else None,
                "missing_cost_row_count": values["missing_cost_row_count"],
                "unconfirmed_quantity": values["unconfirmed_quantity"],
                "unconfirmed_row_count": values["unconfirmed_row_count"],
                "error_count": values["error_count"],
                "margin_status": margin_status,
            }
        )
    return sorted(rows, key=lambda row: row["sales_amount"], reverse=True)


def sale_key(row: dict[str, Any]) -> str:
    return f"{row.get('date')}|{row.get('voucher')}|{row.get('excel_row')}"


def render_principal_margin_chart(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "<p class=\"section-note\">표시할 원청별 매출 메타데이터가 없다.</p>"

    chart_width = 960
    row_height = 54
    top = 28
    left = 160
    right = 120
    chart_height = top + row_height * len(rows) + 24
    plot_width = chart_width - left - right
    max_amount = max(max(row["sales_amount"], row["cost_amount"]) for row in rows) or 1
    elements: list[str] = []
    for index, row in enumerate(rows):
        y = top + index * row_height
        sales_width = (row["sales_amount"] / max_amount) * plot_width
        cost_width = (row["cost_amount"] / max_amount) * plot_width
        label_y = y + 17
        sales_y = y + 8
        cost_y = y + 30
        elements.append(
            f"""
            <text class="principal-label" x="{left - 12}" y="{label_y}" text-anchor="end">{escape(row['principal'])}</text>
            <rect class="bar-principal-sales" x="{left}" y="{sales_y}" width="{sales_width:.1f}" height="14" rx="3">
              <title>{escape(row['principal'])} 매출 {money(row['sales_amount'])}</title>
            </rect>
            <rect class="bar-principal-cost" x="{left}" y="{cost_y}" width="{cost_width:.1f}" height="14" rx="3">
              <title>{escape(row['principal'])} 매출원가 {money(row['cost_amount'])}</title>
            </rect>
            <text class="principal-rate" x="{chart_width - 28}" y="{label_y}" text-anchor="end">{percent(row['margin_rate'])}</text>
            """
        )

    return f"""
      <div class="chart-wrap" aria-label="원청별 매출 원가 마진 그래프">
        <svg viewBox="0 0 {chart_width} {chart_height}" role="img" aria-labelledby="principal-chart-title principal-chart-desc">
          <title id="principal-chart-title">원청별 매출/원가/마진</title>
          <desc id="principal-chart-desc">원청별 매출금액, FIFO 매출원가, 마진율을 비교한 그래프</desc>
          <g>
            {''.join(elements)}
          </g>
        </svg>
      </div>
    """


def render_principal_margin_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <td class="rank">{index}</td>
            <th>{escape(row['principal'])}</th>
            <td class="money">{number(row['row_count'])}</td>
            <td class="money">{money(row['sales_amount'])}</td>
            <td class="money">{money(row['cost_amount'])}</td>
            <td class="money">{money(row['margin_amount'])}</td>
            <td><code>{escape(str(row.get('margin_status', 'confirmed')))}</code></td>
            <td class="money">{percent(row['margin_rate']) if row.get('margin_status') == 'confirmed' else ('오류' if row.get('margin_status') == 'error' else '잠정')}</td>
            <td class="money">{number(row.get('unconfirmed_quantity', 0))}</td>
          </tr>
        """
        for index, row in enumerate(rows, start=1)
    )


def unique_join(values: list[Any]) -> str:
    seen: list[str] = []
    for value in values:
        if value in (None, ""):
            continue
        text = str(value)
        if text not in seen:
            seen.append(text)
    return "\n".join(seen)


def purchase_voucher_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, Any], list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(records, key=lambda item: (item["date"], item["voucher"], item["excel_row"])):
        grouped[(row["date"], row["voucher"])].append(row)

    rows: list[dict[str, Any]] = []
    for (voucher_date, voucher), details in grouped.items():
        rows.append(
            {
                "voucher_id": f"{voucher_date}-{voucher}",
                "date": voucher_date,
                "voucher": voucher,
                "company": unique_join([row.get("company") for row in details]),
                "row_count": len(details),
                "quantity": sum(row.get("quantity") or 0 for row in details),
                "supply_amount": sum(row.get("supply_amount") or 0 for row in details),
                "vat": sum(row.get("vat") or 0 for row in details),
                "total_amount": sum(row.get("total_amount") or 0 for row in details),
            }
        )
    return rows


def render_purchase_voucher_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <td class="rank">{index}</td>
            <th>{escape(row['voucher_id'])}</th>
            <td>{escape(row['date'])}</td>
            <td class="money">{escape(str(row['voucher']))}</td>
            <td>{escape(row['company'])}</td>
            <td class="money">{number(row['row_count'])}</td>
            <td class="money">{number(row['quantity'])}</td>
            <td class="money">{money(row['supply_amount'])}</td>
            <td class="money">{money(row['vat'])}</td>
            <td class="money">{money(row['total_amount'])}</td>
          </tr>
        """
        for index, row in enumerate(rows, start=1)
    )


def render_company_top_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <td class="rank">{index}</td>
            <th>{escape(row['name'])}</th>
            <td class="money">{number(row['row_count'])}</td>
            <td class="money">{number(row['quantity'])}</td>
            <td class="money">{money(row['total_amount'])}</td>
          </tr>
        """
        for index, row in enumerate(rows, start=1)
    )


def render_item_top_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <td class="rank">{index}</td>
            <td class="money">{escape(str(row['product_id'] or '-'))}</td>
            <th>{escape(row['item_name'] or '-')}</th>
            <td class="money">{number(row['row_count'])}</td>
            <td class="money">{number(row['quantity'])}</td>
            <td class="money">{money(row['total_amount'])}</td>
          </tr>
        """
        for index, row in enumerate(rows, start=1)
    )


def render_report_html(sources: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    if spec is None:
        spec = load_report_spec()
    report_spec = spec.get("report", {})
    report_title = report_spec.get("title", "매입/매출 재고 분석 보고서")
    report_scope = report_spec.get("scope", "요약, 금액 대사, 주간 그래프, 원청별 마진, 매출 TOP")
    purchase_meta = sources["purchase"]["metadata"]
    sales_meta = sources["sales"]["metadata"]
    inventory_meta = sources["inventory"]["metadata"]
    reconciliation_payload = sources["reconciliation"]
    reconciliation = reconciliation_payload["summary"]

    period = period_label(purchase_meta, sales_meta)
    generated = date.today().isoformat()
    purchase_amount = reconciliation["purchase_amount"]
    sales_amount = reconciliation["sales_amount"]
    inventory_fifo = reconciliation.get("inventory_amount_at_fifo", reconciliation.get("ending_fifo_inventory_amount", 0))
    remainder_fifo = reconciliation.get("remainder_at_fifo", reconciliation.get("gross_profit", 0))
    amount_balance_chart = render_amount_balance_chart(
        purchase_amount,
        sales_amount,
        inventory_fifo,
        remainder_fifo,
    )
    weekly_amounts = weekly_purchase_sales_amounts(
        sources["purchase"]["records"],
        sources["sales"]["records"],
        sources["inventory"]["records"],
        reconciliation_payload,
    )
    include_plotlyjs = include_plotlyjs_option(spec.get("plotly", {}).get("include_plotlyjs", "inline"))
    weekly_chart_spec = spec["charts"]["weekly_purchase_sales"]
    principal_chart_spec = spec["charts"]["principal_margin"]
    weekly_chart = figure_html(
        weekly_purchase_sales_figure(pd.DataFrame(weekly_amounts), weekly_chart_spec),
        include_plotlyjs=include_plotlyjs,
    )
    weekly_table_rows = render_weekly_table_rows(weekly_amounts)
    principal_rows = principal_sales_cost_rows(
        sources["sales"]["records"],
        sources["purchase"]["records"],
        sources.get("sales_voucher_metadata"),
        reconciliation_payload,
    )
    principal_chart = figure_html(
        principal_margin_figure(pd.DataFrame(principal_rows), principal_chart_spec),
        include_plotlyjs=False,
    )
    principal_table_rows = render_principal_margin_rows(principal_rows)
    summary_rows_html = "\n".join(
        [
            summary_row("분석 기간", period),
            summary_row(
                "거래 건수",
                f"매입 상세 {number(purchase_meta['record_count'])}건 / 매출 상세 {number(sales_meta['record_count'])}건",
                f"재고 품목 {number(inventory_meta['unique_product_ids'])}개",
            ),
        ]
    )

    amount_rows = [
        ("매출 공급가액", "분석기간 매출 상세 공급가액 합계 (부가세 제외)", money(sales_amount)),
        ("재고금액", "분석 종료일 잔여 FIFO 원가층", money(inventory_fifo)),
        ("매입원가", "분석기간 매입수량 × 매입단가", money(reconciliation.get("period_purchase_cost_amount", purchase_amount))),
        ("매출총이익", "분석기간 매출 공급가액 - FIFO 매출원가", money(reconciliation.get("gross_profit", remainder_fifo))),
    ]
    amount_table = "\n".join(
        f"""
          <tr>
            <th>{escape(label)}</th>
            <td>{escape(description)}</td>
            <td class="money">{escape(amount)}</td>
          </tr>
        """
        for label, description, amount in amount_rows
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
      --bg: #eef2f6;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #667085;
      --line: #d5dde7;
      --accent: #2f6f7e;
      --accent-soft: #e3f1f3;
      --warn-soft: #fff4df;
      --page-width: 794px;
      --page-height: 1123px;
      --page-padding: 40px;
    }}
    * {{ box-sizing: border-box; }}
    html {{
      background: var(--bg);
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: "Pretendard", "Noto Sans KR", "Apple SD Gothic Neo", "Segoe UI", sans-serif;
      line-height: 1.55;
      overflow-x: auto;
    }}
    main {{
      width: var(--page-width);
      margin: 28px auto 36px;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: 22px;
    }}
    .report-page {{
      width: var(--page-width);
      min-height: var(--page-height);
      padding: var(--page-padding);
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 18px;
      box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
      display: flex;
      flex-direction: column;
      gap: 18px;
      page-break-after: always;
      break-after: page;
    }}
    .report-page:last-child {{
      page-break-after: auto;
      break-after: auto;
    }}
    .cover-header {{
      padding-bottom: 18px;
      border-bottom: 2px solid var(--accent-soft);
    }}
    header {{
      margin-bottom: 0;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 38px;
      line-height: 1.18;
      letter-spacing: 0;
    }}
    .meta {{
      color: var(--muted);
      margin: 0;
      font-size: 15px;
    }}
    section {{
      background: #fbfcfd;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 24px 26px;
      margin-bottom: 0;
    }}
    h2 {{
      margin: 0 0 18px;
      font-size: 26px;
      line-height: 1.24;
      letter-spacing: 0;
    }}
    .page-panel {{
      display: flex;
      flex-direction: column;
      gap: 14px;
    }}
    .report-page--analysis .page-panel {{
      flex: 1;
    }}
    .summary-list {{
      margin: 0;
      padding: 0;
      border-top: 1px solid var(--line);
    }}
    .summary-row {{
      display: grid;
      grid-template-columns: 148px minmax(0, 1fr);
      gap: 18px;
      align-items: start;
      padding: 16px 0;
      border-bottom: 1px solid var(--line);
    }}
    .summary-row dt {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
      font-weight: 700;
    }}
    .summary-row dd {{
      margin: 0;
      min-width: 0;
    }}
    .summary-row strong {{
      display: block;
      font-size: 24px;
      line-height: 1.28;
      letter-spacing: 0;
    }}
    .summary-row p {{
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 14px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 15px;
      background: #ffffff;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 11px 10px;
      text-align: left;
      vertical-align: top;
    }}
    thead th {{
      color: var(--muted);
      font-weight: 700;
      background: var(--accent-soft);
    }}
    tbody tr:last-child th,
    tbody tr:last-child td {{
      border-bottom: none;
    }}
    .amount-table tbody th {{
      width: 140px;
      font-weight: 700;
    }}
    .rank {{
      width: 52px;
      color: var(--muted);
      font-weight: 700;
      text-align: center;
    }}
    .money {{
      text-align: right;
      font-variant-numeric: tabular-nums;
      font-weight: 700;
      white-space: nowrap;
    }}
    .formula {{
      margin: 2px 0 0;
      padding: 14px 16px;
      border-radius: 8px;
      background: var(--warn-soft);
      color: #5f4717;
      font-size: 15px;
    }}
    .section-note {{
      margin: -4px 0 0;
      color: var(--muted);
      font-size: 15px;
    }}
    .table-scroll {{
      max-width: 100%;
      overflow-x: auto;
    }}
    .legend {{
      display: flex;
      flex-wrap: wrap;
      gap: 14px;
      margin: 2px 0 2px;
      color: var(--muted);
      font-size: 15px;
    }}
    .legend span {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }}
    .legend i {{
      display: inline-block;
      width: 12px;
      height: 12px;
      border-radius: 3px;
    }}
    .purchase-chip {{ background: #2f6f7e; }}
    .outbound-chip {{ background: #c47a23; }}
    .revenue-chip {{ background: #b54708; }}
    .inventory-chip {{ background: #344054; }}
    .chart-wrap {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 12px;
      padding: 10px 12px;
      background: #ffffff;
    }}
    .balance-chart-wrap {{
      margin: 0;
      padding: 10px 12px 4px;
    }}
    .balance-chart-wrap svg {{
      display: block;
      width: 100%;
      height: auto;
    }}
    .js-plotly-plot,
    .plot-container,
    .plotly,
    .plotly-graph-div {{
      width: 100% !important;
    }}
    .balance-block {{
      stroke: #344054;
      stroke-width: 1.5;
    }}
    .balance-inventory {{ fill: #dceff2; }}
    .balance-sales {{ fill: #f7d8b5; }}
    .balance-purchase {{ fill: #8fc2cc; }}
    .balance-remainder {{ fill: url(#balance-remainder-hatch); }}
    .balance-note, .balance-side-note {{
      fill: var(--muted);
      font-size: 12px;
    }}
    .balance-equality-line {{
      stroke: var(--accent);
      stroke-width: 1.5;
      stroke-dasharray: 7 6;
    }}
    .balance-total-label {{
      fill: var(--accent);
      font-size: 13px;
      font-weight: 700;
    }}
    .balance-baseline {{
      stroke: #667085;
      stroke-width: 1.2;
    }}
    .balance-segment-label {{
      fill: #1f2933;
      font-size: 13px;
      font-weight: 700;
    }}
    .balance-segment-value {{
      fill: #344054;
      font-size: 11.5px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}
    .balance-side-title {{
      fill: var(--text);
      font-size: 14px;
      font-weight: 700;
    }}
    .balance-chart-empty {{
      margin: 0 0 18px;
      padding: 18px;
      border: 1px dashed var(--line);
      border-radius: 8px;
      color: var(--muted);
      background: #fbfcfd;
      font-size: 14px;
    }}
    details {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 14px;
    }}
    summary {{
      cursor: pointer;
      color: var(--accent);
      font-weight: 700;
    }}
    @page {{
      size: A4 portrait;
      margin: 0;
    }}
    @media print {{
      html, body {{
        background: #ffffff;
      }}
      main {{
        margin: 0;
        padding: 0;
        gap: 0;
      }}
      .report-page {{
        margin: 0;
        border: none;
        border-radius: 0;
        box-shadow: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <article class="report-page report-page--cover">
      <header class="cover-header">
        <h1>{escape(report_title)}</h1>
        <p class="meta">생성일 {escape(generated)} · 범위: {escape(report_scope)}</p>
      </header>

      <section class="page-panel" aria-labelledby="summary-title">
        <h2 id="summary-title">요약</h2>
        <dl class="summary-list">
          {summary_rows_html}
        </dl>
      </section>

      <section class="page-panel" aria-labelledby="amount-title">
        <h2 id="amount-title">금액 대사</h2>
        {amount_balance_chart}
        <table class="amount-table">
          <thead>
            <tr>
              <th>항목</th>
              <th>계산 기준</th>
              <th class="money">금액</th>
            </tr>
          </thead>
          <tbody>
            {amount_table}
          </tbody>
        </table>
        <p class="formula">FIFO 매출원가와 FIFO 잔여 원가층을 사용했다. 재고 시트의 평균원가·최종매입가는 계산에 사용하지 않는다.</p>
      </section>
    </article>

    <!-- REPORT_EXTRA_PAGES -->

    <article class="report-page report-page--analysis">
      <section class="page-panel" aria-labelledby="weekly-title">
        <h2 id="weekly-title">{escape(weekly_chart_spec['title'])}</h2>
        <p class="section-note">월요일 시작 주 단위로 매출 공급가액과 매출별 FIFO 배정 매출원가를 합산했다.</p>
        <p class="section-note">축: {escape(weekly_chart_spec.get('x_axis_title', '주 시작일'))} / {escape(weekly_chart_spec.get('y_axis_title', '금액'))}</p>
        <p class="section-note">표시 단위: {escape(weekly_chart_spec.get('unit_label', '원'))}</p>
        <div class="legend">
          <span><i class="revenue-chip"></i>{escape(weekly_chart_spec['series']['sales_amount']['label'])}</span>
          <span><i class="outbound-chip"></i>{escape(weekly_chart_spec['series']['cost_amount']['label'])}</span>
        </div>
        {weekly_chart}
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th>주 시작일</th>
                <th class="money">건수</th>
                <th class="money">수량</th>
                <th class="money">매출 공급가액</th>
                <th class="money">매출원가</th>
                <th class="money">마진금액</th>
                <th>마진 상태</th>
                <th class="money">마진율</th>
              </tr>
            </thead>
            <tbody>
              {weekly_table_rows}
            </tbody>
          </table>
        </div>
      </section>
    </article>

    <article class="report-page report-page--analysis">
      <section class="page-panel" aria-labelledby="principal-title">
        <h2 id="principal-title">{escape(principal_chart_spec['title'])}</h2>
        <p class="section-note">매출 전표의 원청 메타데이터를 기준으로 묶었다. 매출 공급가액과 매출원가는 각 거래의 공급가액 및 실제 FIFO 배정원가 합계다.</p>
        <p class="section-note">축: {escape(principal_chart_spec.get('y_axis_title', '원청'))} / {escape(principal_chart_spec.get('x_axis_title', '금액'))}</p>
        <p class="section-note">표시 단위: {escape(principal_chart_spec.get('unit_label', '원'))}</p>
        <div class="legend">
          <span><i class="revenue-chip"></i>매출 공급가액</span>
          <span><i class="outbound-chip"></i>매출원가</span>
        </div>
        {principal_chart}
        <div class="table-scroll">
          <table>
            <thead>
              <tr>
                <th class="rank">순위</th>
                <th>원청</th>
                <th class="money">건수</th>
                <th class="money">매출 공급가액</th>
                <th class="money">매출원가</th>
                <th class="money">마진금액</th>
                <th>마진 상태</th>
                <th class="money">마진율</th>
                <th class="money">미확정 수량</th>
              </tr>
            </thead>
            <tbody>
              {principal_table_rows}
            </tbody>
          </table>
        </div>
      </section>
    </article>
  </main>
</body>
</html>
"""


def generate_report(input_dir: Path, output_path: Path, spec_path: Path = DEFAULT_SPEC) -> None:
    sources = load_sources(input_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_report_html(sources, load_report_spec(spec_path)), encoding="utf-8")


def main() -> int:
    args = parse_args()
    generate_report(args.input_dir, args.output, args.spec)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
