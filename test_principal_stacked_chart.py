from __future__ import annotations

import unittest

from generate_analysis_report_html import render_principal_sales_chart


class PrincipalStackedChartTests(unittest.TestCase):
    def test_renders_fifo_cost_and_margin_as_stacked_revenue_bar(self):
        html = render_principal_sales_chart(
            [
                {
                    "principal": "Demo Buyer C",
                    "sales_amount_exact": "4500",
                    "cost_amount_exact": "2980",
                },
                {
                    "principal": "Demo Buyer A",
                    "sales_amount_exact": "2000",
                    "cost_amount_exact": "840",
                },
            ]
        )

        self.assertIn('id="principal-stacked-sales-chart"', html)
        self.assertIn("FIFO 매출원가", html)
        self.assertIn("매출총이익", html)
        self.assertIn("Demo Buyer C", html)
        self.assertIn("4,500", html)
        self.assertIn("2,980", html)
        self.assertIn("1,520", html)
        self.assertIn("33.8%", html)
        self.assertIn('style="width:66.22%"', html)
        self.assertIn('style="width:33.78%"', html)

    def test_omits_diagnostic_status_language(self):
        html = render_principal_sales_chart(
            [
                {
                    "principal": "Demo Buyer",
                    "sales_amount_exact": "100",
                    "cost_amount_exact": "150",
                }
            ]
        )

        self.assertIn("-50.0%", html)
        self.assertIn("principal-stack-chart__bar--negative", html)
        self.assertNotIn("잠정", html)
        self.assertNotIn("오류", html)
        self.assertNotIn("상태", html)


if __name__ == "__main__":
    unittest.main()
