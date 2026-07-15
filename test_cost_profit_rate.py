from __future__ import annotations

import unittest

import _generate_analysis_report_html_overview as overview


class CostBasedProfitRateTests(unittest.TestCase):
    def test_rate_uses_sales_cost_as_denominator(self):
        self.assertAlmostEqual(
            overview._cost_based_profit_rate(4500, 4960),
            90.7258064516129,
        )

    def test_zero_or_negative_sales_cost_has_no_interpretable_rate(self):
        self.assertIsNone(overview._cost_based_profit_rate(100, 0))
        self.assertIsNone(overview._cost_based_profit_rate(100, -1))

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
