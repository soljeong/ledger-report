#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT = BASE_DIR / "private_reports" / "report.html"
DEFAULT_OUTPUT = BASE_DIR / "private_reports" / "report_a4_full.png"
DEFAULT_WIDTH = 794
DEFAULT_HEIGHT = 1123
DEFAULT_SCALE = 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture an HTML report as a full-page A4-width PNG.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="HTML file to capture.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="PNG output path.")
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH, help="Viewport CSS width. 794px is A4 width at 96dpi.")
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT, help="Viewport CSS height.")
    parser.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="Device scale factor for sharper output.")
    parser.add_argument("--channel", default="chrome", help="Playwright browser channel. Defaults to system Chrome.")
    return parser.parse_args()


def capture_html(
    input_path: Path,
    output_path: Path,
    *,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    scale: float = DEFAULT_SCALE,
    channel: str = "chrome",
) -> Path:
    input_path = input_path.resolve()
    output_path = output_path.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
        )
        page.goto(input_path.as_uri(), wait_until="networkidle")
        page.emulate_media(media="screen")
        page.screenshot(path=str(output_path), full_page=True, animations="disabled")
        browser.close()
    return output_path


def main() -> int:
    args = parse_args()
    output_path = capture_html(
        args.input,
        args.output,
        width=args.width,
        height=args.height,
        scale=args.scale,
        channel=args.channel,
    )
    print(f"wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
