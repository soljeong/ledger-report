#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "private_intermediate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile purchase, sales, and inventory by product_id.")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_DATA_DIR / "inventory_reconciliation.json")
    parser.add_argument("--md-out", type=Path, default=DEFAULT_DATA_DIR / "inventory_reconciliation.md")
    return parser.parse_args()


def load_payloads(base_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    purchase = json.loads((base_dir / "purchase.json").read_text(encoding="utf-8"))
    sales = json.loads((base_dir / "sales.json").read_text(encoding="utf-8"))
    inventory = json.loads((base_dir / "inventory.json").read_text(encoding="utf-8"))
    return purchase, sales, inventory


def amount(value: Any) -> float:
    return value or 0


def money_total(value: float) -> int:
    return round(value)


def product_name(names: set[str | None]) -> str | None:
    cleaned = sorted(name for name in names if name)
    return cleaned[0] if cleaned else None


def build_reconciliation(
    purchase_payload: dict[str, Any],
    sales_payload: dict[str, Any],
    inventory_payload: dict[str, Any],
) -> dict[str, Any]:
    purchase = purchase_payload["records"]
    sales = sales_payload["records"]
    inventory = inventory_payload["records"]

    by_product: dict[int, dict[str, Any]] = defaultdict(
        lambda: {
            "item_names": set(),
            "purchase_quantity": 0,
            "sales_quantity": 0,
            "inventory_quantity": 0,
            "purchase_amount": 0,
            "sales_amount": 0,
            "inventory_amount_at_average_cost": 0,
            "inventory_amount_at_latest_purchase_price": 0,
        }
    )

    for row in purchase:
        product_id = row["product_id"]
        product = by_product[product_id]
        product["item_names"].add(row["item_name"])
        product["purchase_quantity"] += amount(row["quantity"])
        product["purchase_amount"] += amount(row["total_amount"])

    for row in sales:
        product_id = row["product_id"]
        product = by_product[product_id]
        product["item_names"].add(row["item_name"])
        product["sales_quantity"] += amount(row["quantity"])
        product["sales_amount"] += amount(row["total_amount"])

    for row in inventory:
        product_id = row["product_id"]
        product = by_product[product_id]
        product["item_names"].add(row["item_name"])
        stock_quantity = amount(row["stock_quantity"])
        product["inventory_quantity"] += stock_quantity
        product["inventory_amount_at_average_cost"] += stock_quantity * amount(row["average_cost"])
        product["inventory_amount_at_latest_purchase_price"] += stock_quantity * amount(row["latest_purchase_price"])

    purchase_ids = {row["product_id"] for row in purchase}
    sales_ids = {row["product_id"] for row in sales}
    inventory_ids = {row["product_id"] for row in inventory}

    rows: list[dict[str, Any]] = []
    calculated_negative_stock: list[dict[str, Any]] = []
    inventory_negative_stock: list[dict[str, Any]] = []
    quantity_mismatches: list[dict[str, Any]] = []

    for product_id in sorted(by_product):
        product = by_product[product_id]
        calculated_quantity = product["purchase_quantity"] - product["sales_quantity"]
        inventory_quantity = product["inventory_quantity"]
        row = {
            "product_id": product_id,
            "item_name": product_name(product["item_names"]),
            "item_names": sorted(name for name in product["item_names"] if name),
            "purchase_quantity": product["purchase_quantity"],
            "sales_quantity": product["sales_quantity"],
            "calculated_stock_quantity": calculated_quantity,
            "inventory_quantity": inventory_quantity,
            "quantity_difference": inventory_quantity - calculated_quantity,
            "purchase_amount": money_total(product["purchase_amount"]),
            "sales_amount": money_total(product["sales_amount"]),
            "inventory_amount_at_average_cost": money_total(product["inventory_amount_at_average_cost"]),
            "inventory_amount_at_latest_purchase_price": money_total(product["inventory_amount_at_latest_purchase_price"]),
        }
        rows.append(row)
        if calculated_quantity < 0:
            calculated_negative_stock.append(row)
        if inventory_quantity < 0:
            inventory_negative_stock.append(row)
        if row["quantity_difference"] != 0 and (product_id in purchase_ids or product_id in sales_ids):
            quantity_mismatches.append(row)

    purchase_amount = money_total(sum(amount(row["total_amount"]) for row in purchase))
    sales_amount = money_total(sum(amount(row["total_amount"]) for row in sales))
    inventory_amount_at_average_cost = money_total(
        sum(amount(row["stock_quantity"]) * amount(row["average_cost"]) for row in inventory)
    )
    inventory_amount_at_latest_purchase_price = money_total(
        sum(amount(row["stock_quantity"]) * amount(row["latest_purchase_price"]) for row in inventory)
    )

    return {
        "summary": {
            "purchase_records": len(purchase),
            "sales_records": len(sales),
            "inventory_records": len(inventory),
            "purchase_product_ids": len(purchase_ids),
            "sales_product_ids": len(sales_ids),
            "inventory_product_ids": len(inventory_ids),
            "sales_without_purchase_product_id_count": len(sales_ids - purchase_ids),
            "sales_without_purchase_product_ids": sorted(sales_ids - purchase_ids),
            "purchase_without_inventory_product_id_count": len(purchase_ids - inventory_ids),
            "purchase_without_inventory_product_ids": sorted(purchase_ids - inventory_ids),
            "sales_without_inventory_product_id_count": len(sales_ids - inventory_ids),
            "sales_without_inventory_product_ids": sorted(sales_ids - inventory_ids),
            "calculated_negative_stock_count": len(calculated_negative_stock),
            "inventory_negative_stock_count": len(inventory_negative_stock),
            "quantity_mismatch_count": len(quantity_mismatches),
            "purchase_amount": purchase_amount,
            "sales_amount": sales_amount,
            "inventory_amount_at_average_cost": inventory_amount_at_average_cost,
            "inventory_amount_at_latest_purchase_price": inventory_amount_at_latest_purchase_price,
            "remainder_at_average_cost": sales_amount + inventory_amount_at_average_cost - purchase_amount,
            "remainder_at_latest_purchase_price": sales_amount + inventory_amount_at_latest_purchase_price - purchase_amount,
        },
        "calculated_negative_stock": calculated_negative_stock,
        "inventory_negative_stock": inventory_negative_stock,
        "quantity_mismatches": quantity_mismatches,
        "rows": rows,
    }


def money(value: int | float) -> str:
    return f"{int(round(value)):,}"


def render_markdown(analysis: dict[str, Any]) -> str:
    summary = analysis["summary"]
    lines = [
        "# 매입/매출/재고 product_id 대사",
        "",
        "## 요약",
        "",
        f"- 매입 행: {summary['purchase_records']:,}건 / product_id {summary['purchase_product_ids']:,}개",
        f"- 매출 행: {summary['sales_records']:,}건 / product_id {summary['sales_product_ids']:,}개",
        f"- 재고 행: {summary['inventory_records']:,}건 / product_id {summary['inventory_product_ids']:,}개",
        f"- 매출은 있으나 매입이 없는 product_id: {summary['sales_without_purchase_product_id_count']:,}개",
        f"- 매입/매출 계산 재고가 음수인 product_id: {summary['calculated_negative_stock_count']:,}개",
        f"- 재고 파일 수량이 음수인 product_id: {summary['inventory_negative_stock_count']:,}개",
        f"- 매입-매출 계산 수량과 재고 파일 수량 불일치: {summary['quantity_mismatch_count']:,}개",
        "",
        "## 금액",
        "",
        f"- 매입금액: {money(summary['purchase_amount'])}",
        f"- 매출금액: {money(summary['sales_amount'])}",
        f"- 재고금액(평균원가): {money(summary['inventory_amount_at_average_cost'])}",
        f"- 재고금액(최종매입가): {money(summary['inventory_amount_at_latest_purchase_price'])}",
        f"- 나머지(평균원가): {money(summary['remainder_at_average_cost'])}",
        f"- 나머지(최종매입가): {money(summary['remainder_at_latest_purchase_price'])}",
        "",
        "## 음수 재고",
        "",
    ]
    if not analysis["calculated_negative_stock"]:
        lines.append("- 없음")
    else:
        for row in analysis["calculated_negative_stock"]:
            lines.append(
                f"- product_id `{row['product_id']}` `{row['item_name']}`: "
                f"매입 {row['purchase_quantity']:,}, 매출 {row['sales_quantity']:,}, "
                f"계산재고 {row['calculated_stock_quantity']:,}, 재고파일 {row['inventory_quantity']:,}"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    purchase, sales, inventory = load_payloads(args.base_dir)
    analysis = build_reconciliation(purchase, sales, inventory)
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
