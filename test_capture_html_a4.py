#!/usr/bin/env python3
from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from capture_html_a4 import capture_html
from generate_analysis_report_html import generate_report


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise AssertionError(f"not a PNG file: {path}")
    return struct.unpack(">II", header[16:24])


class CaptureHtmlA4Tests(unittest.TestCase):
    def test_captures_full_page_with_requested_css_width_and_scale(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            html_path = base / "report.html"
            output_path = base / "report.png"
            html_path.write_text(
                """
                <!doctype html>
                <html>
                  <head>
                    <meta charset="utf-8">
                    <style>
                      body { margin: 0; }
                      main { width: 100%; height: 1800px; background: #f6f8fa; }
                    </style>
                  </head>
                  <body><main>capture target</main></body>
                </html>
                """,
                encoding="utf-8",
            )

            capture_html(html_path, output_path, width=320, height=600, scale=2)

            width, height = png_size(output_path)
            self.assertEqual(width, 640)
            self.assertGreaterEqual(height, 1800)

    def test_rejects_missing_input_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.html"
            output_path = Path(tmpdir) / "out.png"

            with self.assertRaises(FileNotFoundError):
                capture_html(missing, output_path)

    def test_captures_generated_report_at_a4_width_without_horizontal_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            html_path = base / "report.html"
            output_path = base / "report.png"
            generate_report(EXAMPLE_DIR, html_path)

            capture_html(html_path, output_path)

            width, _height = png_size(output_path)
            self.assertEqual(width, 1588)


if __name__ == "__main__":
    unittest.main()
