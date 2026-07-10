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
        self.assertIn("4,500", html)
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
            "잠정 부가세 정산금",
            "현금 관점의 참고값",
            "validation_error",
            "잠정 매출총이익",
        ):
            self.assertIn(text, html)
        vat_start = html.index('class="report-page report-page--vat"')
        vat_end = html.index("</article>", vat_start)
        self.assertIn('id="vat-settlement-title"', html[vat_start:vat_end])
        self.assertNotIn("부가세 납부 예상액", html[vat_start:vat_end])

    def test_weekly_and_principal_margin_statuses_hide_non_final_rates(self):
        purchase_rows = [
            {"date": "2026-01-01", "quantity": 1, "unit_price": 100, "supply_amount": 100, "vat": 0, "total_amount": 100, "product_id": 1, "voucher": 1, "excel_row": 1},
        ]
        sales_rows = [
            {"date": "2026-01-02", "quantity": 1, "supply_amount": 100, "vat": 0, "total_amount": 101, "product_id": 1, "voucher": 2, "excel_row": 2},
        ]
        reconciliation = build_reconciliation({"records": purchase_rows}, {"records": sales_rows}, {"records": []}, "2026-01-01", "2026-01-31", "2026-01-31")
        weekly = weekly_purchase_sales_amounts(purchase_rows, sales_rows, [], reconciliation)
        self.assertEqual(weekly[0]["margin_status"], "error")
        self.assertIsNone(weekly[0]["margin_rate"])
        principal = principal_sales_cost_rows(sales_rows, purchase_rows, {"records": [{"voucher_key": "2026-01-02-2", "principal": "P"}]}, reconciliation)
        self.assertEqual(principal[0]["margin_status"], "error")
        self.assertIsNone(principal[0]["margin_rate"])

    def test_negative_amounts_replace_the_fixed_balance_placeholder(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        summary = sources["reconciliation"]["summary"]
        summary.update(gross_profit_status="confirmed", opening_stock_amount=0, period_purchase_cost_amount=-10, period_sales_supply_amount=10, sales_amount=10, inventory_amount_at_fifo=0, ending_fifo_inventory_amount=0, post_period_backfill_amount=0, prior_period_shortage_settlement_amount=0, gross_profit=0)
        html = render_report_html(sources)
        self.assertIn("금액 밸런스 차트는 확정값으로 표시하지 않는다", html)
        self.assertNotIn("AMOUNT_BALANCE_CHART", html)

    def test_invalid_date_record_does_not_abort_html_generation_or_period_tables(self):
        sources = deepcopy(load_sources(EXAMPLE_DIR))
        sources["sales"]["records"].append({"date": "2026/05/12", "voucher": 99, "excel_row": 99, "quantity": 1, "supply_amount": 999, "product_id": 99})
        html = render_report_html(sources)
        self.assertIn("거래 단위 오류 상세", html)
        self.assertNotIn("2026/05/12-99", html)


if __name__ == "__main__":
    unittest.main()
