from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

import pandas as pd

from analyze_inventory import build_reconciliation
from generate_analysis_report_html import (
    generate_report,
    load_report_spec,
    load_sources,
    principal_sales_cost_rows,
    render_report_html,
    weekly_inventory_flow_rows,
)
from generate_analysis_report_html_core import weekly_purchase_sales_amounts
from src.charts import weekly_purchase_sales_figure


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class GenerateAnalysisReportHtmlTests(unittest.TestCase):
    def test_report_focuses_on_analysis_and_omits_diagnostics(self):
        html = render_report_html(load_sources(EXAMPLE_DIR))
        overview = html.split('<article class="report-page report-page--profit">', 1)[0]

        self.assertEqual(html.count('class="report-page'), 5)
        for text in (
            "2026-05-10 ~ 2026-05-20",
            "2026-05-25",
            "매출 공급가액",
            "매출원가",
            "매출총이익",
            "원가대비 이익률",
            "기간 손익 흐름",
            "주간 재고금액 흐름",
            "원청별 매출과 원가",
            "주요 품목 손익·재고",
            "매입처별 기간 매입 TOP 8",
            "9,460원",
            "4,960원",
            "4,500원",
            "90.7%",
            "1,510원",
            "Demo Buyer A",
        ):
            self.assertIn(text, html)
        for text in (
            "판정",
            "오류",
            "경고",
            "검증",
            "상태",
            "잠정",
            "문제 품목 상세",
            "거래 단위 오류 상세",
            "FIFO 원가 상태",
            "재고 기준일 수량 대사",
            "component_mismatch",
            "validation_error",
            "매출총이익률",
        ):
            self.assertNotIn(text, html)
        self.assertNotIn("평균원가", html)
        self.assertNotIn("최종매입가", html)
        self.assertNotIn('id="amount-balance-chart"', html)
        self.assertNotIn("거래처별 매출 TOP 8", html)
        self.assertNotIn("품목별 매출 TOP 8", html)
        self.assertNotIn("기말 장부재고수량", overview)
        self.assertIn("기초 재고금액", overview)
        expected_metrics = (
            "매출 공급가액",
            "매출원가",
            "매출총이익",
            "원가대비 이익률",
            "기초 재고금액",
            "기말 재고금액",
        )
        positions = [overview.index(label) for label in expected_metrics]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(
            html.replace("FIFO Widget", "").count("FIFO"),
            1,
        )
        self.assertIn("기말 재고금액은 분석 종료일의 FIFO 잔여 원가층 합계입니다.", html)

    def test_report_uses_fifo_allocations_for_principal_costs(self):
        sources = load_sources(EXAMPLE_DIR)
        html = render_report_html(sources)
        principal_section = html.split('id="principal-title"', 1)[1]
        fifo_total = sources["reconciliation"]["summary"]["fifo_sales_cost_amount"]

        self.assertIn("매출원가", principal_section)
        self.assertIn("Demo Buyer A", principal_section)
        rows = principal_sales_cost_rows(
            sources["sales"]["records"],
            sources["purchase"]["records"],
            sources["sales_voucher_metadata"],
            sources["reconciliation"],
        )
        self.assertEqual(sum(row["cost_amount"] for row in rows), fifo_total)
        self.assertGreater(fifo_total, 0)

    def test_report_derives_cost_profit_rate_from_displayed_amounts(self):
        sources = load_sources(EXAMPLE_DIR)
        summary = sources["reconciliation"]["summary"]
        self.assertEqual(summary["gross_profit_status"], "error")

        html = render_report_html(sources)
        overview = html.split('<article class="report-page report-page--profit">', 1)[0]

        self.assertIn("90.7%", overview)
        self.assertNotIn("gross_profit_status", html)
        self.assertNotIn(">error<", html)

    def test_period_views_exclude_outside_records(self):
        html = render_report_html(load_sources(EXAMPLE_DIR))

        self.assertNotIn("2026-05-24", html)
        self.assertNotIn("Demo Buyer G", html)

    def test_fifo_amount_balance_identity_is_preserved_in_analysis_data(self):
        purchases = {
            "records": [
                {
                    "date": "2025-12-30",
                    "quantity": 10,
                    "unit_price": 100,
                    "total_amount": 1000,
                    "product_id": 1,
                    "voucher": 1,
                    "excel_row": 1,
                    "item_name": "Item",
                    "specification": "A",
                }
            ]
        }
        sales = {
            "records": [
                {
                    "date": "2026-01-10",
                    "quantity": 4,
                    "total_amount": 800,
                    "product_id": 1,
                    "voucher": 1,
                    "excel_row": 1,
                    "item_name": "Item",
                    "specification": "A",
                }
            ]
        }
        reconciliation = build_reconciliation(
            purchases,
            sales,
            {"records": [{"product_id": 1, "stock_quantity": 6}]},
            "2026-01-01",
            "2026-01-31",
            "2026-01-31",
        )
        summary = reconciliation["summary"]
        self.assertEqual(
            summary["sales_amount"] + summary["ending_fifo_inventory_amount"],
            summary["opening_stock_amount"]
            + summary["period_purchase_cost_amount"]
            + summary["backfilled_amount"]
            + summary["gross_profit"],
        )

    def test_report_spec_controls_chart_text_and_plotlyjs_mode(self):
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")
        spec["plotly"]["include_plotlyjs"] = "cdn"
        spec["charts"]["weekly_purchase_sales"]["title"] = "주간 테스트 제목"
        spec["charts"]["weekly_purchase_sales"]["x_axis_title"] = "테스트 X축"
        spec["charts"]["weekly_purchase_sales"]["y_axis_title"] = "테스트 Y축"
        spec["charts"]["weekly_purchase_sales"]["unit_label"] = "테스트 단위"

        sources = load_sources(EXAMPLE_DIR)
        html = render_report_html(sources, spec)
        figure = weekly_purchase_sales_figure(
            pd.DataFrame(
                weekly_purchase_sales_amounts(
                    sources["purchase"]["records"],
                    sources["sales"]["records"],
                    sources["inventory"]["records"],
                    sources["reconciliation"],
                )
            ),
            spec["charts"]["weekly_purchase_sales"],
        )

        self.assertIn("주간 테스트 제목", html)
        self.assertIn("표시 단위: 테스트 단위", html)
        self.assertIn('src="https://cdn.plot.ly', html)
        self.assertEqual(figure.layout.xaxis.title.text, "테스트 X축")
        self.assertEqual(figure.layout.yaxis.title.text, "테스트 Y축")

    def test_writes_report_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            generate_report(EXAMPLE_DIR, output_path)
            html = output_path.read_text(encoding="utf-8")

        self.assertIn("주요 품목 손익·재고", html)
        self.assertIn("매출원가", html)

    def test_vat_is_shown_as_neutral_amount_comparison(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        sources["reconciliation"]["summary"].update(
            vat_settlement_amount=-10,
            vat_settlement_status="validation_error",
            post_vat_reference_status="error",
        )

        html = render_report_html(sources)

        self.assertIn("부가세 금액 비교", html)
        self.assertIn("부가세 차액", html)
        self.assertIn("-10원", html)
        self.assertNotIn("환급 예상액", html)
        self.assertNotIn("납부 예상액", html)
        self.assertNotIn("정산 상태", html)

    def test_invalid_date_record_does_not_abort_or_appear_in_report(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        sources["sales"]["records"].append(
            {
                "date": "2026/05/12",
                "voucher": 99,
                "excel_row": 99,
                "quantity": 1,
                "supply_amount": 999,
                "product_id": 99,
                "company": "Bad Date Buyer",
                "item_name": "Bad Date Item",
            }
        )

        html = render_report_html(sources)

        self.assertIn("기간 손익 흐름", html)
        self.assertNotIn("2026/05/12", html)
        self.assertNotIn("Bad Date Buyer", html)

    def test_invalid_sales_quantity_is_excluded_from_quantity_totals_but_report_renders(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        baseline_weekly = weekly_purchase_sales_amounts(
            sources["purchase"]["records"],
            sources["sales"]["records"],
            [],
            sources["reconciliation"],
        )
        baseline_principal = principal_sales_cost_rows(
            sources["sales"]["records"],
            sources["purchase"]["records"],
            sources["sales_voucher_metadata"],
            sources["reconciliation"],
        )
        sources["sales"]["records"].append(
            {
                "date": "2026-05-12",
                "voucher": 999,
                "excel_row": 999,
                "quantity": "not-a-number",
                "supply_amount": 777,
                "vat": 0,
                "total_amount": 777,
                "product_id": 1001,
                "item_name": "Bad Quantity",
                "specification": "Test",
                "company": "Demo Buyer A",
            }
        )
        sources["reconciliation"] = build_reconciliation(
            sources["purchase"],
            sources["sales"],
            sources["inventory"],
            "2026-05-10",
            "2026-05-20",
            "2026-05-25",
        )

        html = render_report_html(sources)
        weekly = weekly_purchase_sales_amounts(
            sources["purchase"]["records"],
            sources["sales"]["records"],
            [],
            sources["reconciliation"],
        )
        principal = principal_sales_cost_rows(
            sources["sales"]["records"],
            sources["purchase"]["records"],
            sources["sales_voucher_metadata"],
            sources["reconciliation"],
        )

        self.assertIn("기간 손익 흐름", html)
        self.assertEqual(
            sum(row["quantity"] for row in weekly),
            sum(row["quantity"] for row in baseline_weekly),
        )
        self.assertEqual(
            sum(row["quantity"] for row in principal),
            sum(row["quantity"] for row in baseline_principal),
        )
        self.assertEqual(
            sum(row["quantity"] for row in weekly),
            sources["reconciliation"]["summary"]["period_sales_quantity"],
        )

    def test_valid_supply_amount_survives_fifo_and_quantity_errors_in_all_revenue_views(self):
        sales = [
            {
                "date": "2026-01-10",
                "voucher": 1,
                "excel_row": 1,
                "quantity": 2,
                "supply_amount": 100,
                "vat": 0,
                "total_amount": 100,
                "product_id": None,
            },
            {
                "date": "2026-01-11",
                "voucher": 2,
                "excel_row": 2,
                "quantity": "not-a-number",
                "supply_amount": 200,
                "vat": 0,
                "total_amount": 200,
                "product_id": 1,
            },
        ]
        reconciliation = build_reconciliation(
            {"records": []},
            {"records": sales},
            {"records": []},
            "2026-01-01",
            "2026-01-31",
            "2026-01-31",
        )
        weekly = weekly_purchase_sales_amounts([], sales, [], reconciliation)
        principal = principal_sales_cost_rows(sales, [], {"records": []}, reconciliation)
        flow = weekly_inventory_flow_rows([], sales, [], reconciliation)

        self.assertEqual(reconciliation["summary"]["period_sales_supply_amount"], 300)
        self.assertEqual(sum(row["sales_amount"] for row in weekly), 300)
        self.assertEqual(sum(row["sales_amount"] for row in principal), 300)
        self.assertEqual(sum(row["sales_amount"] for row in flow), 300)
        self.assertEqual(sum(row["quantity"] for row in weekly), 0)
        self.assertEqual(sum(row["quantity"] for row in principal), 0)

    def test_missing_empty_and_invalid_quantity_are_not_reported_as_zero_quantity_rows(self):
        sales = [
            {"date": "2026-01-10", "voucher": 1, "excel_row": 1, "quantity": None, "supply_amount": 10, "vat": 0, "total_amount": 10, "product_id": 1},
            {"date": "2026-01-10", "voucher": 2, "excel_row": 2, "quantity": "", "supply_amount": 10, "vat": 0, "total_amount": 10, "product_id": 1},
            {"date": "2026-01-10", "voucher": 3, "excel_row": 3, "quantity": "not-a-number", "supply_amount": 10, "vat": 0, "total_amount": 10, "product_id": 1},
        ]
        reconciliation = build_reconciliation(
            {"records": []},
            {"records": sales},
            {"records": []},
            "2026-01-01",
            "2026-01-31",
            "2026-01-31",
        )
        weekly = weekly_purchase_sales_amounts([], sales, [], reconciliation)
        principal = principal_sales_cost_rows(sales, [], {"records": []}, reconciliation)

        self.assertEqual(reconciliation["summary"]["quantity_validation_error_count"], 3)
        self.assertTrue(
            all(
                not state["quantity_eligible"]
                for state in reconciliation["transaction_validations"]
            )
        )
        self.assertEqual(sum(row["quantity"] for row in weekly), 0)
        self.assertEqual(sum(row["quantity"] for row in principal), 0)
        self.assertEqual(sum(row["sales_amount"] for row in weekly), 30)

    def test_invalid_supply_amount_never_uses_total_as_revenue_fallback(self):
        sales = [
            {
                "date": "2026-01-10",
                "voucher": 1,
                "excel_row": 1,
                "quantity": 1,
                "supply_amount": None,
                "vat": 10,
                "total_amount": 110,
                "product_id": 1,
            }
        ]
        reconciliation = build_reconciliation(
            {"records": []},
            {"records": sales},
            {"records": []},
            "2026-01-01",
            "2026-01-31",
            "2026-01-31",
        )
        weekly = weekly_purchase_sales_amounts([], sales, [], reconciliation)
        principal = principal_sales_cost_rows(sales, [], {"records": []}, reconciliation)
        flow = weekly_inventory_flow_rows([], sales, [], reconciliation)
        legacy_reconciliation = {
            "metadata": {"period_start": "2026-01-01", "period_end": "2026-01-31"}
        }
        legacy_weekly = weekly_purchase_sales_amounts([], sales, [], legacy_reconciliation)
        legacy_principal = principal_sales_cost_rows(
            sales, [], {"records": []}, legacy_reconciliation
        )
        legacy_flow = weekly_inventory_flow_rows([], sales, [], legacy_reconciliation)

        self.assertEqual(reconciliation["summary"]["period_sales_supply_amount"], 0)
        self.assertEqual(sum(row["sales_amount"] for row in weekly), 0)
        self.assertEqual(sum(row["sales_amount"] for row in principal), 0)
        self.assertEqual(sum(row["sales_amount"] for row in flow), 0)
        self.assertEqual(sum(row["sales_amount"] for row in legacy_weekly), 0)
        self.assertEqual(sum(row["sales_amount"] for row in legacy_principal), 0)
        self.assertEqual(sum(row["sales_amount"] for row in legacy_flow), 0)

    def test_negative_amounts_do_not_add_a_diagnostic_balance_panel(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        sources["reconciliation"]["summary"].update(
            period_sales_supply_amount=-1,
            sales_amount=-1,
            period_purchase_cost_amount=-10,
        )

        html = render_report_html(sources)

        self.assertIn("기간 손익 흐름", html)
        self.assertNotIn('id="amount-balance-chart"', html)
        self.assertNotIn("금액 밸런스", html)


if __name__ == "__main__":
    unittest.main()
