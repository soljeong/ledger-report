from __future__ import annotations

import unittest
from pathlib import Path

from plotly.offline import get_plotlyjs_version

from generate_analysis_report_html import load_report_spec, load_sources, render_report_html
from src.report_page_layout import DEFAULT_REPORT_PAGES, resolve_report_pages


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class ReportPageLayoutTests(unittest.TestCase):
    def test_configuration_reorders_and_excludes_non_cover_pages(self):
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")
        spec["report"]["pages"] = ["principal_margin", "weekly_inventory_flow"]
        spec["plotly"]["include_plotlyjs"] = "cdn"

        html = render_report_html(load_sources(EXAMPLE_DIR), spec)

        self.assertEqual(html.count('class="report-page'), 3)
        self.assertLess(html.index("report-page--overview"), html.index('id="principal-title"'))
        self.assertLess(html.index('id="principal-title"'), html.index('id="inventory-flow-title"'))
        self.assertNotIn('id="weekly-title"', html)
        self.assertNotIn('id="item-detail-title"', html)
        self.assertIn('<span class="page-number">01 / 03</span>', html)
        self.assertIn('<span class="page-number">02 / 03</span>', html)
        self.assertIn('<span class="page-number">03 / 03</span>', html)
        self.assertIn(
            f'src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"',
            html,
        )

    def test_empty_page_list_outputs_only_the_fixed_overview(self):
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")
        spec["report"]["pages"] = []
        spec["plotly"]["include_plotlyjs"] = "cdn"

        html = render_report_html(load_sources(EXAMPLE_DIR), spec)

        self.assertEqual(html.count('class="report-page'), 1)
        self.assertIn("report-page--overview", html)
        self.assertIn('<span class="page-number">01 / 01</span>', html)
        self.assertNotIn("https://cdn.plot.ly", html)

    def test_page_configuration_defaults_and_validation(self):
        self.assertEqual(resolve_report_pages({"report": {}}), list(DEFAULT_REPORT_PAGES))

        invalid_cases = (
            ({"report": {"pages": "weekly_purchase_sales"}}, "must be a list"),
            ({"report": {"pages": ["weekly_purchase_sales", "weekly_purchase_sales"]}}, "duplicate"),
            ({"report": {"pages": ["unknown_page"]}}, "unknown"),
            ({"report": {"pages": ["cover"]}}, "fixed"),
        )
        for spec, message in invalid_cases:
            with self.subTest(spec=spec):
                with self.assertRaisesRegex(ValueError, message):
                    resolve_report_pages(spec)


if __name__ == "__main__":
    unittest.main()
