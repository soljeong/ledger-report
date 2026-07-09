#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import yaml

from src.charts import principal_margin_figure, weekly_purchase_sales_figure


BASE_DIR = Path(__file__).resolve().parent


class ChartFigureTests(unittest.TestCase):
    def test_weekly_chart_uses_dataframe_and_report_spec_labels(self) -> None:
        spec = yaml.safe_load((BASE_DIR / "report_spec.yaml").read_text(encoding="utf-8"))
        df = pd.DataFrame(
            [
                {"label": "2026-05-18 ~ 05-24", "purchase_amount": 1000, "sales_amount": 1500},
                {"label": "2026-05-25 ~ 05-31", "purchase_amount": 2000, "sales_amount": 1200},
            ]
        )

        figure = weekly_purchase_sales_figure(df, spec["charts"]["weekly_purchase_sales"])

        self.assertIsInstance(figure, go.Figure)
        self.assertEqual(figure.layout.title.text, "주간 매입/매출 금액")
        self.assertEqual(figure.layout.xaxis.title.text, "주간")
        self.assertEqual(figure.layout.yaxis.title.text, "금액")
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[0].name, "매입금액")
        self.assertEqual(figure.data[1].name, "매출금액")

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
        self.assertEqual(figure.layout.title.text, "원청별 매출/원가/마진")
        self.assertEqual(figure.layout.xaxis.title.text, "금액")
        self.assertEqual(figure.layout.yaxis.title.text, "원청")
        self.assertEqual(len(figure.data), 2)
        self.assertEqual(figure.data[0].name, "매출금액")
        self.assertEqual(figure.data[1].name, "매출원가")
        self.assertGreaterEqual(figure.layout.margin.r, 120)
        self.assertIs(figure.data[0].cliponaxis, False)


if __name__ == "__main__":
    unittest.main()
