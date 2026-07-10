from __future__ import annotations

import json
import unittest
from pathlib import Path

from generate_analysis_report_html import load_report_spec, load_sources, render_report_html


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class PublicLayoutTest(unittest.TestCase):
    def test_private_work_directories_are_ignored_without_report_name_patterns(self) -> None:
        ignore_text = (BASE_DIR / ".gitignore").read_text(encoding="utf-8")

        self.assertIn("private_sources/", ignore_text)
        self.assertIn("private_intermediate/", ignore_text)
        self.assertIn("private_reports/", ignore_text)
        original_report_name = "pe" + "tra_5_7_analysis_report"
        self.assertNotIn(original_report_name, ignore_text)

    def test_dummy_json_can_render_generic_report(self) -> None:
        required_files = [
            "purchase.json",
            "sales.json",
            "inventory.json",
            "inventory_reconciliation.json",
            "sales_voucher_metadata.json",
        ]
        for filename in required_files:
            payload = json.loads((EXAMPLE_DIR / filename).read_text(encoding="utf-8"))
            self.assertIsInstance(payload, dict)

        html = render_report_html(load_sources(EXAMPLE_DIR), load_report_spec(BASE_DIR / "report_spec.yaml"))

        self.assertIn("<title>매입/매출 재고 분석 보고서</title>", html)
        self.assertIn("<h1>매입/매출 재고 분석 보고서</h1>", html)
        self.assertEqual(html.count('class="report-page'), 4)
        self.assertIn("Sample Robotics", html)
        self.assertNotIn("페" + "트라", html)

    def test_readme_starts_with_preview_and_compact_title(self) -> None:
        readme = (BASE_DIR / "README.md").read_text(encoding="utf-8")
        first_lines = "\n".join(readme.splitlines()[:4])

        self.assertIn("# Ledger Workbook Analysis", first_lines)
        self.assertIn("docs/assets/report-preview.png", first_lines)
        self.assertNotIn("페" + "트라", first_lines)

    def test_public_files_do_not_contain_original_project_name(self) -> None:
        blocked = ["Pe" + "tra", "pe" + "tra", "페" + "트라"]
        skipped_dirs = {".venv", "__pycache__", "private_sources", "private_intermediate", "private_reports", ".git"}
        offenders: list[str] = []

        for path in BASE_DIR.rglob("*"):
            if not path.is_file():
                continue
            if any(part in skipped_dirs for part in path.relative_to(BASE_DIR).parts):
                continue
            if path.suffix.lower() in {".png", ".xlsx"}:
                continue
            text = path.read_text(encoding="utf-8")
            if any(token in text for token in blocked):
                offenders.append(str(path.relative_to(BASE_DIR)))

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
