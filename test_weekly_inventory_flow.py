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
        )

        self.assertEqual(rows[0]["week_end"], "2026-05-04")
        self.assertEqual(rows[0]["purchase_increase"], 740_000)
        self.assertEqual(rows[0]["estimated_inventory_amount"], 835_000)
        self.assertEqual(rows[-1]["week_end"], "2026-06-22")
        self.assertEqual(rows[-1]["estimated_inventory_amount"], 676_500)

    def test_chart_uses_signed_bars_and_secondary_axis(self) -> None:
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")["charts"]["weekly_inventory_flow"]
        frame = pd.DataFrame(
            [
                {
                    "label": "05/04",
                    "purchase_increase": 2_000_000,
                    "outbound_cost_estimate": 500_000,
                    "net_change": 1_500_000,
                    "estimated_inventory_amount": 3_000_000,
                },
                {
                    "label": "05/11",
                    "purchase_increase": 0,
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
        self.assertEqual(figure.data[2].yaxis, "y2")
        self.assertEqual(figure.layout.yaxis2.overlaying, "y")

    def test_report_embeds_chart_and_weekly_table_in_amount_section(self) -> None:
        html = render_report_html(load_sources(EXAMPLE_DIR), load_report_spec(BASE_DIR / "report_spec.yaml"))

        self.assertIn("주간 재고금액 흐름: 매입은 위, 출고는 아래", html)
        self.assertIn("주간 출고 감소(추정원가)", html)
        self.assertIn("주말 추정 재고금액", html)
        self.assertIn("주 종료일", html)
        self.assertIn("676,500", html)


if __name__ == "__main__":
    unittest.main()
