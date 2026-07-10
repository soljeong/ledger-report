from __future__ import annotations

import copy
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from openpyxl import load_workbook

from generate_analysis_report_html_core import build_report_data, load_sources
from generate_analysis_result_excel import SHEET_NAMES, export_workbook, generate_report


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class GenerateAnalysisResultExcelTests(unittest.TestCase):
    def _create_workbook(self, sources=None):
        tempdir = tempfile.TemporaryDirectory()
        output = Path(tempdir.name) / "analysis_result.xlsx"
        export_workbook(sources or load_sources(EXAMPLE_DIR), output)
        self.addCleanup(tempdir.cleanup)
        return output, load_workbook(output, data_only=False)

    def test_creates_reopenable_workbook_with_required_sheet_order_and_json_record_counts(self):
        output, workbook = self._create_workbook()

        self.assertTrue(output.exists())
        self.assertEqual(workbook.sheetnames, SHEET_NAMES)
        sources = load_sources(EXAMPLE_DIR)
        for sheet_name, source_name in (("매입", "purchase"), ("매출", "sales"), ("재고", "inventory")):
            self.assertEqual(workbook[sheet_name].max_row - 1, len(sources[source_name]["records"]))
            self.assertEqual(workbook[sheet_name].freeze_panes, "A2")
            self.assertIsNotNone(workbook[sheet_name].auto_filter.ref)

    def test_kpis_come_from_common_report_data_and_warning_status_is_explicit(self):
        output, workbook = self._create_workbook()
        report_data = build_report_data(load_sources(EXAMPLE_DIR))
        summary_values = {
            row[0].value: row[1].value
            for row in workbook["요약"].iter_rows(min_row=2, max_col=3)
        }

        self.assertEqual(summary_values["분석 결과 상태"], "completed_with_warnings")
        self.assertEqual(summary_values["매출 공급가액"], report_data["kpis"]["sales_supply_amount"])
        self.assertEqual(summary_values["매출원가"], report_data["kpis"]["fifo_sales_cost_amount"])
        self.assertEqual(summary_values["매출총이익"], report_data["kpis"]["gross_profit"])
        self.assertIsNone(summary_values["매출총이익률"])
        self.assertEqual(workbook["손익분석"]["A1"].value, "손익 요약")
        self.assertTrue(output.exists())

    def test_writes_no_formulas_or_external_links_and_uses_native_internal_charts(self):
        sources = copy.deepcopy(load_sources(EXAMPLE_DIR))
        sources["sales"]["records"][0]["item_name"] = "=not_a_formula"
        output, workbook = self._create_workbook(sources)

        self.assertFalse(
            any(
                cell.data_type == "f"
                for worksheet in workbook.worksheets
                for row in worksheet.iter_rows()
                for cell in row
            )
        )
        injected = workbook["매출"]["E2"]
        self.assertEqual(injected.value, "=not_a_formula")
        self.assertEqual(injected.data_type, "s")
        self.assertGreaterEqual(sum(len(worksheet._charts) for worksheet in workbook.worksheets), 1)
        self.assertEqual(workbook._external_links, [])

        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            chart_files = [name for name in names if name.startswith("xl/charts/")]
            self.assertTrue(chart_files)
            self.assertFalse(any("externalLinks" in name for name in names))
            self.assertFalse(any("connections" in name for name in names))
            for chart_file in chart_files:
                chart_xml = archive.read(chart_file).decode("utf-8")
                self.assertIn("!$", chart_xml)
                self.assertNotIn("[", chart_xml)

    def test_missing_optional_metadata_generates_a_warning_without_inventing_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = Path(tmpdir) / "input"
            shutil.copytree(EXAMPLE_DIR, input_dir)
            (input_dir / "sales_voucher_metadata.json").unlink()
            output = Path(tmpdir) / "analysis_result.xlsx"

            generate_report(input_dir, output)
            workbook = load_workbook(output, data_only=False)
            execution_values = {
                row[0].value: row[1].value
                for row in workbook["실행정보"].iter_rows(min_row=2, max_col=2)
            }

            self.assertEqual(workbook["요약"]["B2"].value, "completed_with_warnings")
            self.assertEqual(workbook["전표메타"].max_row, 1)
            self.assertEqual(execution_values["제공되지 않은 선택 입력"], "sales_voucher_metadata.json")
            self.assertIn("sales_voucher_metadata_missing", execution_values["적용된 경고"])

    def test_missing_required_json_fails_clearly(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            input_dir = Path(tmpdir) / "input"
            shutil.copytree(EXAMPLE_DIR, input_dir)
            (input_dir / "sales.json").unlink()

            with self.assertRaisesRegex(FileNotFoundError, "required sales JSON is missing"):
                generate_report(input_dir, Path(tmpdir) / "analysis_result.xlsx")


if __name__ == "__main__":
    unittest.main()
