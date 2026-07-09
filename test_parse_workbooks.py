#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from parse_workbooks import parse_workbook


LEDGER_HEADERS = [
    "업체명",
    "일자",
    "전표",
    "품명",
    "규격",
    "등록번호",
    "수량",
    "단가",
    "공급금액",
    "부가세",
    "합계금액",
    "입력일자",
]


def write_workbook(path: Path, sheet_name: str, rows: list[list[object]]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    for row in rows:
        ws.append(row)
    wb.save(path)


class WorkbookParseTests(unittest.TestCase):
    def test_purchase_workbook_parses_product_id_dates_and_totals(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample_매입.xlsx"
            write_workbook(
                path,
                "매입",
                [
                    LEDGER_HEADERS,
                    ["Sample Components", 20260501, 1, "Motor Driver IC", "QFN-48", 1001, 100, 5000, 500000, 50000, 550000, 20260501],
                    ["Sample Components", 20260502, 2, "Sensor Board", "I2C Module", 1002, 80, 3000, 240000, 24000, 264000, 20260502],
                ],
            )

            output_name, payload = parse_workbook(path)

        self.assertEqual(output_name, "purchase.json")
        self.assertEqual(payload["metadata"]["record_count"], 2)
        self.assertEqual(payload["metadata"]["date_min"], "2026-05-01")
        self.assertEqual(payload["metadata"]["date_max"], "2026-05-02")
        self.assertEqual(payload["metadata"]["detail_totals"]["total_amount"], 814000)
        first = payload["records"][0]
        self.assertEqual(first["date"], "2026-05-01")
        self.assertEqual(first["item_name"], "Motor Driver IC")
        self.assertEqual(first["product_id"], 1001)

    def test_sales_workbook_zero_fills_dated_row_with_blank_amounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample_매출.xlsx"
            write_workbook(
                path,
                "매출",
                [
                    LEDGER_HEADERS,
                    ["Sample Robotics", 20260508, 1, "Motor Driver IC", "QFN-48", 1001, 40, 8000, 320000, 32000, 352000, 20260508],
                    ["Sample Robotics", 20260509, 2, "Cable Set", "4P 300mm", 1004, 30, None, None, None, None, 20260509],
                ],
            )

            output_name, payload = parse_workbook(path)

        self.assertEqual(output_name, "sales.json")
        self.assertEqual(payload["metadata"]["record_count"], 2)
        zero_row = next(row for row in payload["records"] if row["excel_row"] == 3)
        self.assertEqual(zero_row["date"], "2026-05-09")
        self.assertEqual(zero_row["raw_date"], 20260509)
        self.assertEqual(zero_row["item_name"], "Cable Set")
        self.assertEqual(zero_row["quantity"], 30)
        self.assertEqual(zero_row["unit_price"], 0)
        self.assertEqual(zero_row["supply_amount"], 0)
        self.assertEqual(zero_row["vat"], 0)
        self.assertEqual(zero_row["total_amount"], 0)
        self.assertEqual(
            payload["metadata"]["zero_filled_numeric_rows"],
            [
                {
                    "excel_row": 3,
                    "missing_fields": ["unit_price", "supply_amount", "vat", "total_amount"],
                }
            ],
        )

    def test_inventory_workbook_parses_stock_by_product_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "sample_재고.xlsx"
            write_workbook(
                path,
                "재고",
                [
                    ["품명", "규격", "등록번호", "재고합계", "평균원가", "최종매입가"],
                    ["Motor Driver IC", "QFN-48", 1001, 60, 5500, 5500],
                    ["Sensor Board", "I2C Module", 1002, None, 3300, 3300],
                ],
            )

            output_name, payload = parse_workbook(path)

        self.assertEqual(output_name, "inventory.json")
        self.assertEqual(payload["metadata"]["record_count"], 2)
        self.assertEqual(payload["metadata"]["unique_product_ids"], 2)
        self.assertEqual(payload["metadata"]["detail_totals"]["stock_quantity"], 60)
        self.assertEqual(payload["metadata"]["detail_totals"]["stock_amount_at_average_cost"], 330000)
        self.assertEqual(payload["metadata"]["blank_stock_quantity_row_count"], 1)
        blank_row = next(row for row in payload["records"] if row["product_id"] == 1002)
        self.assertEqual(blank_row["stock_quantity"], 0)


if __name__ == "__main__":
    unittest.main()
