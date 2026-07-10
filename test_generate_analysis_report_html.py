from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from generate_analysis_report_html import (
    generate_report,
    load_report_spec,
    load_sources,
    principal_sales_cost_rows,
    render_report_html,
)


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class GenerateAnalysisReportHtmlTests(unittest.TestCase):
    def test_report_renders_fifo_metrics_dates_statuses_and_reconciliation(self):
        html = render_report_html(load_sources(EXAMPLE_DIR))

        self.assertEqual(html.count('class="report-page'), 4)
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


if __name__ == "__main__":
    unittest.main()
