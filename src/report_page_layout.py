from __future__ import annotations

from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Any

from plotly.offline import get_plotlyjs, get_plotlyjs_version


REPORT_PAGE_ANCHORS: dict[str, str] = {
    "vat_settlement": 'id="vat-settlement-title"',
    "weekly_inventory_flow": 'id="inventory-flow-title"',
    "weekly_purchase_sales": 'id="weekly-title"',
    "principal_margin": 'id="principal-title"',
}
DEFAULT_REPORT_PAGES: tuple[str, ...] = tuple(REPORT_PAGE_ANCHORS)
PLOTLY_REPORT_PAGES = frozenset(
    {"weekly_inventory_flow", "weekly_purchase_sales", "principal_margin"}
)


class _ReportArticleParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=False)
        self.source = source
        self.line_starts = [0]
        for index, character in enumerate(source):
            if character == "\n":
                self.line_starts.append(index + 1)
        self.article_depth = 0
        self.article_start: int | None = None
        self.spans: list[tuple[int, int]] = []

    def _offset(self) -> int:
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "article":
            return
        if self.article_depth:
            self.article_depth += 1
            return
        classes = dict(attrs).get("class") or ""
        if "report-page" in classes.split():
            self.article_start = self._offset()
            self.article_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if tag != "article" or not self.article_depth:
            return
        self.article_depth -= 1
        if self.article_depth:
            return
        end_start = self._offset()
        end = self.source.find(">", end_start)
        if self.article_start is None or end < 0:
            raise RuntimeError("report page closing tag not found")
        self.spans.append((self.article_start, end + 1))
        self.article_start = None


def resolve_report_pages(spec: Mapping[str, Any] | None) -> list[str]:
    """Return validated non-cover page IDs in their requested order."""
    report = (spec or {}).get("report", {})
    pages = report.get("pages")
    if pages is None:
        return list(DEFAULT_REPORT_PAGES)
    if not isinstance(pages, list):
        raise ValueError("report.pages must be a list of page IDs")

    resolved: list[str] = []
    for index, page_id in enumerate(pages):
        if not isinstance(page_id, str) or not page_id.strip():
            raise ValueError(f"report.pages[{index}] must be a non-empty string")
        resolved.append(page_id.strip())

    duplicates = sorted({page_id for page_id in resolved if resolved.count(page_id) > 1})
    if duplicates:
        raise ValueError(f"duplicate report page IDs: {', '.join(duplicates)}")

    unknown = [page_id for page_id in resolved if page_id not in REPORT_PAGE_ANCHORS]
    if unknown:
        available = ", ".join(REPORT_PAGE_ANCHORS)
        raise ValueError(
            f"unknown report page IDs: {', '.join(unknown)}; available: {available}. "
            "The cover page is fixed and must not be listed."
        )
    return resolved


def _page_id(block: str) -> str:
    matches = [
        page_id
        for page_id, anchor in REPORT_PAGE_ANCHORS.items()
        if anchor in block
    ]
    if len(matches) != 1:
        raise RuntimeError(
            "each non-cover report page must contain exactly one registered page anchor"
        )
    return matches[0]


def _report_page_spans(main_content: str) -> list[tuple[int, int]]:
    parser = _ReportArticleParser(main_content)
    parser.feed(main_content)
    parser.close()
    if parser.article_depth or parser.article_start is not None:
        raise RuntimeError("report page article is not closed")
    return parser.spans


def apply_report_page_layout(
    html: str,
    spec: Mapping[str, Any] | None,
) -> tuple[str, list[str]]:
    """Keep the cover fixed and reorder/filter every following report page."""
    selected = resolve_report_pages(spec)
    main_start = html.find("<main>")
    if main_start < 0:
        raise RuntimeError("report <main> start marker not found")
    content_start = main_start + len("<main>")
    main_end = html.find("</main>", content_start)
    if main_end < 0:
        raise RuntimeError("report </main> end marker not found")

    main_content = html[content_start:main_end]
    spans = _report_page_spans(main_content)
    if not spans:
        raise RuntimeError("report pages not found")

    blocks = [main_content[start:end].strip() for start, end in spans]
    cover = blocks[0]
    if "report-page--cover" not in cover:
        raise RuntimeError("the first report page must be the fixed cover page")

    page_blocks: dict[str, str] = {}
    for block in blocks[1:]:
        page_id = _page_id(block)
        if page_id in page_blocks:
            raise RuntimeError(f"duplicate rendered report page: {page_id}")
        page_blocks[page_id] = block

    missing = [page_id for page_id in REPORT_PAGE_ANCHORS if page_id not in page_blocks]
    if missing:
        raise RuntimeError(f"registered report pages were not rendered: {', '.join(missing)}")

    prefix = main_content[: spans[0][0]]
    suffix = main_content[spans[-1][1] :]
    ordered_blocks = [cover, *(page_blocks[page_id] for page_id in selected)]
    rebuilt = prefix + "\n\n    ".join(ordered_blocks) + suffix
    return html[:content_start] + rebuilt + html[main_end:], selected


def _plotly_script(spec: Mapping[str, Any] | None) -> str:
    value = (spec or {}).get("plotly", {}).get("include_plotlyjs", "inline")
    if value in (True, "inline", "embed", "true", None):
        return f'<script type="text/javascript">{get_plotlyjs()}</script>'
    if value in (False, "false", "none"):
        return ""
    if value == "cdn":
        return (
            '<script charset="utf-8" '
            f'src="https://cdn.plot.ly/plotly-{get_plotlyjs_version()}.min.js"></script>'
        )
    raise ValueError(f"unsupported plotly.include_plotlyjs value: {value!r}")


def finalize_report_html(html: str, spec: Mapping[str, Any] | None) -> str:
    """Apply page layout and load Plotly independently of any optional page."""
    html, selected = apply_report_page_layout(html, spec)
    if not PLOTLY_REPORT_PAGES.intersection(selected):
        return html

    script = _plotly_script(spec)
    if not script:
        return html
    head_end = html.find("</head>")
    if head_end < 0:
        raise RuntimeError("report </head> marker not found")
    return html[:head_end] + f"  {script}\n" + html[head_end:]
