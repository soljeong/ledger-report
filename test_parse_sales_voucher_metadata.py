#!/usr/bin/env python3
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from parse_sales_voucher_metadata import parse_metadata_workbook, write_json


def write_metadata_workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "매출_전표별"
    sheet.append(
        [
            "행유형",
            "전표키",
            "일자",
            "전표",
            "거래처",
            "행수",
            "수량",
            "단가",
            "공급금액",
            "부가세",
            "합계금액",
            "등록번호",
            "원청",
        ]
    )
    sheet.append(["전표", "2026-05-08-1", "2026-05-08", 1, "Sample Robotics", 2, 60, None, 420000, 42000, 462000, None, "Sample Robotics"])
    sheet.append(["전표", "2026-05-22-2", "2026-05-22", 2, "Demo Automation", 1, 15, None, 90000, 9000, 99000, None, "Demo Automation "])
    workbook.save(path)


class ParseSalesVoucherMetadataTests(unittest.TestCase):
    def test_parses_principal_metadata_by_sales_voucher(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = Path(tmpdir) / "sample_sales_voucher_groups_principal_input.xlsx"
            write_metadata_workbook(input_path)

            payload = parse_metadata_workbook(input_path)

        self.assertEqual(payload["metadata"]["source_file"], "sample_sales_voucher_groups_principal_input.xlsx")
        self.assertEqual(payload["metadata"]["record_count"], 2)
        self.assertEqual(payload["metadata"]["unique_principal_count"], 2)
        self.assertEqual(payload["metadata"]["total_amount"], 561000)

        first = payload["records"][0]
        self.assertEqual(first["voucher_key"], "2026-05-08-1")
        self.assertEqual(first["date"], "2026-05-08")
        self.assertEqual(first["voucher"], 1)
        self.assertEqual(first["principal"], "Sample Robotics")
        self.assertEqual(first["total_amount"], 462000)

        demo = next(row for row in payload["records"] if row["principal"] == "Demo Automation")
        self.assertEqual(demo["principal_raw"], "Demo Automation ")

    def test_writes_metadata_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            input_path = base / "sample_sales_voucher_groups_principal_input.xlsx"
            output_path = base / "metadata" / "metadata.json"
            write_metadata_workbook(input_path)
            payload = parse_metadata_workbook(input_path)

            write_json(output_path, payload)

            loaded = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["metadata"]["record_count"], 2)
            self.assertEqual(loaded["records"][1]["principal"], "Demo Automation")


if __name__ == "__main__":
    unittest.main()
