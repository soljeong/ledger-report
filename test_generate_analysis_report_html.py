from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from generate_analysis_report_html import (
    generate_report,
    load_report_spec,
    load_sources,
    principal_sales_cost_rows,
    render_report_html,
    weekly_inventory_flow_rows,
)
from generate_analysis_report_html_core import weekly_purchase_sales_amounts
from analyze_inventory import build_reconciliation


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class GenerateAnalysisReportHtmlTests(unittest.TestCase):
    def test_report_renders_fifo_metrics_dates_statuses_and_reconciliation(self):
        html = render_report_html(load_sources(EXAMPLE_DIR))

        self.assertEqual(html.count('class="report-page'), 5)
        for text in (
            "2026-05-10 ~ 2026-05-20",
            "2026-05-25",
            "2026-05-24",
            "2026-05-22",
            "FIFO 매출원가",
            "FIFO 재고금액",
            "매출총이익",
            "FIFO 원가 상태",
            "장부수량 계산 상태",
            "재고 스냅샷 검증 상태",
            "수량 대사 상태",
            "금액 구성 검증 상태",
            "실제 수량 차이 품목",
            "전체 수량 대사 검증 상태",
            "confirmed",
            "backfilled",
            "unconfirmed",
            "재고 기준일 수량 대사",
            "inventory_more",
            "원청별 매출",
            "미확정 수량",
        ):
            self.assertIn(text, html)
        self.assertIn("4,960", html)
        self.assertIn("1,510", html)
        amount_section = html.split('id="amount-title"', 1)[1].split('</section>', 1)[0]
        self.assertIn("잠정 매출총이익", amount_section)
        self.assertIn("4,500", amount_section)
        self.assertNotIn("평균원가", html)
        self.assertNotIn("최종매입가", html)
        self.assertNotIn("purchase_unit_costs", html)

    def test_report_uses_fifo_allocations_for_principal_costs(self):
        sources = load_sources(EXAMPLE_DIR)
        html = render_report_html(sources)
        principal_section = html.split('id="principal-title"', 1)[1]
        fifo_total = sources["reconciliation"]["summary"]["fifo_sales_cost_amount"]
        self.assertIn("매출원가", principal_section)
        self.assertIn("Demo Buyer A", principal_section)
        self.assertNotIn("현재 평균원가", principal_section)
        rows = principal_sales_cost_rows(
            sources["sales"]["records"],
            sources["purchase"]["records"],
            sources["sales_voucher_metadata"],
            sources["reconciliation"],
        )
        self.assertEqual(sum(row["cost_amount"] for row in rows), fifo_total)
        self.assertGreater(fifo_total, 0)

    def test_report_renders_missing_backfill_date_as_none(self):
        sources = load_sources(EXAMPLE_DIR)
        sources["reconciliation"]["metadata"]["backfill_last_purchase_date"] = None

        html = render_report_html(sources)

        self.assertIn("후속 원가보충 최종 매입일", html)
        self.assertIn(">없음</strong>", html)

    def test_period_charts_exclude_outside_sales_and_problem_table_identifies_rows(self):
        purchases = {"records": [{"date": "2026-01-01", "quantity": 10, "unit_price": 100, "supply_amount": 1000, "vat": 0, "total_amount": 1000, "product_id": 1, "voucher": 1, "excel_row": 1, "item_name": "Item", "specification": "A"}]}
        sales = {"records": [
            {"date": "2026-01-10", "quantity": 2, "supply_amount": 400, "vat": 0, "total_amount": 400, "product_id": 1, "voucher": 1, "excel_row": 1, "item_name": "Item", "specification": "A"},
            {"date": "2026-02-10", "quantity": 3, "supply_amount": 600, "vat": 0, "total_amount": 600, "product_id": 1, "voucher": 2, "excel_row": 2, "item_name": "Item", "specification": "A"},
        ]}
        reconciliation = build_reconciliation(purchases, sales, {"records": []}, "2026-01-01", "2026-01-31", "2026-02-28")
        weekly = weekly_purchase_sales_amounts(purchases["records"], sales["records"], [], reconciliation)
        self.assertEqual(sum(row["sales_amount"] for row in weekly), 400)
        self.assertEqual(sum(row["quantity"] for row in weekly), 2)
        sources = load_sources(EXAMPLE_DIR)
        self.assertIn("문제 품목 상세", render_report_html(sources))

    def test_fifo_amount_balance_identity_is_exposed_without_error(self):
        purchases = {"records": [{"date": "2025-12-30", "quantity": 10, "unit_price": 100, "total_amount": 1000, "product_id": 1, "voucher": 1, "excel_row": 1, "item_name": "Item", "specification": "A"}]}
        sales = {"records": [{"date": "2026-01-10", "quantity": 4, "total_amount": 800, "product_id": 1, "voucher": 1, "excel_row": 1, "item_name": "Item", "specification": "A"}]}
        reconciliation = build_reconciliation(purchases, sales, {"records": [{"product_id": 1, "stock_quantity": 6}]}, "2026-01-01", "2026-01-31", "2026-01-31")
        summary = reconciliation["summary"]
        self.assertEqual(summary["sales_amount"] + summary["ending_fifo_inventory_amount"], summary["opening_stock_amount"] + summary["period_purchase_cost_amount"] + summary["backfilled_amount"] + summary["gross_profit"])

    def test_report_spec_controls_chart_text_and_plotlyjs_mode(self):
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")
        spec["plotly"]["include_plotlyjs"] = "cdn"
        spec["charts"]["weekly_purchase_sales"]["title"] = "주간 테스트 제목"
        spec["charts"]["weekly_purchase_sales"]["x_axis_title"] = "테스트 X축"
        spec["charts"]["weekly_purchase_sales"]["y_axis_title"] = "테스트 Y축"
        spec["charts"]["weekly_purchase_sales"]["unit_label"] = "테스트 단위"
        html = render_report_html(load_sources(EXAMPLE_DIR), spec)
        self.assertIn("주간 테스트 제목", html)
        self.assertIn("테스트 X축", html)
        self.assertIn("테스트 Y축", html)
        self.assertIn("표시 단위: 테스트 단위", html)
        self.assertIn('src="https://cdn.plot.ly', html)

    def test_writes_report_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            generate_report(EXAMPLE_DIR, output_path)
            html = output_path.read_text(encoding="utf-8")
            self.assertIn("FIFO 재고금액", html)

    def test_report_separates_vat_and_marks_invalid_amounts_as_non_final(self):
        html = render_report_html(load_sources(EXAMPLE_DIR))
        for text in (
            "부가세 정산",
            "매출 공급가액",
            "매입 부가세",
            "잠정 부가세 납부 예상액",
            "현금 관점의 참고값",
            "validation_error",
            "잠정 매출총이익",
        ):
            self.assertIn(text, html)
        vat_start = html.index('class="report-page report-page--vat"')
        vat_end = html.index("</article>", vat_start)
        self.assertIn('id="vat-settlement-title"', html[vat_start:vat_end])
        self.assertIn("잠정 부가세 납부 예상액", html[vat_start:vat_end])

    def test_demo_amounts_preserve_component_mismatch_and_fifo_unconfirmed_state(self):
        sources = load_sources(EXAMPLE_DIR)
        summary = sources["reconciliation"]["summary"]
        self.assertEqual(
            (
                summary["period_sales_supply_amount"],
                summary["period_sales_vat_amount"],
                summary["period_sales_total_amount"],
                summary["fifo_sales_cost_amount"],
                summary["gross_profit"],
                summary["vat_settlement_amount"],
                summary["post_vat_reference_amount"],
            ),
            (9460, 160, 9630, 4960, 4500, 106, 4394),
        )
        product_1007 = next(row for row in sources["reconciliation"]["rows"] if row["product_id"] == 1007)
        self.assertEqual((product_1007["cost_status"], product_1007["amount_validation_status"], product_1007["period_unconfirmed_quantity"], product_1007["unconfirmed_sales_supply_amount"]), ("unconfirmed", "component_mismatch", 5, 500))
        weekly = weekly_purchase_sales_amounts(sources["purchase"]["records"], sources["sales"]["records"], [], sources["reconciliation"])
        principal = principal_sales_cost_rows(sources["sales"]["records"], sources["purchase"]["records"], sources["sales_voucher_metadata"], sources["reconciliation"])
        self.assertEqual(sum(row["sales_amount"] for row in weekly), 9460)
        self.assertEqual(sum(row["sales_amount"] for row in principal), 9460)
        self.assertEqual(sum(row["cost_amount"] for row in weekly), 4960)
        self.assertEqual(sum(row["cost_amount"] for row in principal), 4960)
        html = render_report_html(sources)
        self.assertIn("금액 구성 검증 상태", html)
        self.assertIn("component_mismatch", html)
        self.assertIn("매출 부가세</th><td class=\"money\">160", html)
        self.assertIn("잠정 부가세 납부 예상액</th><td class=\"money\">106", html)

    def test_weekly_and_principal_margin_statuses_hide_non_final_rates(self):
        purchase_rows = [
            {"date": "2026-01-01", "quantity": 1, "unit_price": 100, "supply_amount": 100, "vat": 0, "total_amount": 100, "product_id": 1, "voucher": 1, "excel_row": 1},
        ]
        sales_rows = [
            {"date": "2026-01-02", "quantity": 1, "supply_amount": 100, "vat": 0, "total_amount": 101, "product_id": 1, "voucher": 2, "excel_row": 2},
        ]
        reconciliation = build_reconciliation({"records": purchase_rows}, {"records": sales_rows}, {"records": []}, "2026-01-01", "2026-01-31", "2026-01-31")
        weekly = weekly_purchase_sales_amounts(purchase_rows, sales_rows, [], reconciliation)
        self.assertEqual(weekly[0]["margin_status"], "confirmed")
        self.assertEqual(weekly[0]["amount_validation_status"], "component_mismatch")
        self.assertEqual(weekly[0]["margin_rate"], 0)
        principal = principal_sales_cost_rows(sales_rows, purchase_rows, {"records": [{"voucher_key": "2026-01-02-2", "principal": "P"}]}, reconciliation)
        self.assertEqual(principal[0]["margin_status"], "confirmed")
        self.assertEqual(principal[0]["amount_validation_status"], "component_mismatch")
        self.assertEqual(principal[0]["margin_rate"], 0)

    def test_negative_amounts_replace_the_fixed_balance_placeholder(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        summary = sources["reconciliation"]["summary"]
        summary.update(gross_profit_status="confirmed", opening_stock_amount=0, opening_stock_amount_exact="0", period_purchase_cost_amount=-10, period_purchase_cost_amount_exact="-10", period_sales_supply_amount=10, period_sales_supply_amount_exact="10", sales_amount=10, inventory_amount_at_fifo=0, ending_fifo_inventory_amount=0, ending_fifo_inventory_amount_exact="0", post_period_backfill_amount=0, period_backfilled_amount_exact="0", prior_period_shortage_settlement_amount=0, prior_period_shortage_settlement_amount_exact="0", gross_profit=0, gross_profit_exact="0")
        html = render_report_html(sources)
        self.assertIn("금액 밸런스 차트는 확정값으로 표시하지 않는다", html)
        self.assertNotIn("AMOUNT_BALANCE_CHART", html)

    def test_invalid_date_record_does_not_abort_html_generation_or_period_tables(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        sources["sales"]["records"].append({"date": "2026/05/12", "voucher": 99, "excel_row": 99, "quantity": 1, "supply_amount": 999, "product_id": 99})
        html = render_report_html(sources)
        self.assertIn("거래 단위 오류 상세", html)
        self.assertNotIn("2026/05/12-99", html)

    def test_invalid_sales_quantity_renders_and_is_excluded_from_quantity_totals(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        baseline_weekly = weekly_purchase_sales_amounts(
            sources["purchase"]["records"], sources["sales"]["records"], [], sources["reconciliation"]
        )
        baseline_principal = principal_sales_cost_rows(
            sources["sales"]["records"], sources["purchase"]["records"], sources["sales_voucher_metadata"], sources["reconciliation"]
        )
        sources["sales"]["records"].append(
            {
                "date": "2026-05-12", "voucher": 999, "excel_row": 999,
                "quantity": "not-a-number", "supply_amount": 777, "vat": 0,
                "total_amount": 777, "product_id": 1001, "item_name": "Bad Quantity", "specification": "Test",
            }
        )
        sources["reconciliation"] = build_reconciliation(
            sources["purchase"], sources["sales"], sources["inventory"], "2026-05-10", "2026-05-20", "2026-05-25"
        )

        html = render_report_html(sources)
        weekly = weekly_purchase_sales_amounts(
            sources["purchase"]["records"], sources["sales"]["records"], [], sources["reconciliation"]
        )
        principal = principal_sales_cost_rows(
            sources["sales"]["records"], sources["purchase"]["records"], sources["sales_voucher_metadata"], sources["reconciliation"]
        )

        self.assertIn("거래 단위 오류 상세", html)
        self.assertEqual(sum(row["quantity"] for row in weekly), sum(row["quantity"] for row in baseline_weekly))
        self.assertEqual(sum(row["quantity"] for row in principal), sum(row["quantity"] for row in baseline_principal))
        self.assertEqual(sum(row["quantity"] for row in weekly), sources["reconciliation"]["summary"]["period_sales_quantity"])

    def test_valid_supply_amount_survives_fifo_and_quantity_errors_in_all_revenue_views(self):
        sales = [
            {"date": "2026-01-10", "voucher": 1, "excel_row": 1, "quantity": 2, "supply_amount": 100, "vat": 0, "total_amount": 100, "product_id": None},
            {"date": "2026-01-11", "voucher": 2, "excel_row": 2, "quantity": "not-a-number", "supply_amount": 200, "vat": 0, "total_amount": 200, "product_id": 1},
        ]
        reconciliation = build_reconciliation({"records": []}, {"records": sales}, {"records": []}, "2026-01-01", "2026-01-31", "2026-01-31")
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
        reconciliation = build_reconciliation({"records": []}, {"records": sales}, {"records": []}, "2026-01-01", "2026-01-31", "2026-01-31")
        weekly = weekly_purchase_sales_amounts([], sales, [], reconciliation)
        principal = principal_sales_cost_rows(sales, [], {"records": []}, reconciliation)

        self.assertEqual(reconciliation["summary"]["quantity_validation_error_count"], 3)
        self.assertTrue(all(not state["quantity_eligible"] for state in reconciliation["transaction_validations"]))
        self.assertEqual(sum(row["quantity"] for row in weekly), 0)
        self.assertEqual(sum(row["quantity"] for row in principal), 0)
        self.assertEqual(sum(row["sales_amount"] for row in weekly), 30)

    def test_vat_validation_error_keeps_refund_and_payable_direction(self):
        for settlement, label in ((-10, "잠정 부가세 환급 예상액"), (10, "잠정 부가세 납부 예상액")):
            with self.subTest(settlement=settlement):
                sources = deepcopy(load_sources(EXAMPLE_DIR))
                sources["reconciliation"]["summary"].update(
                    vat_settlement_amount=settlement,
                    vat_settlement_status="validation_error",
                    post_vat_reference_status="error",
                )
                html = render_report_html(sources)
                vat_start = html.index('class="report-page report-page--vat"')
                vat_end = html.index("</article>", vat_start)
                vat_section = html[vat_start:vat_end]

                self.assertIn(f"{label}</th><td class=\"money\">10", vat_section)
                self.assertIn("검증 필요", vat_section)

    def test_invalid_supply_amount_never_uses_total_as_revenue_fallback(self):
        sales = [
            {"date": "2026-01-10", "voucher": 1, "excel_row": 1, "quantity": 1, "supply_amount": None, "vat": 10, "total_amount": 110, "product_id": 1},
        ]
        reconciliation = build_reconciliation({"records": []}, {"records": sales}, {"records": []}, "2026-01-01", "2026-01-31", "2026-01-31")
        weekly = weekly_purchase_sales_amounts([], sales, [], reconciliation)
        principal = principal_sales_cost_rows(sales, [], {"records": []}, reconciliation)
        flow = weekly_inventory_flow_rows([], sales, [], reconciliation)
        legacy_reconciliation = {"metadata": {"period_start": "2026-01-01", "period_end": "2026-01-31"}}
        legacy_weekly = weekly_purchase_sales_amounts([], sales, [], legacy_reconciliation)
        legacy_principal = principal_sales_cost_rows(sales, [], {"records": []}, legacy_reconciliation)
        legacy_flow = weekly_inventory_flow_rows([], sales, [], legacy_reconciliation)

        self.assertEqual(reconciliation["summary"]["period_sales_supply_amount"], 0)
        self.assertEqual(sum(row["sales_amount"] for row in weekly), 0)
        self.assertEqual(sum(row["sales_amount"] for row in principal), 0)
        self.assertEqual(sum(row["sales_amount"] for row in flow), 0)
        self.assertEqual(sum(row["sales_amount"] for row in legacy_weekly), 0)
        self.assertEqual(sum(row["sales_amount"] for row in legacy_principal), 0)
        self.assertEqual(sum(row["sales_amount"] for row in legacy_flow), 0)


if __name__ == "__main__":
    unittest.main()
