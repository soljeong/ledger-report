from __future__ import annotations

import copy
import re
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

    @staticmethod
    def _labeled_row(workbook, sheet_name: str, label: str):
        for row in workbook[sheet_name].iter_rows(min_col=1, max_col=3):
            if row[0].value == label:
                return row
        raise AssertionError(f"{label!r} was not found in {sheet_name}")

    @staticmethod
    def _header_column(workbook, sheet_name: str, header: str):
        for row in workbook[sheet_name].iter_rows():
            for cell in row:
                if cell.value == header:
                    return cell.row, cell.column
        raise AssertionError(f"{header!r} header was not found in {sheet_name}")

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

    def test_rate_cells_keep_their_distinct_ratio_and_percentage_point_contracts(self):
        sources = copy.deepcopy(load_sources(EXAMPLE_DIR))
        sources["reconciliation"]["summary"]["gross_profit_status"] = "confirmed"
        output, workbook = self._create_workbook(sources)

        summary_rate = self._labeled_row(workbook, "요약", "매출총이익률")[1]
        profit_rate = self._labeled_row(workbook, "손익분석", "매출총이익률")[1]
        self.assertAlmostEqual(summary_rate.value, 4500 / 9460)
        self.assertEqual(summary_rate.number_format, "0.0%")
        self.assertEqual(profit_rate.number_format, "0.0%")

        weekly_header_row, weekly_rate_column = self._header_column(workbook, "손익분석", "margin_rate")
        weekly_rate = workbook["손익분석"].cell(weekly_header_row + 1, weekly_rate_column)
        self.assertAlmostEqual(weekly_rate.value, 50.285714285714285)
        self.assertEqual(weekly_rate.number_format, "0.0\\%")

        principal_header_row, principal_rate_column = self._header_column(workbook, "매출분석", "margin_rate")
        principal_rate = workbook["매출분석"].cell(principal_header_row + 1, principal_rate_column)
        self.assertAlmostEqual(principal_rate.value, 33.77777777777778)
        self.assertEqual(principal_rate.number_format, "0.0\\%")

        inventory_header_row, inventory_rate_column = self._header_column(workbook, "재고분석", "gross_profit_rate")
        inventory_rate = workbook["재고분석"].cell(inventory_header_row + 1, inventory_rate_column)
        self.assertAlmostEqual(inventory_rate.value, 53.714285714285715)
        self.assertEqual(inventory_rate.number_format, "0.0\\%")
        self.assertTrue(output.exists())

    def test_charts_include_only_the_requested_non_contiguous_series(self):
        output, workbook = self._create_workbook()
        first_chart = workbook["요약"]._charts[0]
        flow_header_row, purchase_column = self._header_column(workbook, "재고분석", "purchase_increase")
        sales_column = next(
            cell.column
            for cell in workbook["재고분석"][flow_header_row]
            if cell.value == "sales_amount"
        )
        purchase_coordinate = workbook["재고분석"].cell(flow_header_row, purchase_column).coordinate
        sales_coordinate = workbook["재고분석"].cell(flow_header_row, sales_column).coordinate

        self.assertEqual(len(first_chart.series), 2)
        title_formulas = [series.tx.strRef.f for series in first_chart.series]
        value_formulas = [series.val.numRef.f for series in first_chart.series]
        self.assertEqual([formula.rsplit("!", 1)[1] for formula in title_formulas], [purchase_coordinate, sales_coordinate])
        self.assertEqual(
            [re.search(r"!\$([A-Z]+)\$", formula).group(1) for formula in value_formulas],
            [getattr(workbook["재고분석"].cell(flow_header_row, column), "column_letter") for column in (purchase_column, sales_column)],
        )
        self.assertTrue(output.exists())

    def test_profit_sheet_matches_html_vat_settlement_values_and_statuses(self):
        output, workbook = self._create_workbook()
        report_summary = build_report_data(load_sources(EXAMPLE_DIR))["summary"]
        vat_row = self._labeled_row(workbook, "손익분석", "잠정 부가세 납부 예상액")
        post_vat_row = self._labeled_row(workbook, "손익분석", "검증 필요")

        self.assertEqual(vat_row[1].value, abs(report_summary["vat_settlement_amount"]))
        self.assertEqual(vat_row[2].value, report_summary["vat_settlement_status"])
        self.assertEqual(post_vat_row[1].value, report_summary["post_vat_reference_amount"])
        self.assertEqual(post_vat_row[2].value, report_summary["post_vat_reference_status"])
        self.assertEqual(self._labeled_row(workbook, "손익분석", "매출 합계금액")[1].value, report_summary["period_sales_total_amount"])
        self.assertEqual(self._labeled_row(workbook, "손익분석", "매입 합계금액")[1].value, report_summary["period_purchase_total_amount"])
        self.assertTrue(output.exists())

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
