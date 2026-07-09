#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "private_intermediate"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze parsed purchase/sales JSON files.")
    parser.add_argument("--base-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--json-out", type=Path, default=DEFAULT_DATA_DIR / "validation.json")
    parser.add_argument("--md-out", type=Path, default=DEFAULT_DATA_DIR / "validation.md")
    return parser.parse_args()


def normalize(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value).strip()).upper()


def load_records(base_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    purchase = json.loads((base_dir / "purchase.json").read_text(encoding="utf-8"))
    sales = json.loads((base_dir / "sales.json").read_text(encoding="utf-8"))
    return purchase, sales


def side_label(transaction_type: str) -> str:
    return {"purchase": "매입", "sales": "매출"}.get(transaction_type, transaction_type)


def row_ref(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": row["transaction_type"],
        "excel_row": row["excel_row"],
        "company": row["company"],
        "date": row["date"],
        "item_name": row["item_name"],
        "specification": row["specification"],
        "quantity": row["quantity"],
        "unit_price": row["unit_price"],
        "total_amount": row["total_amount"],
    }


def build_analysis(purchase_payload: dict[str, Any], sales_payload: dict[str, Any]) -> dict[str, Any]:
    purchase = purchase_payload["records"]
    sales = sales_payload["records"]
    records = [*purchase, *sales]

    by_item: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_side_item: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    by_side_item_spec: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    by_item_spec: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    for row in records:
        item_key = normalize(row["item_name"])
        spec_key = normalize(row["specification"])
        by_item[item_key].append(row)
        by_side_item[(row["transaction_type"], item_key)].append(row)
        by_side_item_spec[(row["transaction_type"], item_key, spec_key)].append(row)
        by_item_spec[(item_key, spec_key)].append(row)

    purchase_items = {normalize(row["item_name"]) for row in purchase}
    sales_items = {normalize(row["item_name"]) for row in sales}
    purchase_item_specs = {(normalize(row["item_name"]), normalize(row["specification"])) for row in purchase}
    sales_item_specs = {(normalize(row["item_name"]), normalize(row["specification"])) for row in sales}

    same_item_different_specs = []
    for item_key, rows in sorted(by_item.items()):
        specs = defaultdict(list)
        for row in rows:
            specs[normalize(row["specification"])].append(row)
        if len(specs) <= 1:
            continue
        same_item_different_specs.append(
            {
                "item_key": item_key,
                "item_names": sorted({row["item_name"] for row in rows}),
                "side_counts": dict(Counter(row["transaction_type"] for row in rows)),
                "specifications": [
                    {
                        "spec_key": spec_key,
                        "specifications": sorted({row["specification"] for row in spec_rows}),
                        "rows": [row_ref(row) for row in spec_rows],
                    }
                    for spec_key, spec_rows in sorted(specs.items())
                ],
            }
        )

    def sales_total_for_item(item_key: str) -> int | float:
        return sum(row["total_amount"] for row in by_side_item[("sales", item_key)])

    sales_without_purchase_item = [
        {
            "item_key": item_key,
            "item_names": sorted({row["item_name"] for row in by_side_item[("sales", item_key)]}),
            "sales_rows": len(by_side_item[("sales", item_key)]),
            "sales_total_amount": sales_total_for_item(item_key),
            "specifications": sorted({row["specification"] for row in by_side_item[("sales", item_key)]}),
            "rows": [row_ref(row) for row in by_side_item[("sales", item_key)]],
        }
        for item_key in sorted(sales_items - purchase_items)
    ]
    sales_without_purchase_item.sort(key=lambda row: row["sales_total_amount"], reverse=True)

    purchase_without_sales_item = [
        {
            "item_key": item_key,
            "item_names": sorted({row["item_name"] for row in by_side_item[("purchase", item_key)]}),
            "purchase_rows": len(by_side_item[("purchase", item_key)]),
            "purchase_total_amount": sum(row["total_amount"] for row in by_side_item[("purchase", item_key)]),
            "specifications": sorted({row["specification"] for row in by_side_item[("purchase", item_key)]}),
            "rows": [row_ref(row) for row in by_side_item[("purchase", item_key)]],
        }
        for item_key in sorted(purchase_items - sales_items)
    ]
    purchase_without_sales_item.sort(key=lambda row: row["purchase_total_amount"], reverse=True)

    sales_without_purchase_item_spec = []
    for item_key, spec_key in sorted(sales_item_specs - purchase_item_specs):
        rows = by_side_item_spec[("sales", item_key, spec_key)]
        sales_without_purchase_item_spec.append(
            {
                "item_key": item_key,
                "spec_key": spec_key,
                "item_names": sorted({row["item_name"] for row in rows}),
                "specifications": sorted({row["specification"] for row in rows}),
                "sales_rows": len(rows),
                "sales_total_amount": sum(row["total_amount"] for row in rows),
                "rows": [row_ref(row) for row in rows],
            }
        )
    sales_without_purchase_item_spec.sort(key=lambda row: row["sales_total_amount"], reverse=True)

    multi_price_same_item_spec = []
    for (transaction_type, item_key, spec_key), rows in sorted(by_side_item_spec.items()):
        prices = sorted({row["unit_price"] for row in rows})
        if len(prices) <= 1:
            continue
        multi_price_same_item_spec.append(
            {
                "type": transaction_type,
                "item_key": item_key,
                "spec_key": spec_key,
                "prices": prices,
                "row_count": len(rows),
                "rows": [row_ref(row) for row in rows],
            }
        )

    sales_price_below_purchase = []
    for item_key, spec_key in sorted(purchase_item_specs & sales_item_specs):
        purchase_rows = by_side_item_spec[("purchase", item_key, spec_key)]
        sales_rows = by_side_item_spec[("sales", item_key, spec_key)]
        purchase_prices = [row["unit_price"] for row in purchase_rows]
        sales_prices = [row["unit_price"] for row in sales_rows]
        if not purchase_prices or not sales_prices:
            continue
        max_purchase = max(purchase_prices)
        min_sales = min(sales_prices)
        if min_sales < max_purchase:
            sales_price_below_purchase.append(
                {
                    "item_key": item_key,
                    "spec_key": spec_key,
                    "min_sales_unit_price": min_sales,
                    "max_purchase_unit_price": max_purchase,
                    "unit_price_delta": min_sales - max_purchase,
                    "purchase_rows": [row_ref(row) for row in purchase_rows],
                    "sales_rows": [row_ref(row) for row in sales_rows],
                }
            )
    sales_price_below_purchase.sort(key=lambda row: row["unit_price_delta"])

    return {
        "summary": {
            "purchase_records": len(purchase),
            "sales_records": len(sales),
            "purchase_unique_items": len(purchase_items),
            "sales_unique_items": len(sales_items),
            "same_item_different_specs_count": len(same_item_different_specs),
            "sales_without_purchase_item_count": len(sales_without_purchase_item),
            "purchase_without_sales_item_count": len(purchase_without_sales_item),
            "sales_without_purchase_item_spec_count": len(sales_without_purchase_item_spec),
            "multi_price_same_item_spec_count": len(multi_price_same_item_spec),
            "sales_price_below_purchase_count": len(sales_price_below_purchase),
            "zero_filled_rows": {
                "purchase": purchase_payload["metadata"].get("zero_filled_numeric_rows", []),
                "sales": sales_payload["metadata"].get("zero_filled_numeric_rows", []),
            },
        },
        "same_item_different_specs": same_item_different_specs,
        "sales_without_purchase_item": sales_without_purchase_item,
        "purchase_without_sales_item": purchase_without_sales_item,
        "sales_without_purchase_item_spec": sales_without_purchase_item_spec,
        "multi_price_same_item_spec": multi_price_same_item_spec,
        "sales_price_below_purchase": sales_price_below_purchase,
    }


def money(value: int | float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{int(value):,}"


def render_markdown(analysis: dict[str, Any]) -> str:
    summary = analysis["summary"]
    lines = [
        "# 매입/매출 검증 리포트",
        "",
        "## 요약",
        "",
        f"- 매입 상세 행: {summary['purchase_records']:,}건",
        f"- 매출 상세 행: {summary['sales_records']:,}건",
        f"- 매입 고유 품명: {summary['purchase_unique_items']:,}개",
        f"- 매출 고유 품명: {summary['sales_unique_items']:,}개",
        f"- 같은 품명에 서로 다른 규격 존재: {summary['same_item_different_specs_count']:,}개 품명",
        f"- 매입 품명 없이 매출만 있는 품명: {summary['sales_without_purchase_item_count']:,}개",
        f"- 매입 품명은 있으나 같은 품명+규격 매입이 없는 매출 품목: {summary['sales_without_purchase_item_spec_count']:,}개",
        f"- 같은 품명+규격 안에서 단가가 여러 개인 케이스: {summary['multi_price_same_item_spec_count']:,}개",
        f"- 같은 품명+규격에서 최저 매출단가가 최고 매입단가보다 낮은 케이스: {summary['sales_price_below_purchase_count']:,}개",
        "",
        "## 0원 처리 행",
        "",
    ]

    zero_rows = summary["zero_filled_rows"]
    if not zero_rows["purchase"] and not zero_rows["sales"]:
        lines.append("- 없음")
    else:
        for label, rows in [("매입", zero_rows["purchase"]), ("매출", zero_rows["sales"])]:
            if rows:
                lines.append(f"- {label}: {rows}")

    lines.extend(["", "## 같은 품명 / 다른 규격", ""])
    if not analysis["same_item_different_specs"]:
        lines.append("- 없음")
    else:
        for entry in analysis["same_item_different_specs"]:
            specs = []
            for spec_entry in entry["specifications"]:
                specs.extend(spec_entry["specifications"])
            lines.append(
                f"- `{entry['item_key']}`: {len(entry['specifications'])}개 규격, "
                f"행수 {sum(entry['side_counts'].values())}건, 규격={'; '.join(specs)}"
            )

    lines.extend(["", "## 매입 없이 매출만 있는 품명", ""])
    for entry in analysis["sales_without_purchase_item"][:30]:
        lines.append(
            f"- `{entry['item_key']}`: 매출 {entry['sales_rows']}건, "
            f"합계 {money(entry['sales_total_amount'])}, 규격={'; '.join(entry['specifications'])}"
        )
    if len(analysis["sales_without_purchase_item"]) > 30:
        lines.append(f"- ... 나머지 {len(analysis['sales_without_purchase_item']) - 30}개는 JSON 참조")

    lines.extend(["", "## 매출 품명+규격 기준 매입 없음", ""])
    for entry in analysis["sales_without_purchase_item_spec"][:30]:
        lines.append(
            f"- `{entry['item_key']}` / `{entry['spec_key']}`: 매출 {entry['sales_rows']}건, "
            f"합계 {money(entry['sales_total_amount'])}"
        )
    if len(analysis["sales_without_purchase_item_spec"]) > 30:
        lines.append(f"- ... 나머지 {len(analysis['sales_without_purchase_item_spec']) - 30}개는 JSON 참조")

    lines.extend(["", "## 단가 검증", ""])
    lines.append("### 같은 품명+규격에서 여러 단가")
    for entry in analysis["multi_price_same_item_spec"][:30]:
        lines.append(
            f"- {side_label(entry['type'])} `{entry['item_key']}` / `{entry['spec_key']}`: "
            f"단가={', '.join(money(price) for price in entry['prices'])}, 행수={entry['row_count']}"
        )
    if len(analysis["multi_price_same_item_spec"]) > 30:
        lines.append(f"- ... 나머지 {len(analysis['multi_price_same_item_spec']) - 30}개는 JSON 참조")

    lines.extend(["", "### 최저 매출단가 < 최고 매입단가", ""])
    for entry in analysis["sales_price_below_purchase"][:30]:
        lines.append(
            f"- `{entry['item_key']}` / `{entry['spec_key']}`: "
            f"최저 매출 {money(entry['min_sales_unit_price'])}, "
            f"최고 매입 {money(entry['max_purchase_unit_price'])}, "
            f"차이 {money(entry['unit_price_delta'])}"
        )
    if len(analysis["sales_price_below_purchase"]) > 30:
        lines.append(f"- ... 나머지 {len(analysis['sales_price_below_purchase']) - 30}개는 JSON 참조")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    purchase_payload, sales_payload = load_records(args.base_dir)
    analysis = build_analysis(purchase_payload, sales_payload)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.md_out.write_text(render_markdown(analysis), encoding="utf-8")

    summary = analysis["summary"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {args.json_out}")
    print(f"wrote {args.md_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
