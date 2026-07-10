#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, timedelta
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


def card(title: str, value: str, note: str = "") -> str:
    note_html = f"<p>{escape(note)}</p>" if note else ""
    return f"""
      <article class="card">
        <span>{escape(title)}</span>
        <strong>{escape(value)}</strong>
        {note_html}
      </article>
    """


def top_sales_by_company(records: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(lambda: {"row_count": 0, "quantity": 0, "total_amount": 0})
    for row in records:
        company = row.get("company") or "(거래처 없음)"
        grouped[company]["row_count"] += 1
        grouped[company]["quantity"] += row.get("quantity") or 0
        grouped[company]["total_amount"] += row.get("total_amount") or 0

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
        grouped[key]["total_amount"] += row.get("total_amount") or 0

    rows = list(grouped.values())
    return sorted(rows, key=lambda row: row["total_amount"], reverse=True)[:limit]


def week_start(value: str) -> date:
    parsed = date.fromisoformat(value)
    return parsed - timedelta(days=parsed.weekday())


def weekly_purchase_sales_amounts(
    purchase_records: list[dict[str, Any]], sales_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    grouped: dict[date, dict[str, Any]] = defaultdict(lambda: {"purchase_amount": 0, "sales_amount": 0})
    for row in purchase_records:
        grouped[week_start(row["date"])]["purchase_amount"] += row.get("total_amount") or 0
    for row in sales_records:
        grouped[week_start(row["date"])]["sales_amount"] += row.get("total_amount") or 0

    result: list[dict[str, Any]] = []
    for start in sorted(grouped):
        end = start + timedelta(days=6)
        result.append(
            {
                "week_start": start.isoformat(),
                "week_end": end.isoformat(),
                "label": f"{start.isoformat()} ~ {end.strftime('%m-%d')}",
                "purchase_amount": grouped[start]["purchase_amount"],
                "sales_amount": grouped[start]["sales_amount"],
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
        label = f"{row['week_start'][5:]}~{row['week_end'][5:]}"

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
            밸런스 블록 차트는 매입·매출·재고·나머지가 모두 0 이상일 때 표시한다.
          </div>
        """

    total_amount = sales_amount + inventory_amount
    if total_amount <= 0:
        return """
          <div class="balance-chart-empty" role="note">
            밸런스 블록 차트를 표시할 금액이 없다.
          </div>
        """

    chart_width = 960
    chart_height = 520
    plot_top = 118
    plot_height = 286
    baseline_y = plot_top + plot_height
    left_x = 188
    right_x = 602
    block_width = 170

    def height(value: int | float) -> float:
        return (value / total_amount) * plot_height

    inventory_height = height(inventory_amount)
    sales_height = height(sales_amount)
    purchase_height = height(purchase_amount)
    remainder_height = height(remainder_amount)

    inventory_y = baseline_y - inventory_height
    sales_y = inventory_y - sales_height
    purchase_y = baseline_y - purchase_height
    remainder_y = purchase_y - remainder_height

    def segment_label(x: float, y: float, segment_height: float, label: str, value: int | float) -> str:
        center_y = y + segment_height / 2
        return f"""
          <text class="balance-segment-label" x="{x + block_width / 2:.1f}" y="{center_y - 8:.1f}" text-anchor="middle">{escape(label)}</text>
          <text class="balance-segment-value" x="{x + block_width / 2:.1f}" y="{center_y + 15:.1f}" text-anchor="middle">{money(value)}원</text>
        """

    equation = (
        f"매출 {money(sales_amount)} + 재고 {money(inventory_amount)} = "
        f"매입 {money(purchase_amount)} + 나머지 {money(remainder_amount)} = {money(total_amount)}원"
    )

    return f"""
      <div class="chart-wrap balance-chart-wrap" aria-label="금액 대사 밸런스 블록 차트">
        <svg id="amount-balance-chart" viewBox="0 0 {chart_width} {chart_height}" role="img"
             aria-labelledby="amount-balance-title amount-balance-desc">
          <title id="amount-balance-title">밸런스 블록 차트: 매출과 재고의 합계, 매입과 나머지의 합계</title>
          <desc id="amount-balance-desc">왼쪽은 재고금액 위에 매출금액을, 오른쪽은 매입금액 위에 나머지를 쌓아 양쪽 합계가 같은지 보여준다.</desc>
          <defs>
            <pattern id="balance-remainder-hatch" width="10" height="10" patternUnits="userSpaceOnUse" patternTransform="rotate(35)">
              <rect width="10" height="10" fill="#fff7e8"></rect>
              <line x1="0" y1="0" x2="0" y2="10" stroke="#c47a23" stroke-width="3"></line>
            </pattern>
          </defs>

          <text class="balance-equation" x="480" y="36" text-anchor="middle">{escape(equation)}</text>
          <text class="balance-note" x="480" y="64" text-anchor="middle">기초재고 0 가정 · 나머지 = 매출 + 현재재고금액 - 매입</text>

          <line class="balance-equality-line" x1="{left_x}" y1="{plot_top}" x2="{right_x + block_width}" y2="{plot_top}"></line>
          <text class="balance-total-label" x="480" y="{plot_top - 14}" text-anchor="middle">양쪽 합계 {money(total_amount)}원</text>

          <rect class="balance-block balance-inventory" x="{left_x}" y="{inventory_y:.1f}" width="{block_width}" height="{inventory_height:.1f}" rx="3"></rect>
          <rect class="balance-block balance-sales" x="{left_x}" y="{sales_y:.1f}" width="{block_width}" height="{sales_height:.1f}" rx="3"></rect>
          {segment_label(left_x, inventory_y, inventory_height, '재고금액', inventory_amount)}
          {segment_label(left_x, sales_y, sales_height, '매출', sales_amount)}

          <rect class="balance-block balance-purchase" x="{right_x}" y="{purchase_y:.1f}" width="{block_width}" height="{purchase_height:.1f}" rx="3"></rect>
          <rect class="balance-block balance-remainder" x="{right_x}" y="{remainder_y:.1f}" width="{block_width}" height="{remainder_height:.1f}" rx="3"></rect>
          {segment_label(right_x, purchase_y, purchase_height, '매입', purchase_amount)}
          {segment_label(right_x, remainder_y, remainder_height, '나머지', remainder_amount)}

          <line class="balance-baseline" x1="{left_x - 28}" y1="{baseline_y}" x2="{left_x + block_width + 28}" y2="{baseline_y}"></line>
          <line class="balance-baseline" x1="{right_x - 28}" y1="{baseline_y}" x2="{right_x + block_width + 28}" y2="{baseline_y}"></line>
          <text class="balance-side-title" x="{left_x + block_width / 2}" y="452" text-anchor="middle">매출 + 재고</text>
          <text class="balance-side-note" x="{left_x + block_width / 2}" y="478" text-anchor="middle">회수액과 남아 있는 자산</text>
          <text class="balance-side-title" x="{right_x + block_width / 2}" y="452" text-anchor="middle">매입 + 나머지</text>
          <text class="balance-side-note" x="{right_x + block_width / 2}" y="478" text-anchor="middle">투입액과 대사 차액</text>
        </svg>
      </div>
    """


def render_weekly_table_rows(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"""
          <tr>
            <th>{escape(row['label'])}</th>
            <td class="money">{money(row['purchase_amount'])}</td>
            <td class="money">{money(row['sales_amount'])}</td>
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


def purchase_unit_costs(records: list[dict[str, Any]]) -> dict[Any, float]:
    grouped: dict[Any, dict[str, float]] = defaultdict(lambda: {"quantity": 0, "amount": 0})
    for row in records:
        product_id = row.get("product_id")
        if product_id is None:
            continue
        quantity = row.get("quantity") or 0
        if quantity == 0:
            continue
        grouped[product_id]["quantity"] += quantity
        grouped[product_id]["amount"] += row.get("total_amount") or 0
    return {
        product_id: values["amount"] / values["quantity"]
        for product_id, values in grouped.items()
        if values["quantity"]
    }


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
) -> list[dict[str, Any]]:
    principal_by_voucher = voucher_principal_map(voucher_metadata)
    unit_costs = purchase_unit_costs(purchase_records)
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "principal": None,
            "row_count": 0,
            "quantity": 0,
            "sales_amount": 0,
            "cost_amount": 0.0,
            "missing_cost_row_count": 0,
        }
    )
    for row in sales_records:
        voucher_key = f"{row['date']}-{row['voucher']}"
        principal = principal_by_voucher.get(voucher_key, "(원청 없음)")
        group = grouped[principal]
        group["principal"] = principal
        group["row_count"] += 1
        group["quantity"] += row.get("quantity") or 0
        group["sales_amount"] += row.get("total_amount") or 0
        unit_cost = unit_costs.get(row.get("product_id"))
        if unit_cost is None:
            group["missing_cost_row_count"] += 1
            continue
        group["cost_amount"] += (row.get("quantity") or 0) * unit_cost

    rows: list[dict[str, Any]] = []
    for values in grouped.values():
        sales_amount = values["sales_amount"]
        cost_amount = int(round(values["cost_amount"]))
        margin_amount = sales_amount - cost_amount
        rows.append(
            {
                "principal": values["principal"],
                "row_count": values["row_count"],
                "quantity": values["quantity"],
                "sales_amount": sales_amount,
                "cost_amount": cost_amount,
                "margin_amount": margin_amount,
                "margin_rate": (margin_amount / sales_amount * 100) if sales_amount else None,
                "missing_cost_row_count": values["missing_cost_row_count"],
            }
        )
    return sorted(rows, key=lambda row: row["sales_amount"], reverse=True)


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
          <desc id="principal-chart-desc">원청별 매출금액, 추정 매출원가, 마진율을 비교한 그래프</desc>
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
            <td class="money">{number(row['quantity'])}</td>
            <td class="money">{money(row['sales_amount'])}</td>
            <td class="money">{money(row['cost_amount'])}</td>
            <td class="money">{money(row['margin_amount'])}</td>
            <td class="money">{percent(row['margin_rate'])}</td>
            <td class="money">{number(row['missing_cost_row_count'])}</td>
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
    reconciliation = sources["reconciliation"]["summary"]

    period = period_label(purchase_meta, sales_meta)
    generated = date.today().isoformat()
    purchase_amount = reconciliation["purchase_amount"]
    sales_amount = reconciliation["sales_amount"]
    inventory_average = reconciliation["inventory_amount_at_average_cost"]
    remainder_average = reconciliation["remainder_at_average_cost"]
    amount_balance_chart = render_amount_balance_chart(
        purchase_amount,
        sales_amount,
        inventory_average,
        remainder_average,
    )
    weekly_amounts = weekly_purchase_sales_amounts(sources["purchase"]["records"], sources["sales"]["records"])
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
    )
    principal_chart = figure_html(
        principal_margin_figure(pd.DataFrame(principal_rows), principal_chart_spec),
        include_plotlyjs=False,
    )
    principal_table_rows = render_principal_margin_rows(principal_rows)
    item_top_rows = render_item_top_rows(top_sales_by_item(sources["sales"]["records"]))

    summary_cards = "\n".join(
        [
            card("분석 기간", period),
            card(
                "거래 건수",
                f"매입 상세 {number(purchase_meta['record_count'])}건 / 매출 상세 {number(sales_meta['record_count'])}건",
                f"재고 품목 {number(inventory_meta['unique_product_ids'])}개",
            ),
        ]
    )

    amount_rows = [
        ("매입금액", "기간 내 매입 상세 합계", money(purchase_amount)),
        ("매출금액", "기간 내 매출 상세 합계", money(sales_amount)),
        ("재고금액", "재고수량 x 평균원가", money(inventory_average)),
        ("나머지", "매출금액 + 평균원가 기준 재고금액 - 매입금액", money(remainder_average)),
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

    source_rows = "\n".join(
        f"<li>{escape(meta['source_file'])}: {escape(meta['transaction_type'])}</li>"
        for meta in [purchase_meta, sales_meta, inventory_meta]
    )
    if sources.get("sales_voucher_metadata"):
        metadata_meta = sources["sales_voucher_metadata"]["metadata"]
        source_rows += f"<li>{escape(metadata_meta['source_file'])}: sales voucher metadata</li>"

    return f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(report_title)}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2933;
      --muted: #667085;
      --line: #d8dee8;
      --accent: #2f6f7e;
      --accent-soft: #e3f1f3;
      --warn-soft: #fff4df;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.5;
    }}
    main {{
      width: min(1120px, calc(100% - 40px));
      margin: 40px auto;
    }}
    header {{
      margin-bottom: 24px;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 30px;
      letter-spacing: 0;
    }}
    .meta {{
      color: var(--muted);
      margin: 0;
      font-size: 14px;
    }}
    section {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 22px;
      margin-bottom: 18px;
    }}
    h2 {{
      margin: 0 0 16px;
      font-size: 20px;
      letter-spacing: 0;
    }}
    .cards {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }}
    .card {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      background: #fbfcfd;
      min-height: 124px;
    }}
    .card span {{
      display: block;
      color: var(--muted);
      font-size: 13px;
      margin-bottom: 8px;
    }}
    .card strong {{
      display: block;
      font-size: 22px;
      line-height: 1.25;
      letter-spacing: 0;
    }}
    .card p {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 13px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      border-bottom: 1px solid var(--line);
      padding: 12px 10px;
      text-align: left;
      vertical-align: top;
    }}
    thead th {{
      color: var(--muted);
      font-weight: 700;
      background: var(--accent-soft);
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
      margin: 14px 0 0;
      padding: 12px 14px;
      border-radius: 8px;
      background: var(--warn-soft);
      color: #5f4717;
      font-size: 14px;
    }}
    .section-note {{
      margin: -6px 0 14px;
      color: var(--muted);
      font-size: 14px;
    }}
    .table-scroll {{
      overflow-x: auto;
    }}
    .legend {{
      display: flex;
      gap: 14px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 14px;
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
    .sales-chip {{ background: #c47a23; }}
    .cost-chip {{ background: #7b8794; }}
    .chart-wrap {{
      width: 100%;
      overflow-x: auto;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 8px;
      background: #fbfcfd;
    }}
    .balance-chart-wrap {{
      margin: 0 0 18px;
      padding: 10px 12px 2px;
    }}
    .balance-chart-wrap svg {{
      display: block;
      width: 100%;
      min-width: 700px;
      height: auto;
    }}
    .balance-block {{
      stroke: #344054;
      stroke-width: 1.5;
    }}
    .balance-inventory {{ fill: #dceff2; }}
    .balance-sales {{ fill: #f7d8b5; }}
    .balance-purchase {{ fill: #8fc2cc; }}
    .balance-remainder {{ fill: url(#balance-remainder-hatch); }}
    .balance-equation {{
      fill: var(--text);
      font-size: 18px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}
    .balance-note, .balance-side-note {{
      fill: var(--muted);
      font-size: 13px;
    }}
    .balance-equality-line {{
      stroke: var(--accent);
      stroke-width: 1.5;
      stroke-dasharray: 7 6;
    }}
    .balance-total-label {{
      fill: var(--accent);
      font-size: 14px;
      font-weight: 700;
    }}
    .balance-baseline {{
      stroke: #667085;
      stroke-width: 1.2;
    }}
    .balance-segment-label {{
      fill: #1f2933;
      font-size: 15px;
      font-weight: 700;
    }}
    .balance-segment-value {{
      fill: #344054;
      font-size: 13px;
      font-weight: 700;
      font-variant-numeric: tabular-nums;
    }}
    .balance-side-title {{
      fill: var(--text);
      font-size: 16px;
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
    @media (max-width: 860px) {{
      main {{ width: min(100% - 24px, 1120px); margin: 24px auto; }}
      .cards {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    }}
    @media (max-width: 560px) {{
      .cards {{ grid-template-columns: 1fr; }}
      section {{ padding: 16px; }}
      h1 {{ font-size: 24px; }}
      .card strong {{ font-size: 19px; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{escape(report_title)}</h1>
      <p class="meta">생성일 {escape(generated)} · 범위: {escape(report_scope)}</p>
    </header>

    <section aria-labelledby="summary-title">
      <h2 id="summary-title">요약</h2>
      <div class="cards">
        {summary_cards}
      </div>
      <details>
        <summary>사용한 원본 파일</summary>
        <ul>
          {source_rows}
        </ul>
      </details>
    </section>

    <section aria-labelledby="amount-title">
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
      <p class="formula">나머지 = 매출금액 + 재고금액 - 매입금액. 재고금액은 평균원가 기준이다.</p>
    </section>

    <section aria-labelledby="weekly-title">
      <h2 id="weekly-title">{escape(weekly_chart_spec['title'])}</h2>
      <p class="section-note">월요일 시작 주 단위로 매입금액과 매출금액을 합산했다.</p>
      <p class="section-note">축: {escape(weekly_chart_spec.get('x_axis_title', '주간'))} / {escape(weekly_chart_spec.get('y_axis_title', '금액'))}</p>
      <p class="section-note">표시 단위: {escape(weekly_chart_spec.get('unit_label', '원'))}</p>
      <div class="legend">
        <span><i class="purchase-chip"></i>매입금액</span>
        <span><i class="sales-chip"></i>매출금액</span>
      </div>
      {weekly_chart}
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>주간</th>
              <th class="money">매입금액</th>
              <th class="money">매출금액</th>
            </tr>
          </thead>
          <tbody>
            {weekly_table_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section aria-labelledby="principal-title">
      <h2 id="principal-title">{escape(principal_chart_spec['title'])}</h2>
      <p class="section-note">매출 전표의 원청 메타데이터를 기준으로 묶었다. 매출원가는 기간 내 매입을 `product_id`별 가중평균 단가로 환산한 추정값이다.</p>
      <p class="section-note">축: {escape(principal_chart_spec.get('y_axis_title', '원청'))} / {escape(principal_chart_spec.get('x_axis_title', '금액'))}</p>
      <p class="section-note">표시 단위: {escape(principal_chart_spec.get('unit_label', '원'))}</p>
      <div class="legend">
        <span><i class="purchase-chip"></i>매출금액</span>
        <span><i class="cost-chip"></i>매출원가</span>
      </div>
      {principal_chart}
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th class="rank">순위</th>
              <th>원청</th>
              <th class="money">건수</th>
              <th class="money">수량</th>
              <th class="money">매출금액</th>
              <th class="money">매출원가</th>
              <th class="money">마진금액</th>
              <th class="money">마진율</th>
              <th class="money">원가 미확인 행수</th>
            </tr>
          </thead>
          <tbody>
            {principal_table_rows}
          </tbody>
        </table>
      </div>
    </section>

    <section aria-labelledby="item-top-title">
      <h2 id="item-top-title">매출 품목별 TOP</h2>
      <table>
        <thead>
          <tr>
            <th class="rank">순위</th>
            <th class="money">등록번호</th>
            <th>품명</th>
            <th class="money">건수</th>
            <th class="money">수량</th>
            <th class="money">매출금액</th>
          </tr>
        </thead>
        <tbody>
          {item_top_rows}
        </tbody>
      </table>
    </section>
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
