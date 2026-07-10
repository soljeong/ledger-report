#!/usr/bin/env python3
"""Create a review-only analysis workbook from parsed and calculated JSON.

The exporter deliberately consumes the same report-data builder as the HTML
report.  It does not reopen ERP workbooks, run FIFO/reconciliation/VAT logic,
or write Excel formulas.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

import generate_analysis_report_html_core as report_core
from generate_analysis_report_html import weekly_inventory_flow_rows


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "private_intermediate"
DEFAULT_OUTPUT = BASE_DIR / "private_reports" / "analysis_result.xlsx"
DEFAULT_SPEC = BASE_DIR / "report_spec.yaml"
SHEET_NAMES = ["요약", "매입", "매출", "재고", "전표메타", "손익분석", "재고분석", "매출분석", "검증결과", "실행정보"]

HEADER_FILL = PatternFill("solid", fgColor="D9EAF7")
SECTION_FILL = PatternFill("solid", fgColor="EAF4E1")
WARNING_FILL = PatternFill("solid", fgColor="FFF2CC")
ERROR_FILL = PatternFill("solid", fgColor="FCE4D6")
HEADER_FONT = Font(bold=True)
STATUS_FONT = Font(bold=True)

REQUIRED_INPUTS = {
    "purchase": ["purchase.json", f"{report_core.LEGACY_PREFIX}purchase.json"],
    "sales": ["sales.json", f"{report_core.LEGACY_PREFIX}sales.json"],
    "inventory": ["inventory.json", "pe" + "tra_current_inventory.json"],
    "reconciliation": ["inventory_reconciliation.json", f"{report_core.LEGACY_PREFIX}inventory_reconciliation.json"],
}
OPTIONAL_INPUTS = {
    "sales_voucher_metadata": ["sales_voucher_metadata.json", f"{report_core.LEGACY_PREFIX}sales_voucher_metadata.json"],
}

RECORD_COLUMNS = {
    "purchase": ["date", "voucher", "company", "product_id", "item_name", "specification", "quantity", "unit_price", "supply_amount", "vat", "total_amount", "excel_row"],
    "sales": ["date", "voucher", "company", "product_id", "item_name", "specification", "quantity", "unit_price", "supply_amount", "vat", "total_amount", "excel_row"],
    "inventory": ["product_id", "item_name", "specification", "stock_quantity", "average_cost", "latest_purchase_price", "sales_price", "excel_row"],
    "sales_voucher_metadata": ["source", "date", "voucher", "voucher_key", "principal", "excel_row"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate an analysis_result.xlsx file from JSON analysis inputs.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    return parser.parse_args()


def _resolve_input_paths(input_dir: Path) -> tuple[dict[str, Path], dict[str, Path]]:
    required: dict[str, Path] = {}
    optional: dict[str, Path] = {}
    for name, candidates in REQUIRED_INPUTS.items():
        path = next((input_dir / candidate for candidate in candidates if (input_dir / candidate).exists()), None)
        if path is None:
            raise FileNotFoundError(f"required {name} JSON is missing in {input_dir}: {', '.join(candidates)}")
        required[name] = path
    for name, candidates in OPTIONAL_INPUTS.items():
        path = next((input_dir / candidate for candidate in candidates if (input_dir / candidate).exists()), None)
        if path is not None:
            optional[name] = path
    return required, optional


def load_analysis_sources(input_dir: Path) -> tuple[dict[str, Any], dict[str, Path], dict[str, Path]]:
    """Load and validate JSON-only inputs, treating voucher metadata as optional."""
    required_paths, optional_paths = _resolve_input_paths(input_dir)
    try:
        sources = report_core.load_sources(input_dir)
    except (json.JSONDecodeError, OSError) as error:
        raise ValueError(f"could not read required analysis JSON in {input_dir}: {error}") from error

    for name in ("purchase", "sales", "inventory"):
        payload = sources.get(name)
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list) or not isinstance(payload.get("metadata"), dict):
            raise ValueError(f"required {name} JSON has no records list and metadata object: {required_paths[name]}")
    reconciliation = sources.get("reconciliation")
    if not isinstance(reconciliation, dict) or not isinstance(reconciliation.get("summary"), dict):
        raise ValueError(f"required reconciliation JSON has no summary object: {required_paths['reconciliation']}")
    return sources, required_paths, optional_paths


def _excel_value(value: Any, column: str = "") -> Any:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float, bool, datetime, date)):
        return value
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = str(value)
    if "date" in column.lower() and len(text) == 10:
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass
    return text


def _write_cell(cell, value: Any, column: str = "") -> None:
    value = _excel_value(value, column)
    cell.value = value
    # openpyxl infers formulas from a leading '='.  Force a shared string so
    # source data cannot become an executable workbook formula.
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        cell.data_type = "s"


def _column_kind(column: str) -> str:
    normalized = column.lower()
    if "date" in normalized or normalized in {"일자", "주 시작일"}:
        return "date"
    # Report analysis rows publish percentage points (for example 53.7),
    # whereas summary KPIs use fractional ratios.  The two contracts need
    # different Excel formats; see _set_labeled_value_format for KPI ratios.
    if normalized in {"margin_rate", "gross_profit_rate"}:
        return "percentage_points"
    if "ratio" in normalized or "비율" in normalized:
        return "ratio"
    if any(token in normalized for token in ("quantity", "stock", "수량", "건수", "row_count", "excel_row")):
        return "quantity"
    if any(token in normalized for token in ("amount", "price", "cost", "vat", "금액", "단가", "원가", "공급가")):
        return "money"
    return "text"


def _apply_value_format(cell, column: str) -> None:
    kind = _column_kind(column)
    if kind == "date" and isinstance(cell.value, (date, datetime)):
        cell.number_format = "yyyy-mm-dd"
    elif kind == "percentage_points" and isinstance(cell.value, (int, float)):
        cell.number_format = "0.0\\%"
    elif kind == "ratio" and isinstance(cell.value, (int, float)):
        cell.number_format = "0.0%"
    elif kind == "quantity" and isinstance(cell.value, (int, float)):
        cell.number_format = "#,##0.####"
    elif kind == "money" and isinstance(cell.value, (int, float)):
        cell.number_format = "#,##0"


def _style_header(ws: Worksheet, row: int, columns: list[str]) -> None:
    for index, column in enumerate(columns, start=1):
        cell = ws.cell(row, index)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _style_data_rows(ws: Worksheet, start_row: int, end_row: int, columns: list[str]) -> None:
    for row in ws.iter_rows(min_row=start_row, max_row=end_row, min_col=1, max_col=len(columns)):
        for cell, column in zip(row, columns):
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            _apply_value_format(cell, column)
            if str(cell.value).lower() in {"error", "validation_error"}:
                cell.fill = ERROR_FILL
            elif str(cell.value).lower() in {"warning", "provisional", "unconfirmed", "completed_with_warnings"}:
                cell.fill = WARNING_FILL


def _auto_size(ws: Worksheet) -> None:
    for column_cells in ws.columns:
        width = 9
        for cell in column_cells:
            if cell.value is None:
                continue
            longest = max(len(line) for line in (str(cell.value).splitlines() or [""]))
            width = max(width, min(longest + 2, 48))
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = width


def _stable_columns(records: Iterable[dict[str, Any]], preferred: list[str]) -> list[str]:
    extras = sorted({key for record in records for key in record if key not in preferred})
    return [key for key in preferred if key in {field for record in records for field in record}] + extras


def _write_table(
    ws: Worksheet,
    records: list[dict[str, Any]],
    preferred_columns: list[str],
    *,
    start_row: int = 1,
    title: str | None = None,
    include_empty_columns: bool = False,
) -> tuple[int, int, list[str]]:
    if title is not None:
        ws.cell(start_row, 1, title)
        ws.cell(start_row, 1).fill = SECTION_FILL
        ws.cell(start_row, 1).font = HEADER_FONT
        start_row += 1
    columns = _stable_columns(records, preferred_columns)
    if not columns and include_empty_columns:
        columns = preferred_columns
    if not columns:
        columns = ["message"]
        records = [{"message": "제공된 데이터가 없습니다."}]
    header_row = start_row
    for index, column in enumerate(columns, start=1):
        _write_cell(ws.cell(header_row, index), column, column)
    _style_header(ws, header_row, columns)
    for record in records:
        row_number = ws.max_row + 1
        for index, column in enumerate(columns, start=1):
            _write_cell(ws.cell(row_number, index), record.get(column), column)
    end_row = ws.max_row
    if end_row >= header_row + 1:
        _style_data_rows(ws, header_row + 1, end_row, columns)
    return header_row, end_row, columns


def _set_tabular_sheet_options(ws: Worksheet, header_row: int, end_row: int, columns: list[str]) -> None:
    ws.freeze_panes = f"A{header_row + 1}"
    ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(columns))}{max(end_row, header_row + 1)}"
    _auto_size(ws)


def _set_labeled_value_format(ws: Worksheet, labels: set[str], number_format: str) -> None:
    """Format only named KPI rows whose values share the adjacent value column."""
    for row in ws.iter_rows(min_col=1, max_col=2):
        if row[0].value in labels and isinstance(row[1].value, (int, float)):
            row[1].number_format = number_format


def _new_sheet(workbook: Workbook, title: str) -> Worksheet:
    return workbook.create_sheet(title)


def _write_record_sheet(workbook: Workbook, title: str, records: list[dict[str, Any]], record_type: str) -> Worksheet:
    ws = _new_sheet(workbook, title)
    header_row, end_row, columns = _write_table(
        ws,
        records,
        RECORD_COLUMNS[record_type],
        include_empty_columns=True,
    )
    _set_tabular_sheet_options(ws, header_row, end_row, columns)
    return ws


def _validation_rows(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def append_entries(entries: Iterable[dict[str, Any]], severity: str) -> None:
        for entry in entries:
            related = entry.get("transaction_id") or entry.get("sale_id") or entry.get("voucher_key")
            description = entry.get("description") or entry.get("message") or entry.get("reason")
            if description is None:
                description = entry.get("amount_validation_status") or entry.get("status") or entry.get("code")
            rows.append(
                {
                    "severity": severity,
                    "code": entry.get("code") or entry.get("amount_validation_status") or "unspecified",
                    "data_source": entry.get("source"),
                    "date": entry.get("date"),
                    "voucher": entry.get("voucher"),
                    "excel_row": entry.get("excel_row"),
                    "product_id": entry.get("product_id"),
                    "item_name": entry.get("item_name"),
                    "description": description,
                    "impact": ", ".join(
                        key.removeprefix("affects_")
                        for key, value in entry.items()
                        if key.startswith("affects_") and value
                    ),
                    "related_id": related,
                }
            )

    validation = report_data["validation"]
    append_entries(validation["errors"], "error")
    append_entries(validation["warnings"], "warning")
    known = {(row["code"], row["date"], row["voucher"], row["excel_row"]) for row in rows}
    for entry in report_data["summary"].get("amount_validation_errors", []):
        key = (entry.get("code"), entry.get("date"), entry.get("voucher"), entry.get("excel_row"))
        if key not in known:
            append_entries([entry], "warning")
    return rows


def _warning_type_rows(validation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(f"{row['severity']}:{row['code']}" for row in validation_rows)
    return [{"type": code, "count": count} for code, count in sorted(counts.items())]


def _write_summary_sheet(workbook: Workbook, report_data: dict[str, Any], validation_rows: list[dict[str, Any]]) -> Worksheet:
    ws = workbook.active
    ws.title = "요약"
    summary = report_data["summary"]
    kpis = report_data["kpis"]
    errors = sum(row["severity"] == "error" for row in validation_rows)
    warnings = sum(row["severity"] == "warning" for row in validation_rows)
    rows = [
        {"항목": "분석 결과 상태", "값": report_data["status"], "상태/주의": "비치명적 검증 문제는 검증결과 시트에서 확인" if report_data["status"] != "completed" else ""},
        {"항목": "분석 대상 기간", "값": report_data["period"]["label"], "상태/주의": ""},
        {"항목": "매입 상세 건수", "값": len(report_data["records"]["period_purchase"]), "상태/주의": "분석 기간 내"},
        {"항목": "매출 상세 건수", "값": len(report_data["records"]["period_sales"]), "상태/주의": "분석 기간 내"},
        {"항목": "재고 품목 수", "값": len(report_data["records"]["inventory"]), "상태/주의": "입력 재고 스냅샷"},
        {"항목": "매출 공급가액", "값": kpis["sales_supply_amount"], "상태/주의": "부가세 제외"},
        {"항목": "매출원가", "값": kpis["fifo_sales_cost_amount"], "상태/주의": "FIFO 배정 원가"},
        {"항목": "매출총이익", "값": kpis["gross_profit"], "상태/주의": kpis["gross_profit_status"] or "unknown"},
        {"항목": "매출총이익률", "값": kpis["gross_profit_rate"], "상태/주의": "확정 손익일 때만 표시" if kpis["gross_profit_rate"] is not None else "확정되지 않음"},
        {"항목": "기말 재고수량", "값": kpis["ending_inventory_quantity"], "상태/주의": "장부상 기말 수량"},
        {"항목": "기말 재고금액", "값": kpis["ending_inventory_amount"], "상태/주의": "FIFO 원가"},
        {"항목": "오류 건수", "값": errors, "상태/주의": "비치명적 오류 포함"},
        {"항목": "경고 건수", "값": warnings, "상태/주의": "비치명적 경고 포함"},
        {"항목": "금액 구성 검증 상태", "값": summary.get("amount_validation_status"), "상태/주의": ""},
    ]
    header_row, end_row, columns = _write_table(ws, rows, ["항목", "값", "상태/주의"], include_empty_columns=True)
    _set_labeled_value_format(ws, {"매출총이익률"}, "0.0%")
    _set_tabular_sheet_options(ws, header_row, end_row, columns)
    return ws


def _write_metadata_sheet(workbook: Workbook, report_data: dict[str, Any]) -> Worksheet:
    records = [{"source": "sales", **record} for record in report_data["records"]["sales_voucher_metadata"]]
    return _write_record_sheet(workbook, "전표메타", records, "sales_voucher_metadata")


def _write_profit_sheet(workbook: Workbook, report_data: dict[str, Any]) -> tuple[Worksheet, tuple[int, int, list[str]]]:
    ws = _new_sheet(workbook, "손익분석")
    summary = report_data["summary"]
    vat_settlement = summary.get("vat_settlement_amount")
    vat_direction = "납부 예상액" if vat_settlement and vat_settlement > 0 else ("환급 예상액" if vat_settlement and vat_settlement < 0 else "정산금")
    vat_label = f"{'잠정 ' if summary.get('vat_settlement_status') == 'validation_error' else ''}부가세 {vat_direction}"
    post_vat_status = summary.get("post_vat_reference_status")
    post_vat_label = "부가세 정산 후 잔여금액" if post_vat_status == "confirmed" else ("검증 필요" if post_vat_status == "error" else "잠정 참고값")
    metric_rows = [
        {"지표": "매출 공급가액", "값": report_data["kpis"]["sales_supply_amount"], "상태": "부가세 제외"},
        {"지표": "매출 부가세", "값": summary.get("period_sales_vat_amount"), "상태": summary.get("vat_settlement_status")},
        {"지표": "매출 합계금액", "값": summary.get("period_sales_total_amount"), "상태": "공급가액 + 부가세"},
        {"지표": "매입 공급가액", "값": summary.get("period_purchase_supply_amount"), "상태": "부가세 제외"},
        {"지표": "매입 부가세", "값": summary.get("period_purchase_vat_amount"), "상태": summary.get("vat_settlement_status")},
        {"지표": "매입 합계금액", "값": summary.get("period_purchase_total_amount"), "상태": "공급가액 + 부가세"},
        {"지표": vat_label, "값": abs(vat_settlement) if isinstance(vat_settlement, (int, float)) else vat_settlement, "상태": summary.get("vat_settlement_status")},
        {"지표": post_vat_label, "값": summary.get("post_vat_reference_amount"), "상태": post_vat_status},
        {"지표": "FIFO 매출원가", "값": report_data["kpis"]["fifo_sales_cost_amount"], "상태": ""},
        {"지표": "매출총이익", "값": report_data["kpis"]["gross_profit"], "상태": report_data["kpis"]["gross_profit_status"]},
        {"지표": "매출총이익률", "값": report_data["kpis"]["gross_profit_rate"], "상태": "확정 손익일 때만 표시"},
    ]
    _write_table(ws, metric_rows, ["지표", "값", "상태"], title="손익 요약", include_empty_columns=True)
    _set_labeled_value_format(ws, {"매출총이익률"}, "0.0%")
    weekly = _write_table(
        ws,
        report_data["analysis"]["weekly_margin"],
        ["week_start", "label", "row_count", "quantity", "sales_amount", "cost_amount", "margin_amount", "margin_rate", "margin_status", "amount_validation_status"],
        start_row=ws.max_row + 2,
        title="기간별 손익",
        include_empty_columns=True,
    )
    _auto_size(ws)
    return ws, weekly


def _write_inventory_sheet(workbook: Workbook, report_data: dict[str, Any]) -> tuple[Worksheet, tuple[int, int, list[str]]]:
    ws = _new_sheet(workbook, "재고분석")
    inventory_rows = _write_table(
        ws,
        report_data["analysis"]["inventory_rows"],
        ["product_id", "item_name", "specification", "opening_stock_quantity", "period_purchase_quantity", "period_sales_quantity", "ending_signed_stock_quantity", "ending_fifo_inventory_amount", "inventory_quantity_difference", "cost_status", "quantity_reconciliation_status", "quantity_reconciliation_validation_status", "profit_status"],
        title="품목별 재고 대사 및 FIFO 상태",
        include_empty_columns=True,
    )
    flow = weekly_inventory_flow_rows(
        report_data["records"]["purchase"],
        report_data["records"]["sales"],
        report_data["records"]["inventory"],
        report_data["reconciliation"],
    )
    flow_rows = _write_table(
        ws,
        flow,
        ["week_start", "label", "purchase_increase", "outbound_cost_estimate", "sales_amount", "net_change", "estimated_inventory_amount"],
        start_row=ws.max_row + 2,
        title="기간별 재고금액 흐름",
        include_empty_columns=True,
    )
    _auto_size(ws)
    return ws, flow_rows


def _write_sales_sheet(workbook: Workbook, report_data: dict[str, Any]) -> tuple[Worksheet, tuple[int, int, list[str]], tuple[int, int, list[str]]]:
    ws = _new_sheet(workbook, "매출분석")
    company_rows = _write_table(
        ws,
        report_data["analysis"]["top_sales_by_company"],
        ["name", "row_count", "quantity", "total_amount"],
        title="상위 거래처별 매출 공급가액",
        include_empty_columns=True,
    )
    item_rows = _write_table(
        ws,
        report_data["analysis"]["top_sales_by_item"],
        ["product_id", "item_name", "row_count", "quantity", "total_amount"],
        start_row=ws.max_row + 2,
        title="상위 품목별 매출 공급가액",
        include_empty_columns=True,
    )
    principal_rows = _write_table(
        ws,
        report_data["analysis"]["principal_margin"],
        ["principal", "row_count", "quantity", "sales_amount", "cost_amount", "margin_amount", "margin_rate", "margin_status", "amount_validation_status"],
        start_row=ws.max_row + 2,
        title="원청별 매출·원가·이익",
        include_empty_columns=True,
    )
    _auto_size(ws)
    return ws, company_rows, item_rows


def _write_validation_sheet(workbook: Workbook, validation_rows: list[dict[str, Any]]) -> tuple[Worksheet, tuple[int, int, list[str]]]:
    ws = _new_sheet(workbook, "검증결과")
    _write_table(
        ws,
        validation_rows,
        ["severity", "code", "data_source", "date", "voucher", "excel_row", "product_id", "item_name", "description", "impact", "related_id"],
        include_empty_columns=True,
    )
    type_rows = _write_table(
        ws,
        _warning_type_rows(validation_rows),
        ["type", "count"],
        start_row=ws.max_row + 2,
        title="검증 유형별 건수",
        include_empty_columns=True,
    )
    _auto_size(ws)
    return ws, type_rows


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BASE_DIR, text=True).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _write_execution_sheet(
    workbook: Workbook,
    report_data: dict[str, Any],
    required_paths: dict[str, Path],
    optional_paths: dict[str, Path],
) -> Worksheet:
    ws = _new_sheet(workbook, "실행정보")
    spec_version = report_data["spec"].get("version") if isinstance(report_data["spec"], dict) else None
    rows = [
        {"항목": "생성 시각", "값": datetime.now().astimezone().isoformat(timespec="seconds")},
        {"항목": "결과 상태", "값": report_data["status"]},
        {"항목": "분석 대상 기간", "값": report_data["period"]["label"]},
        {"항목": "입력 JSON 파일", "값": ", ".join(path.name for path in [*required_paths.values(), *optional_paths.values()])},
        {"항목": "원본 파일명 메타데이터", "값": ", ".join(str(meta.get("source_file", "unknown")) for meta in report_data["metadata"].values() if isinstance(meta, dict))},
        {"항목": "매입 JSON 레코드 수", "값": len(report_data["records"]["purchase"])},
        {"항목": "매출 JSON 레코드 수", "값": len(report_data["records"]["sales"])},
        {"항목": "재고 JSON 레코드 수", "값": len(report_data["records"]["inventory"])},
        {"항목": "전표 메타 JSON 레코드 수", "값": len(report_data["records"]["sales_voucher_metadata"])},
        {"항목": "보고서 스펙 버전", "값": spec_version or "unknown"},
        {"항목": "생성기 Git commit", "값": _git_commit()},
        {"항목": "적용된 경고", "값": ", ".join(str(item.get("code", "warning")) for item in report_data["validation"]["warnings"]) or "없음"},
        {"항목": "제공되지 않은 선택 입력", "값": "없음" if optional_paths else "sales_voucher_metadata.json"},
    ]
    header_row, end_row, columns = _write_table(ws, rows, ["항목", "값"], include_empty_columns=True)
    _set_tabular_sheet_options(ws, header_row, end_row, columns)
    return ws


def _column_index(columns: list[str], name: str) -> int | None:
    return columns.index(name) + 1 if name in columns else None


def _add_bar_chart(
    summary_ws: Worksheet,
    source_ws: Worksheet,
    table: tuple[int, int, list[str]],
    *,
    category: str,
    series: list[str],
    title: str,
    anchor: str,
) -> None:
    header_row, end_row, columns = table
    category_col = _column_index(columns, category)
    series_cols = [_column_index(columns, name) for name in series]
    if end_row <= header_row or category_col is None or any(column is None for column in series_cols):
        return
    chart = BarChart()
    chart.type = "bar"
    chart.style = 10
    chart.title = title
    chart.y_axis.title = category
    chart.x_axis.title = "금액"
    chart.height = 7
    chart.width = 15
    categories = Reference(source_ws, min_col=category_col, min_row=header_row + 1, max_row=end_row)
    for series_col in series_cols:
        # series may be non-contiguous in the source table.  Adding each
        # requested column separately prevents adjacent measures from silently
        # becoming chart series.
        chart.add_data(
            Reference(source_ws, min_col=series_col, max_col=series_col, min_row=header_row, max_row=end_row),
            titles_from_data=True,
        )
    chart.set_categories(categories)
    summary_ws.add_chart(chart, anchor)


def _add_line_chart(
    summary_ws: Worksheet,
    source_ws: Worksheet,
    table: tuple[int, int, list[str]],
    *,
    category: str,
    series: str,
    title: str,
    anchor: str,
) -> None:
    header_row, end_row, columns = table
    category_col = _column_index(columns, category)
    series_col = _column_index(columns, series)
    if end_row <= header_row or category_col is None or series_col is None:
        return
    chart = LineChart()
    chart.style = 13
    chart.title = title
    chart.y_axis.title = "금액"
    chart.x_axis.title = category
    chart.height = 7
    chart.width = 15
    chart.add_data(Reference(source_ws, min_col=series_col, max_col=series_col, min_row=header_row, max_row=end_row), titles_from_data=True)
    chart.set_categories(Reference(source_ws, min_col=category_col, min_row=header_row + 1, max_row=end_row))
    summary_ws.add_chart(chart, anchor)


def _add_charts(
    summary_ws: Worksheet,
    profit_ws: Worksheet,
    profit_table: tuple[int, int, list[str]],
    inventory_ws: Worksheet,
    inventory_flow_table: tuple[int, int, list[str]],
    sales_ws: Worksheet,
    company_table: tuple[int, int, list[str]],
    item_table: tuple[int, int, list[str]],
    validation_ws: Worksheet,
    validation_table: tuple[int, int, list[str]],
) -> None:
    _add_bar_chart(summary_ws, inventory_ws, inventory_flow_table, category="label", series=["purchase_increase", "sales_amount"], title="기간별 매입 원가·매출 공급가액", anchor="E2")
    _add_bar_chart(summary_ws, profit_ws, profit_table, category="label", series=["sales_amount", "cost_amount", "margin_amount"], title="기간별 매출·매출원가·매출총이익", anchor="N2")
    _add_line_chart(summary_ws, inventory_ws, inventory_flow_table, category="label", series="estimated_inventory_amount", title="기간별 재고금액 추이", anchor="E17")
    _add_bar_chart(summary_ws, sales_ws, company_table, category="name", series=["total_amount"], title="상위 거래처 매출", anchor="N17")
    _add_bar_chart(summary_ws, sales_ws, item_table, category="item_name", series=["total_amount"], title="상위 품목 매출", anchor="E32")
    _add_bar_chart(summary_ws, validation_ws, validation_table, category="type", series=["count"], title="검증 경고 유형별 건수", anchor="N32")


def export_workbook(
    sources: dict[str, Any],
    output_path: Path,
    *,
    spec: dict[str, Any] | None = None,
    required_paths: dict[str, Path] | None = None,
    optional_paths: dict[str, Path] | None = None,
) -> None:
    report_data = report_core.build_report_data(sources, spec)
    validation_rows = _validation_rows(report_data)
    workbook = Workbook()
    summary_ws = _write_summary_sheet(workbook, report_data, validation_rows)
    _write_record_sheet(workbook, "매입", report_data["records"]["purchase"], "purchase")
    _write_record_sheet(workbook, "매출", report_data["records"]["sales"], "sales")
    _write_record_sheet(workbook, "재고", report_data["records"]["inventory"], "inventory")
    _write_metadata_sheet(workbook, report_data)
    profit_ws, profit_table = _write_profit_sheet(workbook, report_data)
    inventory_ws, inventory_flow_table = _write_inventory_sheet(workbook, report_data)
    sales_ws, company_table, item_table = _write_sales_sheet(workbook, report_data)
    validation_ws, validation_table = _write_validation_sheet(workbook, validation_rows)
    _write_execution_sheet(workbook, report_data, required_paths or {}, optional_paths or {})
    _add_charts(
        summary_ws,
        profit_ws,
        profit_table,
        inventory_ws,
        inventory_flow_table,
        sales_ws,
        company_table,
        item_table,
        validation_ws,
        validation_table,
    )
    if workbook.sheetnames != SHEET_NAMES:
        raise RuntimeError(f"unexpected worksheet order: {workbook.sheetnames}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)


def generate_report(input_dir: Path, output_path: Path, spec_path: Path = DEFAULT_SPEC) -> None:
    sources, required_paths, optional_paths = load_analysis_sources(input_dir)
    spec = report_core.load_report_spec(spec_path)
    export_workbook(
        sources,
        output_path,
        spec=spec,
        required_paths=required_paths,
        optional_paths=optional_paths,
    )


def main() -> int:
    args = parse_args()
    generate_report(args.input_dir, args.output, args.spec)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
