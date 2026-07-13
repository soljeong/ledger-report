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
        spec["report"]["pages"] = ["principal_margin", "vat_settlement"]
        spec["plotly"]["include_plotlyjs"] = "cdn"

        html = render_report_html(load_sources(EXAMPLE_DIR), spec)

        self.assertEqual(html.count('class="report-page'), 3)
        self.assertLess(html.index("report-page--cover"), html.index('id="principal-title"'))
        self.assertLess(html.index('id="principal-title"'), html.index('id="vat-settlement-title"'))
        self.assertNotIn('id="inventory-flow-title"', html)
        self.assertNotIn('id="weekly-title"', html)
        self.assertIn(
            f'src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"',
            html,
        )

    def test_empty_page_list_outputs_only_the_fixed_cover(self):
        spec = load_report_spec(BASE_DIR / "report_spec.yaml")
        spec["report"]["pages"] = []
        spec["plotly"]["include_plotlyjs"] = "cdn"

        html = render_report_html(load_sources(EXAMPLE_DIR), spec)

        self.assertEqual(html.count('class="report-page'), 1)
        self.assertIn("report-page--cover", html)
        self.assertNotIn("https://cdn.plot.ly", html)

    def test_page_configuration_defaults_and_validation(self):
        self.assertEqual(resolve_report_pages({"report": {}}), list(DEFAULT_REPORT_PAGES))

        invalid_cases = (
            ({"report": {"pages": "vat_settlement"}}, "must be a list"),
            ({"report": {"pages": ["vat_settlement", "vat_settlement"]}}, "duplicate"),
            ({"report": {"pages": ["unknown_page"]}}, "unknown"),
            ({"report": {"pages": ["cover"]}}, "fixed"),
        )
        for spec, message in invalid_cases:
            with self.subTest(spec=spec):
                with self.assertRaisesRegex(ValueError, message):
                    resolve_report_pages(spec)


if __name__ == "__main__":
    unittest.main()
