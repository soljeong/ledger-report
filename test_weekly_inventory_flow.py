from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from generate_analysis_report_html import (
    load_report_spec,
    load_sources,
    render_report_html,
    weekly_inventory_flow_rows,
)
from src.charts import weekly_inventory_flow_figure


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class WeeklyInventoryFlowTests(unittest.TestCase):
    def test_rows_reconcile_to_current_inventory(self) -> None:
        sources = load_sources(EXAMPLE_DIR)

        rows = weekly_inventory_flow_rows(
            sources["purchase"]["records"],
            sources["sales"]["records"],
            sources["inventory"]["records"],
            sources["reconciliation"],
        )

        self.assertEqual(rows[0]["week_start"], "2026-05-04")
        self.assertEqual(rows[0]["purchase_increase"], 2920)
        self.assertEqual(rows[0]["sales_amount"], 3500)
        self.assertEqual(rows[0]["outbound_cost_estimate"], 1740)
        self.assertEqual(rows[0]["estimated_inventory_amount"], 1780)
        self.assertEqual(rows[-1]["week_start"], "2026-05-18")
        self.assertEqual(rows[-1]["estimated_inventory_amount"], -570)

    def test_chart_uses_signed_bars_and_secondary_axis(self) -> None:
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")["charts"]["weekly_inventory_flow"]
        frame = pd.DataFrame(
            [
                {
                    "label": "2026-04-27",
                    "purchase_increase": 2_000_000,
                    "sales_amount": 800_000,
                    "outbound_cost_estimate": 500_000,
                    "net_change": 1_500_000,
                    "estimated_inventory_amount": 3_000_000,
                },
                {
                    "label": "2026-05-04",
                    "purchase_increase": 0,
                    "sales_amount": 900_000,
                    "outbound_cost_estimate": 700_000,
                    "net_change": -700_000,
                    "estimated_inventory_amount": 2_300_000,
                },
            ]
        )

        figure = weekly_inventory_flow_figure(frame, spec)

        self.assertIsInstance(figure, go.Figure)
        self.assertEqual(len(figure.data), 4)
        self.assertEqual(list(figure.data[1].y), [-0.5, -0.7])
        self.assertEqual(list(figure.data[2].y), [-0.8, -0.9])
        self.assertEqual(figure.data[3].yaxis, "y2")
        self.assertEqual(figure.layout.yaxis2.overlaying, "y")

    def test_report_renders_inventory_as_dedicated_a4_page(self) -> None:
        html = render_report_html(load_sources(EXAMPLE_DIR), load_report_spec(BASE_DIR / "report_spec.yaml"))

        self.assertIn('class="report-page report-page--analysis"', html)
        self.assertIn("주간 재고금액 흐름: 매입은 위, 출고는 아래", html)
        self.assertIn("주간 출고 감소·주간 매출금액", html)
        self.assertIn("주말 추정 재고금액", html)
        self.assertIn("5/4", html)
        self.assertIn("1,510", html)
        self.assertIn("<th class=\"money\">매입 증가</th>", html)
        self.assertIn("<th class=\"money\">출고 감소(FIFO 원가)</th>", html)


if __name__ == "__main__":
    unittest.main()
