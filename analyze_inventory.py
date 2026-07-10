#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.fifo_inventory import calculate_fifo


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "private_intermediate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calculate FIFO inventory cost and dated stock reconciliation.")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--period-start", required=True, help="Analysis period start, YYYY-MM-DD")
    parser.add_argument("--period-end", required=True, help="Analysis period end, YYYY-MM-DD")
    parser.add_argument("--inventory-date", required=True, help="Stock snapshot reconciliation date, YYYY-MM-DD")
    parser.add_argument("--json-out", type=Path, default=DEFAULT_DATA_DIR / "inventory_reconciliation.json")
    parser.add_argument("--md-out", type=Path, default=DEFAULT_DATA_DIR / "inventory_reconciliation.md")
    return parser.parse_args()


def load_payloads(base_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    purchase = json.loads((base_dir / "purchase.json").read_text(encoding="utf-8"))
    sales = json.loads((base_dir / "sales.json").read_text(encoding="utf-8"))
    inventory = json.loads((base_dir / "inventory.json").read_text(encoding="utf-8"))
    return purchase, sales, inventory


def build_reconciliation(
    purchase_payload: dict[str, Any],
    sales_payload: dict[str, Any],
    inventory_payload: dict[str, Any],
    period_start: str | None = None,
    period_end: str | None = None,
    inventory_date: str | None = None,
) -> dict[str, Any]:
    all_dates = [
        row.get("date")
        for payload in (purchase_payload, sales_payload)
        for row in payload.get("records", [])
        if row.get("date")
    ]
    if not all_dates:
        raise ValueError("purchase and sales data contain no dated records")
    period_start = period_start or min(all_dates)
    period_end = period_end or max(all_dates)
    inventory_date = inventory_date or period_end
    return calculate_fifo(
        purchase_payload.get("records", []),
        sales_payload.get("records", []),
        inventory_payload.get("records", []),
        period_start,
        period_end,
        inventory_date,
    )


def money(value: Any) -> str:
    if value is None:
        return "-"
    return f"{int(round(float(value))):,}"


def render_markdown(analysis: dict[str, Any]) -> str:
    metadata = analysis["metadata"]
    summary = analysis["summary"]
    lines = [
        "# FIFO 재고원가·재고수량 대사",
        "",
        "## 분석 범위",
        "",
        f"- 분석기간: {metadata['period_start']} ~ {metadata['period_end']}",
        f"- 재고 기준일: {metadata['inventory_date']}",
        f"- 입력 거래일: {metadata['input_data_first_transaction_date']} ~ {metadata['input_data_last_transaction_date']}",
        f"- 원가 방식: {metadata['costing_method']} (재고 시트 단가 미사용)",
        "",
        "## 요약",
        "",
        f"- 기초재고: {summary['opening_stock_quantity']:,} / {money(summary['opening_stock_amount'])}원",
        f"- 기간 매입: {summary['period_purchase_quantity']:,} / {money(summary['period_purchase_cost_amount'])}원",
        f"- 기간 매출: {summary['period_sales_quantity']:,} / {money(summary['period_sales_amount'])}원",
        f"- FIFO 매출원가: {money(summary['fifo_sales_cost_amount'])}원",
        f"- 매출총이익: {money(summary['gross_profit'])}원",
        f"- 종료일 정상재고: {summary['ending_normal_stock_quantity']:,} / {money(summary['ending_fifo_inventory_amount'])}원",
        f"- 종료일 음수재고: {summary['ending_negative_stock_quantity']:,}",
        f"- 후속 매입 소급배정: {summary['backfilled_quantity']:,} / {money(summary['backfilled_amount'])}원",
        f"- 미확정 출고: {summary['unconfirmed_quantity']:,}",
        f"- 재고수량 대사 불일치: {summary['quantity_reconciliation_mismatch_count']:,}개",
        f"- 수량 계산 검증 오류: {summary.get('quantity_validation_error_count', 0):,}건",
        f"- 오류: {summary['error_count']:,}건",
        "",
        "## 계산 오류와 경고",
        "",
    ]
    lines.extend(f"- 오류: {error}" for error in analysis["errors"])
    lines.extend(f"- 경고: {warning}" for warning in analysis["warnings"])
    if not analysis["errors"] and not analysis["warnings"]:
        lines.append("- 없음")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    purchase, sales, inventory = load_payloads(args.base_dir)
    try:
        analysis = build_reconciliation(
            purchase,
            sales,
            inventory,
            args.period_start,
            args.period_end,
            args.inventory_date,
        )
    except ValueError as exc:
        raise SystemExit(f"analyze_inventory: error: {exc}") from exc
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.md_out.write_text(render_markdown(analysis), encoding="utf-8")
    print(json.dumps(analysis["summary"], ensure_ascii=False, indent=2))
    print(f"wrote {args.json_out}")
    print(f"wrote {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
