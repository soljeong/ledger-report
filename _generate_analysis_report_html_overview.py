#!/usr/bin/env python3
from __future__ import annotations

from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

import _generate_analysis_report_html_impl as _impl
from _generate_analysis_report_html_impl import *  # noqa: F401,F403


BRIDGE_STYLE = r"""
    .report-page--overview .hero { padding: 4px 0 15px; }
    .report-page--overview h1 { font-size: 34px; }
    .report-page--overview .subtitle { margin-top: 8px; font-size: 14px; }
    .report-page--overview .hero-meta { margin-top: 14px; gap: 8px; }
    .report-page--overview .hero-meta div { padding: 10px 12px; }
    .report-page--overview .hero-meta span { font-size: 11px; }
    .report-page--overview .hero-meta strong { margin-top: 3px; font-size: 13px; }
    .report-page--overview .metric-grid { margin-top: 14px; gap: 8px; }
    .report-page--overview .metric-card { min-height: 82px; padding: 10px 12px; }
    .report-page--overview .metric-label { font-size: 11.5px; }
    .report-page--overview .metric-card strong { margin-top: 4px; font-size: 18px; }
    .report-page--overview .metric-note { margin-top: 4px; font-size: 10px; }
    .report-page--overview .overview-grid { margin-top: 10px; gap: 10px; }
    .report-page--overview .panel { padding: 11px 13px; }
    .report-page--overview .panel h3 { margin-bottom: 6px; font-size: 14px; }
    .report-page--overview .fact { grid-template-columns: 110px minmax(0, 1fr); gap: 8px; padding: 5px 0; }
    .report-page--overview .fact dt { font-size: 11px; }
    .report-page--overview .fact dd { font-size: 11.5px; }
    .report-page--overview .method-list { padding-left: 15px; font-size: 10.5px; }
    .report-page--overview .method-list li + li { margin-top: 3px; }
    .profit-flow-bridge {
      margin-top: 11px;
      padding: 11px 13px 9px;
      border: 1px solid var(--line);
      border-radius: 14px;
      background: linear-gradient(180deg, #fff, #fbfcfd);
    }
    .profit-flow-bridge__head {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 2px;
    }
    .profit-flow-bridge__title { display: flex; align-items: center; gap: 8px; }
    .profit-flow-bridge__chip {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 27px;
      height: 21px;
      border-radius: 6px;
      background: #2f6f7e;
      color: #fff;
      font-size: 11px;
      font-weight: 800;
    }
    .profit-flow-bridge h3 { margin: 0; font-size: 16px; }
    .profit-flow-bridge__head p {
      margin: 1px 0 0;
      max-width: 420px;
      color: var(--muted);
      font-size: 10.5px;
      text-align: right;
    }
    .profit-flow-bridge svg {
      display: block;
      width: 100%;
      height: auto;
      max-height: 236px;
      font-variant-numeric: tabular-nums;
    }
    .profit-flow-bridge__caption {
      margin: 1px 0 0;
      color: var(--muted);
      font-size: 10px;
      line-height: 1.35;
    }
"""


def _summary_decimal(summary: dict[str, Any], *names: str) -> Decimal:
    for name in names:
        value = summary.get(name)
        if value is None:
            continue
        amount = _impl._base.decimal_amount(value)
        if amount is not None:
            return amount
    return Decimal("0")


def _money(value: Decimal) -> str:
    return _impl._base.money(_impl._base.rounded_amount(value))


def _movement_label(value: Decimal, result: bool = False, plain: bool = False) -> str:
    if result:
        return f"={_money(value)}"
    if plain:
        return _money(value)
    if value > 0:
        return f"+{_money(value)}"
    if value < 0:
        return f"−{_money(abs(value))}"
    return "0"


def render_profit_flow_bridge(summary: dict[str, Any]) -> str:
    """Render the mockup's option 2b as a cost-aware waterfall bridge."""
    sales = _summary_decimal(
        summary,
        "period_sales_supply_amount_exact",
        "period_sales_supply_amount",
        "sales_amount",
    )
    ending_inventory = _summary_decimal(
        summary,
        "ending_fifo_inventory_amount_exact",
        "inventory_amount_at_fifo",
        "ending_fifo_inventory_amount",
    )
    prior_shortage = _summary_decimal(
        summary,
        "prior_period_shortage_settlement_amount_exact",
        "prior_period_shortage_settlement_amount",
    )
    purchase = _summary_decimal(
        summary,
        "period_purchase_cost_amount_exact",
        "period_purchase_cost_amount",
        "purchase_amount",
    )
    backfill = _summary_decimal(
        summary,
        "period_backfilled_amount_exact",
        "post_period_backfill_amount",
        "backfilled_amount",
    )
    opening = _summary_decimal(summary, "opening_stock_amount_exact", "opening_stock_amount")
    gross_profit = _summary_decimal(summary, "gross_profit_exact", "gross_profit")

    inventory_step = ending_inventory + prior_shortage
    purchase_step = purchase + backfill
    deltas = [sales, inventory_step, -purchase_step, -opening]
    cumulative: list[Decimal] = [Decimal("0")]
    for delta in deltas:
        cumulative.append(cumulative[-1] + delta)

    values_for_scale = [Decimal("0"), gross_profit, *cumulative]
    low = min(values_for_scale)
    high = max(values_for_scale)
    span = high - low
    if span == 0:
        span = Decimal("1")
    padding = span * Decimal("0.12")
    low -= padding
    high += padding

    plot_top = 28.0
    plot_bottom = 216.0
    plot_height = plot_bottom - plot_top

    def y(value: Decimal) -> float:
        return plot_top + float((high - value) / (high - low)) * plot_height

    steps = [
        ("매출", sales, Decimal("0"), sales, "#2f6f7e"),
        ("＋ 기말재고", inventory_step, cumulative[1], cumulative[2], "#7fb4bd"),
        ("－ 매입", -purchase_step, cumulative[2], cumulative[3], "#c96f4a"),
        ("－ 기초재고", -opening, cumulative[3], cumulative[4], "#e0a98f"),
        ("＝ 매출총이익", gross_profit, Decimal("0"), gross_profit, "#1f4d57"),
    ]
    xs = [73, 205, 337, 469, 601]
    bar_width = 78
    bars: list[str] = []
    connectors: list[str] = []
    labels: list[str] = []

    for index, ((category, movement, start, end, color), x_pos) in enumerate(zip(steps, xs)):
        top = min(y(start), y(end))
        bottom = max(y(start), y(end))
        height = max(bottom - top, 2.0)
        if height == 2.0:
            top -= 1.0
        bars.append(
            f'<rect x="{x_pos}" y="{top:.1f}" width="{bar_width}" height="{height:.1f}" '
            f'rx="3" fill="{color}"></rect>'
        )
        value_text = _movement_label(
            movement,
            result=index == len(steps) - 1,
            plain=index == 0,
        )
        label_y = max(17.0, top - 6.0) if movement >= 0 else min(235.0, bottom + 13.0)
        label_color = "#1f4d57" if index == 4 else color
        labels.append(
            f'<text x="{x_pos + bar_width / 2:.1f}" y="{label_y:.1f}" text-anchor="middle" '
            f'fill="{label_color}" font-size="10.5" font-weight="800">{escape(value_text)}</text>'
        )
        labels.append(
            f'<text x="{x_pos + bar_width / 2:.1f}" y="255" text-anchor="middle" '
            f'fill="#344054" font-size="11" font-weight="700">{escape(category)}</text>'
        )
        if index < 4:
            connector_value = end
            connectors.append(
                f'<line x1="{x_pos + bar_width}" y1="{y(connector_value):.1f}" '
                f'x2="{xs[index + 1]}" y2="{y(connector_value):.1f}" '
                'stroke="#98a2b3" stroke-width="1" stroke-dasharray="4 4"></line>'
            )

    adjustment_notes: list[str] = []
    if prior_shortage:
        adjustment_notes.append(f"기말재고 단계에 이전기간 부족 보충 {_money(prior_shortage)}원 포함")
    if backfill:
        adjustment_notes.append(f"매입 단계에 후속 소급배정 {_money(backfill)}원 포함")
    adjustment_text = " · " + " · ".join(adjustment_notes) if adjustment_notes else ""
    zero_y = y(Decimal("0"))

    return f"""
      <section class="profit-flow-bridge" aria-labelledby="profit-flow-bridge-title">
        <div class="profit-flow-bridge__head">
          <div class="profit-flow-bridge__title">
            <span class="profit-flow-bridge__chip">2b</span>
            <h3 id="profit-flow-bridge-title">손익 흐름 브릿지</h3>
          </div>
          <p>매출 공급가액에 기말 재고를 더하고 매입·기초재고를 차감해 매출총이익으로 연결합니다.</p>
        </div>
        <svg id="profit-flow-bridge" viewBox="0 0 752 270" role="img" aria-label="손익 흐름 폭포 차트">
          <title>손익 흐름 브릿지</title>
          <desc>매출, 기말 재고, 기간 매입원가, 기초 재고를 거쳐 매출총이익을 보여주는 폭포 차트</desc>
          <g stroke="#eef2f6" stroke-width="1">
            <line x1="48" y1="65" x2="710" y2="65"></line>
            <line x1="48" y1="122" x2="710" y2="122"></line>
            <line x1="48" y1="179" x2="710" y2="179"></line>
          </g>
          <line x1="48" y1="{zero_y:.1f}" x2="710" y2="{zero_y:.1f}" stroke="#667085" stroke-width="1.2"></line>
          <text x="49" y="17" fill="#98a2b3" font-size="9.5">금액(원)</text>
          {''.join(connectors)}
          {''.join(bars)}
          {''.join(labels)}
        </svg>
        <p class="profit-flow-bridge__caption">원가 항등식 기준 · 공급가액 기준 손익{escape(adjustment_text)}</p>
      </section>
    """


def render_report_html(sources: dict[str, Any], spec: dict[str, Any] | None = None) -> str:
    html = _impl.render_report_html(sources, spec)
    summary = sources["reconciliation"]["summary"]
    bridge = render_profit_flow_bridge(summary)
    html = html.replace("  </style>", f"{BRIDGE_STYLE}\n  </style>", 1)
    marker = '      <section class="overview-grid">'
    if marker not in html:
        return html
    return html.replace(marker, f"{bridge}\n\n{marker}", 1)


def generate_report(
    input_dir: Path,
    output_path: Path,
    spec_path: Path = _impl._base.DEFAULT_SPEC,
) -> None:
    sources = _impl._base.load_sources(input_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_report_html(sources, _impl._base.load_report_spec(spec_path)),
        encoding="utf-8",
    )


def main() -> int:
    args = _impl._base.parse_args()
    generate_report(args.input_dir, args.output, args.spec)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
