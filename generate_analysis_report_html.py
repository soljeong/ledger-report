#!/usr/bin/env python3
from __future__ import annotations

from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

import _generate_analysis_report_html_overview as _overview
from _generate_analysis_report_html_overview import *  # noqa: F401,F403


PRINCIPAL_CHART_STYLE = r"""
    .principal-stack-chart {
      margin: 0;
      padding: 14px 16px 10px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: #fff;
    }
    .principal-stack-chart__legend {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 12px;
      margin: 0 0 12px;
      color: var(--muted);
      font-size: 11.5px;
    }
    .principal-stack-chart__legend span {
      display: inline-flex;
      align-items: center;
      gap: 6px;
    }
    .principal-stack-chart__legend i {
      display: inline-block;
      width: 10px;
      height: 10px;
      border-radius: 3px;
    }
    .principal-stack-chart__legend .cost { background: #c47a23; }
    .principal-stack-chart__legend .margin { background: #2f6f7e; }
    .principal-stack-chart__legend-note { color: #98a2b3; }
    .principal-stack-chart__rows {
      display: flex;
      flex-direction: column;
      gap: 9px;
    }
    .principal-stack-chart__row,
    .principal-stack-chart__axis {
      display: grid;
      grid-template-columns: 108px minmax(0, 1fr) 116px;
      align-items: center;
      gap: 12px;
    }
    .principal-stack-chart__name {
      overflow: hidden;
      color: var(--ink);
      font-size: 11.5px;
      font-weight: 700;
      text-align: right;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .principal-stack-chart__track {
      position: relative;
      height: 28px;
      background:
        linear-gradient(to right, transparent calc(25% - .5px), #edf0f3 calc(25% - .5px), #edf0f3 calc(25% + .5px), transparent calc(25% + .5px)),
        linear-gradient(to right, transparent calc(50% - .5px), #edf0f3 calc(50% - .5px), #edf0f3 calc(50% + .5px), transparent calc(50% + .5px)),
        linear-gradient(to right, transparent calc(75% - .5px), #edf0f3 calc(75% - .5px), #edf0f3 calc(75% + .5px), transparent calc(75% + .5px));
      border-left: 1px solid #edf0f3;
      border-right: 1px solid #edf0f3;
    }
    .principal-stack-chart__bar {
      position: absolute;
      top: 3px;
      left: 0;
      display: flex;
      height: 22px;
      overflow: hidden;
      border-radius: 5px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, .10);
    }
    .principal-stack-chart__bar--negative {
      overflow: visible;
      background: #c47a23;
    }
    .principal-stack-chart__bar--negative::after {
      position: absolute;
      top: -2px;
      right: -3px;
      width: 6px;
      height: 26px;
      border-radius: 2px;
      background: #b42318;
      content: "";
    }
    .principal-stack-chart__cost,
    .principal-stack-chart__margin {
      display: flex;
      min-width: 0;
      align-items: center;
      justify-content: center;
      color: #fff;
      font-size: 9.5px;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }
    .principal-stack-chart__cost { background: #c47a23; }
    .principal-stack-chart__margin { background: #2f6f7e; }
    .principal-stack-chart__empty {
      position: absolute;
      top: 5px;
      left: 0;
      width: 3px;
      height: 18px;
      border-radius: 2px;
      background: #cfd6de;
    }
    .principal-stack-chart__value {
      display: flex;
      align-items: baseline;
      gap: 7px;
      white-space: nowrap;
    }
    .principal-stack-chart__value strong {
      color: var(--ink);
      font-size: 11.5px;
      font-variant-numeric: tabular-nums;
    }
    .principal-stack-chart__value span {
      color: var(--teal);
      font-size: 10.5px;
      font-weight: 800;
      font-variant-numeric: tabular-nums;
    }
    .principal-stack-chart__value .negative { color: #b42318; }
    .principal-stack-chart__axis {
      margin-top: 2px;
      color: #98a2b3;
      font-size: 9.5px;
      font-variant-numeric: tabular-nums;
    }
    .principal-stack-chart__ticks {
      position: relative;
      height: 18px;
    }
    .principal-stack-chart__ticks span {
      position: absolute;
      top: 1px;
      transform: translateX(-50%);
    }
    .principal-stack-chart__ticks span:first-child { transform: none; }
    .principal-stack-chart__ticks span:last-child { transform: translateX(-100%); }
    .principal-stack-chart__unit { align-self: start; }
    .principal-stack-chart__caption {
      margin: 7px 0 0;
      color: var(--muted);
      font-size: 10px;
    }
"""


def _principal_decimal(row: dict[str, Any], *names: str) -> Decimal:
    for name in names:
        value = row.get(name)
        if value is None:
            continue
        amount = _overview._impl._base.decimal_amount(value)
        if amount is not None:
            return amount
    return Decimal("0")


def _principal_money(value: Decimal) -> str:
    return _overview._impl._base.money(_overview._impl._base.rounded_amount(value))


def _principal_rate(margin: Decimal, sales: Decimal) -> str:
    if sales == 0:
        return "-"
    return f"{float(margin / sales * Decimal('100')):.1f}%"


def render_principal_sales_chart(rows: list[dict[str, Any]]) -> str:
    """Render revenue as a horizontal bar split into cost and gross margin."""
    prepared: list[dict[str, Any]] = []
    for row in rows:
        sales = _principal_decimal(row, "sales_amount_exact", "sales_amount")
        cost = _principal_decimal(row, "cost_amount_exact", "cost_amount")
        margin = sales - cost
        prepared.append(
            {
                "principal": str(row.get("principal") or "(원청 없음)"),
                "sales": sales,
                "cost": cost,
                "margin": margin,
            }
        )

    max_sales = max((row["sales"] for row in prepared if row["sales"] > 0), default=Decimal("1"))
    tick_values = [max_sales * Decimal(index) / Decimal("4") for index in range(5)]
    row_html: list[str] = []

    for row in prepared:
        sales = row["sales"]
        cost = row["cost"]
        margin = row["margin"]
        bar_width = max(Decimal("0"), min(Decimal("100"), sales / max_sales * Decimal("100")))
        rate = _principal_rate(margin, sales)
        rate_class = "negative" if margin < 0 else ""
        value_html = (
            f'<div class="principal-stack-chart__value">'
            f'<strong>{escape(_principal_money(sales))}</strong>'
            f'<span class="{rate_class}">{escape(rate)}</span>'
            f"</div>"
        )

        if sales <= 0:
            bar_html = '<span class="principal-stack-chart__empty" aria-hidden="true"></span>'
        elif cost >= 0 and margin >= 0:
            cost_width = cost / sales * Decimal("100")
            margin_width = margin / sales * Decimal("100")
            show_cost = cost / max_sales >= Decimal("0.11")
            show_margin = margin / max_sales >= Decimal("0.075")
            cost_label = escape(_principal_money(cost)) if show_cost else ""
            margin_label = escape(_principal_money(margin)) if show_margin else ""
            bar_html = (
                f'<div class="principal-stack-chart__bar" style="width:{float(bar_width):.2f}%">'
                f'<div class="principal-stack-chart__cost" style="width:{float(cost_width):.2f}%" '
                f'title="매출원가 {_principal_money(cost)}원">{cost_label}</div>'
                f'<div class="principal-stack-chart__margin" style="width:{float(margin_width):.2f}%" '
                f'title="매출총이익 {_principal_money(margin)}원">{margin_label}</div>'
                f"</div>"
            )
        else:
            bar_html = (
                f'<div class="principal-stack-chart__bar principal-stack-chart__bar--negative" '
                f'style="width:{float(bar_width):.2f}%" '
                f'title="매출 공급가액 {_principal_money(sales)}원 · 매출원가 {_principal_money(cost)}원 · '
                f'매출총이익 {_principal_money(margin)}원"></div>'
            )

        row_html.append(
            f"""
            <div class="principal-stack-chart__row">
              <div class="principal-stack-chart__name" title="{escape(row['principal'])}">{escape(row['principal'])}</div>
              <div class="principal-stack-chart__track">{bar_html}</div>
              {value_html}
            </div>
            """
        )

    ticks = "".join(
        f'<span style="left:{index * 25}%">{escape(_principal_money(value))}</span>'
        for index, value in enumerate(tick_values)
    )
    empty_note = (
        '<p class="principal-stack-chart__caption">표시할 원청별 매출 데이터가 없습니다.</p>'
        if not prepared
        else '<p class="principal-stack-chart__caption">막대 길이는 매출 공급가액이며, 내부 구간은 매출원가와 매출총이익입니다. 음수 마진은 우측 값과 막대 끝 표시로 구분합니다.</p>'
    )

    return f"""
        <div class="principal-stack-chart" id="principal-stacked-sales-chart" aria-label="원청별 매출 공급가액과 원가 및 매출총이익">
          <div class="principal-stack-chart__legend">
            <span><i class="cost"></i>매출원가</span>
            <span><i class="margin"></i>매출총이익</span>
            <span class="principal-stack-chart__legend-note">막대 합계 = 매출 공급가액</span>
          </div>
          <div class="principal-stack-chart__rows">
            {''.join(row_html)}
            <div class="principal-stack-chart__axis">
              <div></div>
              <div class="principal-stack-chart__ticks">{ticks}</div>
              <div class="principal-stack-chart__unit">금액(원)</div>
            </div>
          </div>
          {empty_note}
        </div>
    """


def _replace_principal_chart(html: str, chart: str) -> str:
    title_position = html.find('id="principal-title"')
    if title_position < 0:
        return html
    section_marker = '      <section class="section">'
    section_position = html.find(section_marker, title_position)
    if section_position < 0:
        return html
    table_marker = '        <div class="table-wrap" style="margin-top: 12px;">'
    table_position = html.find(table_marker, section_position)
    if table_position < 0:
        return html
    content_start = section_position + len(section_marker)
    return html[:content_start] + f"\n{chart}\n\n" + html[table_position:]


def render_report_html(sources: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    html = _overview.render_report_html(sources, spec)
    principal_rows = _overview._impl._base.principal_sales_cost_rows(
        sources["sales"].get("records", []),
        sources["purchase"].get("records", []),
        sources.get("sales_voucher_metadata"),
        sources["reconciliation"],
    )[:8]
    principal_chart = render_principal_sales_chart(principal_rows)
    html = html.replace("  </style>", f"{PRINCIPAL_CHART_STYLE}\n  </style>", 1)
    return _replace_principal_chart(html, principal_chart)


def generate_report(
    input_dir: Path,
    output_path: Path,
    spec_path: Path = _overview._impl._base.DEFAULT_SPEC,
) -> None:
    sources = _overview._impl._base.load_sources(input_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_report_html(sources, _overview._impl._base.load_report_spec(spec_path)),
        encoding="utf-8",
    )


def main() -> int:
    args = _overview._impl._base.parse_args()
    generate_report(args.input_dir, args.output, args.spec)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
