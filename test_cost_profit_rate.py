from __future__ import annotations

import unittest

import _generate_analysis_report_html_overview as overview


class CostBasedProfitRateTests(unittest.TestCase):
    def test_rate_uses_sales_cost_as_denominator(self):
        self.assertAlmostEqual(
            overview._cost_based_profit_rate(4500, 4960),
            90.7258064516129,
        )

    def test_zero_or_negative_sales_cost_is_rendered_as_dash(self):
        for cost in (0, -1):
            self.assertIsNone(overview._cost_based_profit_rate(100, cost))
            card = overview._replace_cost_profit_metric(
                """
                <div class="metric-card">
                  <span class="metric-label">매출총이익률</span>
                  <strong>50.0%</strong>
                  <span class="metric-note">표시 금액 기준</span>
                </div>
                """,
                {"gross_profit": 100, "fifo_sales_cost_amount": cost},
            )
            self.assertIn("원가대비 이익률", card)
            self.assertIn("<strong>-</strong>", card)

        rows = overview._render_weekly_cost_profit_rows(
            [
                {
                    "week_start": "2026-01-05",
                    "row_count": 1,
                    "quantity": 1,
                    "sales_amount_exact": 100,
                    "cost_amount_exact": 0,
                },
                {
                    "week_start": "2026-01-12",
                    "row_count": 1,
                    "quantity": 1,
                    "sales_amount_exact": 100,
                    "cost_amount_exact": -1,
                },
            ]
        )
        self.assertEqual(rows.count('<td class="num">-</td>'), 3)

    def test_weekly_total_uses_total_profit_over_total_cost(self):
        html = overview._render_weekly_cost_profit_rows(
            [
                {
                    "week_start": "2026-01-05",
                    "row_count": 1,
                    "quantity": 1,
                    "sales_amount_exact": 150,
                    "cost_amount_exact": 100,
                },
                {
                    "week_start": "2026-01-12",
                    "row_count": 1,
                    "quantity": 1,
                    "sales_amount_exact": 400,
                    "cost_amount_exact": 200,
                },
            ]
        )

        self.assertIn("50.0%", html)
        self.assertIn("100.0%", html)
        self.assertIn("83.3%", html)
        self.assertNotIn("75.0%", html)


if __name__ == "__main__":
    unittest.main()
