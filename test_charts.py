#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import yaml

from src.charts import principal_margin_figure, weekly_inventory_flow_figure, weekly_purchase_sales_figure


BASE_DIR = Path(__file__).resolve().parent


class ChartFigureTests(unittest.TestCase):
    def test_weekly_chart_uses_dataframe_and_report_spec_labels(self) -> None:
        spec = yaml.safe_load((BASE_DIR / "report_spec.yaml").read_text(encoding="utf-8"))
        df = pd.DataFrame(
            [
                {"label": "5/18", "sales_amount": 1500, "cost_amount": 1000},
                {"label": "5/25", "sales_amount": 1200, "cost_amount": 800},
            ]
        )

        figure = weekly_purchase_sales_figure(df, spec["charts"]["weekly_purchase_sales"])

        self.assertIsInstance(figure, go.Figure)
        self.assertFalse(figure.layout.title.text)
        self.assertEqual(figure.layout.xaxis.title.text, "주 시작일")
        self.assertEqual(figure.layout.yaxis.title.text, "금액")
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[0].name, "매출 공급가액")
        self.assertEqual(figure.data[1].name, "매출원가")
        self.assertEqual(figure.layout.barmode, "overlay")

    def test_weekly_inventory_flow_chart_overlays_sales_on_negative_side(self) -> None:
        spec = yaml.safe_load((BASE_DIR / "report_spec.yaml").read_text(encoding="utf-8"))
        df = pd.DataFrame(
            [
                {
                    "label": "2026-04-27",
                    "purchase_increase": 1000,
                    "sales_amount": 800,
                    "outbound_cost_estimate": 500,
                    "net_change": 500,
                    "estimated_inventory_amount": 2400,
                }
            ]
        )

        figure = weekly_inventory_flow_figure(df, spec["charts"]["weekly_inventory_flow"])

        self.assertIsInstance(figure, go.Figure)
        self.assertEqual(figure.layout.xaxis.title.text, "주 시작일")
        self.assertEqual(figure.layout.barmode, "overlay")
        self.assertEqual(figure.data[2].name, "주간 매출 공급가액")
        self.assertEqual(list(figure.data[2].y), [-0.0008])

    def test_principal_chart_uses_dataframe_and_report_spec_labels(self) -> None:
        spec = yaml.safe_load((BASE_DIR / "report_spec.yaml").read_text(encoding="utf-8"))
        df = pd.DataFrame(
            [
                {
                    "principal": "Sample Robotics",
                    "sales_amount": 53720010,
                    "cost_amount": 49234560,
                    "margin_rate": 8.3,
                },
                {
                    "principal": "Demo Automation",
                    "sales_amount": 22215160,
                    "cost_amount": 21259681,
                    "margin_rate": 4.3,
                },
            ]
        )

        figure = principal_margin_figure(df, spec["charts"]["principal_margin"])

        self.assertIsInstance(figure, go.Figure)
        self.assertFalse(figure.layout.title.text)
        self.assertEqual(figure.layout.xaxis.title.text, "금액")
        self.assertEqual(figure.layout.yaxis.title.text, "원청")
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[0].name, "매출 공급가액")
        self.assertEqual(figure.data[1].name, "매출원가")
        self.assertGreaterEqual(figure.layout.margin.r, 120)
        self.assertIs(figure.data[0].cliponaxis, False)
        self.assertEqual(figure.layout.barmode, "overlay")
        self.assertNotIn("상태", figure.data[0].hovertemplate)
        self.assertIsNone(figure.data[0].customdata)


if __name__ == "__main__":
    unittest.main()
