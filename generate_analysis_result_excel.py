#!/usr/bin/env python3
"""Create a review-only analysis workbook from parsed and calculated JSON.

The exporter deliberately consumes the same report-data builder as the HTML
report.  It does not reopen ERP workbooks, run FIFO/reconciliation/VAT logic,
or write Excel formulas.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import tempfile
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

import generate_analysis_report_html_core as report_core
from generate_analysis_report_html import weekly_inventory_flow_rows
from src.fifo_inventory import event_sort_key


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE_DIR / "private_intermediate"
DEFAULT_OUTPUT = BASE_DIR / "private_reports" / "analysis_result.xlsx"
DEFAULT_SPEC = BASE_DIR / "report_spec.yaml"
SHEET_NAMES = ["요약", "매입", "매출", "재고", "전표메타", "손익분석", "재고분석", "FIFO계산", "매출분석", "검증결과", "실행정보"]

HEADER_FILL = PatternFill("solid", fgColor="D9EAF7")
SECTION_FILL = PatternFill("solid", fgColor="EAF4E1")
WARNING_FILL = PatternFill("solid", fgColor="FFF2CC")
ERROR_FILL = PatternFill("solid", fgColor="FCE4D6")
HEADER_FONT = Font(bold=True)
STATUS_FONT = Font(bold=True)
FIFO_PRODUCT_BOUNDARY = Side(style="thin", color="9EADBA")

FIFO_CALCULATION_COLUMNS = [
    "product_id",
    "item_name",
    "specification",
    "transaction_date",
    "transaction_type",
    "transaction_id",
    "voucher",
    "excel_row",
    "transaction_quantity",
    "allocation_sequence",
    "allocated_quantity",
    "source_purchase_date",
    "source_purchase_voucher",
    "source_purchase_excel_row",
    "source_purchase_unit_cost",
    "fifo_cost_amount",
    "allocation_type",
    "unallocated_quantity",
]

REQUIRED_INPUTS = {
    "purchase": ["purchase.json", f"{report_core.LEGACY_PREFIX}purchase.json"],
    "sales": ["sales.json", f"{report_core.LEGACY_PREFIX}sales.json"],
    "inventory": ["inventory.json", "pe" + "tra_current_inventory.json"],
    "reconciliation": ["inventory_reconciliation.json", f"{report_core.LEGACY_PREFIX}inventory_reconciliation.json"],
}
OPTIONAL_INPUTS = {
    "sales_voucher_metadata": ["sales_voucher_metadata.json", f"{report_core.LEGACY_PREFIX}sales_voucher_metadata.json"],
}

REQUIRED_RECONCILIATION_METADATA_FIELDS = ("period_start", "period_end")
REQUIRED_RECONCILIATION_SUMMARY_FIELDS = {
    "period_sales_supply_amount": ("period_sales_supply_amount", "sales_amount"),
    "fifo_sales_cost_amount": ("fifo_sales_cost_amount",),
    "gross_profit": ("gross_profit",),
    "gross_profit_status": ("gross_profit_status", "profit_status"),
    "ending_fifo_inventory_amount": ("inventory_amount_at_fifo", "ending_fifo_inventory_amount"),
    "ending_signed_stock_quantity": ("ending_signed_stock_quantity",),
}
ALLOWED_GROSS_PROFIT_STATUSES = frozenset({"confirmed", "provisional", "error"})

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


def _present(value: Any) -> bool:
    return value is not None and (not isinstance(value, str) or bool(value.strip()))


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, Decimal):
        return value.is_finite()
    return math.isfinite(value)


def _first_reconciliation_value(summary: dict[str, Any], candidates: tuple[str, ...]) -> tuple[str, Any] | None:
    """Match the report-data fallback order without masking a malformed primary key."""
    for candidate in candidates:
        if candidate in summary:
            return candidate, summary[candidate]
    return None


def _validate_reconciliation_contract(reconciliation: dict[str, Any], path: Path) -> None:
    """Reject a JSON file that lacks the calculation results required by the workbook."""
    summary = reconciliation.get("summary")
    metadata = reconciliation.get("metadata")
    if not isinstance(summary, dict):
        raise ValueError(f"required reconciliation JSON has no summary object: {path}")
    if not isinstance(metadata, dict):
        raise ValueError(f"required reconciliation JSON has no metadata object: {path}")

    invalid = [field for field in REQUIRED_RECONCILIATION_METADATA_FIELDS if not _is_iso_date(metadata.get(field))]
    for label, candidates in REQUIRED_RECONCILIATION_SUMMARY_FIELDS.items():
        resolved = _first_reconciliation_value(summary, candidates)
        value = resolved[1] if resolved is not None else None
        is_valid = (
            isinstance(value, str) and value in ALLOWED_GROSS_PROFIT_STATUSES
            if label == "gross_profit_status"
            else _is_finite_number(value)
        )
        if not is_valid:
            invalid.append(label)
    if invalid:
        raise ValueError(f"required reconciliation results are missing or invalid in {path}: {', '.join(invalid)}")


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
    if not isinstance(reconciliation, dict):
        raise ValueError(f"required reconciliation JSON is not an object: {required_paths['reconciliation']}")
    _validate_reconciliation_contract(reconciliation, required_paths["reconciliation"])
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


def _transaction_id(row: dict[str, Any]) -> str:
    """Return the public FIFO transaction identifier without deriving new facts."""
    transaction_id = row.get("transaction_id") or row.get("sale_id")
    if transaction_id:
        return str(transaction_id)
    return f"{row.get('date')}|{row.get('voucher')}|{row.get('excel_row')}"


def _transaction_reference(row: dict[str, Any]) -> tuple[str, str, str]:
    """Identify a published transaction or cost layer by its source trace fields."""
    return (str(row.get("date")), str(row.get("voucher")), str(row.get("excel_row")))


def _source_records_by_transaction(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index parsed display fields; no FIFO allocation is inferred from them."""
    return {_transaction_id(record): record for record in records}


def _source_record(event: dict[str, Any], source_records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return source_records.get(_transaction_id(event), {})


def _row_number(value: Any) -> Any:
    """Keep public numeric values numeric when a cancellation needs its existing sign."""
    if isinstance(value, str):
        try:
            number = Decimal(value)
        except Exception:
            return value
        return int(number) if number == number.to_integral_value() else number
    return value


def _signed_cancellation_value(value: Any, transaction_quantity: Any) -> Any:
    """Express an existing restored allocation with the cancellation event sign.

    This only applies the already-published transaction sign; it never derives
    a quantity or amount from a unit cost.
    """
    value = _row_number(value)
    quantity = _row_number(transaction_quantity)
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and isinstance(quantity, (int, float, Decimal)) and quantity < 0:
        return -value if value > 0 else value
    return value


def _allocation_value(allocation: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in allocation:
            return allocation[name]
    return None


def _fifo_base_row(event: dict[str, Any], source: dict[str, Any], transaction_type: str) -> dict[str, Any]:
    """Build the transaction fields shared by all flattened display rows."""
    return {
        "product_id": event.get("product_id", source.get("product_id")),
        "item_name": source.get("item_name", event.get("item_name")),
        "specification": source.get("specification", event.get("specification")),
        "transaction_date": event.get("date", source.get("date")),
        "transaction_type": transaction_type,
        "transaction_id": _transaction_id(event),
        "voucher": event.get("voucher", source.get("voucher")),
        "excel_row": event.get("excel_row", source.get("excel_row")),
        "transaction_quantity": event.get("quantity", source.get("quantity")),
        "allocation_sequence": None,
        "allocated_quantity": None,
        "source_purchase_date": None,
        "source_purchase_voucher": None,
        "source_purchase_excel_row": None,
        "source_purchase_unit_cost": None,
        "fifo_cost_amount": None,
        "allocation_type": None,
        "unallocated_quantity": None,
    }


def _allocation_row(
    base: dict[str, Any],
    allocation: dict[str, Any],
    sequence: int,
    allocation_type: str,
    *,
    cancellation: bool = False,
) -> dict[str, Any]:
    """Flatten one public cost allocation into one Excel display row."""
    row = dict(base)
    row.update(
        allocation_sequence=sequence,
        allocated_quantity=_allocation_value(allocation, "purchase_quantity", "quantity"),
        source_purchase_date=allocation.get("purchase_date"),
        source_purchase_voucher=allocation.get("purchase_voucher"),
        source_purchase_excel_row=allocation.get("purchase_excel_row"),
        source_purchase_unit_cost=allocation.get("unit_cost"),
        fifo_cost_amount=_allocation_value(allocation, "cost_amount", "amount"),
        allocation_type=allocation_type,
    )
    if cancellation:
        row["allocated_quantity"] = _signed_cancellation_value(row["allocated_quantity"], row["transaction_quantity"])
        row["fifo_cost_amount"] = _signed_cancellation_value(row["fifo_cost_amount"], row["transaction_quantity"])
    return row


def _cancellation_allocations(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Use only the public cancellation references, including restored sale layers."""
    direct = event.get("allocations")
    if isinstance(direct, list):
        return [allocation for allocation in direct if isinstance(allocation, dict)]
    allocations: list[dict[str, Any]] = []
    for sale in event.get("sales", []):
        if isinstance(sale, dict):
            allocations.extend(
                allocation
                for allocation in sale.get("restored_allocations", [])
                if isinstance(allocation, dict)
            )
    return allocations


def _cancellation_events_by_reference(reconciliation: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    return {
        _transaction_reference(event): event
        for event in reconciliation.get("cancellation_events", [])
        if isinstance(event, dict)
    }


def _backfill_purchase_references(reconciliation: dict[str, Any]) -> set[tuple[str, str, str]]:
    """Identify later purchases from existing backfill allocations, never dates alone."""
    references: set[tuple[str, str, str]] = set()
    allocation_groups: list[Any] = [reconciliation.get("backfill_allocations", [])]
    allocation_groups.extend(
        sale.get("allocations", [])
        for sale in reconciliation.get("sales_allocations", [])
        if isinstance(sale, dict)
    )
    for allocations in allocation_groups:
        for allocation in allocations or []:
            if not isinstance(allocation, dict) or not (
                allocation.get("backfilled") or allocation.get("allocation_source") == "backfill"
            ):
                continue
            references.add(
                (
                    str(allocation.get("purchase_date")),
                    str(allocation.get("purchase_voucher")),
                    str(allocation.get("purchase_excel_row")),
                )
            )
    return references


def _fifo_event_is_in_display_range(
    event: dict[str, Any],
    kind: str,
    period_end: str | None,
    backfill_purchases: set[tuple[str, str, str]],
) -> bool:
    """Apply the FIFO report range using event references, not a guessed queue."""
    event_date = event.get("date")
    if not period_end or not event_date or str(event_date) <= period_end:
        return True
    return kind == "purchase" and event.get("event_type") == "purchase" and _transaction_reference(event) in backfill_purchases


def _fifo_product_sort_key(product_id: Any) -> str:
    # The FIFO engine publishes product-level result rows with this key.
    return str(product_id)


def _sale_unconfirmed_quantity(event: dict[str, Any], sales_allocations: dict[str, dict[str, Any]]) -> Any:
    allocation = sales_allocations.get(_transaction_id(event), {})
    if "unconfirmed_quantity" in allocation:
        return allocation.get("unconfirmed_quantity")
    return event.get("current_unconfirmed_quantity", event.get("unconfirmed_quantity_delta"))


def build_fifo_calculation_rows(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten published FIFO events for the Excel-only `FIFO계산` worksheet.

    This is deliberately a projection: it neither rebuilds layers nor derives
    cost from quantities and unit prices.  Parsed source records only provide
    user-facing item labels and the unit cost of a normal purchase row.
    """
    reconciliation = report_data["reconciliation"]
    metadata = reconciliation.get("metadata", {})
    period_end = metadata.get("period_end")
    purchase_records = _source_records_by_transaction(report_data["records"]["purchase"])
    sales_records = _source_records_by_transaction(report_data["records"]["sales"])
    sales_allocations = {
        _transaction_id(sale): sale
        for sale in reconciliation.get("sales_allocations", [])
        if isinstance(sale, dict)
    }
    cancellation_events = _cancellation_events_by_reference(reconciliation)
    backfill_purchases = _backfill_purchase_references(reconciliation)

    transaction_rows: list[tuple[tuple[Any, ...], list[dict[str, Any]]]] = []
    seen_cancellations: set[tuple[str, str, str]] = set()

    for event in reconciliation.get("purchase_cost_events", []):
        if not isinstance(event, dict) or not _fifo_event_is_in_display_range(event, "purchase", period_end, backfill_purchases):
            continue
        event_type = event.get("event_type")
        source = _source_record(event, purchase_records)
        if event_type == "purchase":
            row = _fifo_base_row(event, source, "purchase")
            row["source_purchase_unit_cost"] = source.get("unit_price")
            rows = [row]
        elif event_type == "purchase_cancellation":
            cancellation = cancellation_events.get(_transaction_reference(event), event)
            seen_cancellations.add(_transaction_reference(event))
            base = _fifo_base_row(event, source, "purchase_cancellation")
            allocations = _cancellation_allocations(cancellation)
            rows = [
                _allocation_row(base, allocation, sequence, "purchase_cancellation", cancellation=True)
                for sequence, allocation in enumerate(allocations, start=1)
            ]
            if not rows:
                row = dict(base)
                row.update(fifo_cost_amount=event.get("cost_amount"), allocation_type="purchase_cancellation")
                rows = [row]
        else:
            continue
        transaction_rows.append(((_fifo_product_sort_key(rows[0]["product_id"]), *event_sort_key(event, "purchase")), rows))

    for event in reconciliation.get("sales_cost_events", []):
        if not isinstance(event, dict) or not _fifo_event_is_in_display_range(event, "sales", period_end, backfill_purchases):
            continue
        event_type = event.get("event_type")
        source = _source_record(event, sales_records)
        if event_type == "sale":
            base = _fifo_base_row(event, source, "sales")
            allocations = event.get("cost_allocations", [])
            rows = [
                _allocation_row(
                    base,
                    allocation,
                    sequence,
                    "backfill" if allocation.get("backfilled") or allocation.get("allocation_source") == "backfill" else "period_fifo",
                )
                for sequence, allocation in enumerate(allocations, start=1)
                if isinstance(allocation, dict)
            ]
            unconfirmed_quantity = _sale_unconfirmed_quantity(event, sales_allocations)
            if unconfirmed_quantity not in (None, 0, Decimal("0"), "0", "0.0"):
                row = dict(base)
                row.update(
                    allocation_sequence=len(rows) + 1,
                    allocation_type="unallocated",
                    unallocated_quantity=unconfirmed_quantity,
                )
                rows.append(row)
            if not rows:
                # A zero-effective sale has no allocation fact to display;
                # its separate cancellation event remains in the transaction flow.
                continue
        elif event_type == "sale_cancellation":
            cancellation = cancellation_events.get(_transaction_reference(event), event)
            seen_cancellations.add(_transaction_reference(event))
            base = _fifo_base_row(event, source, "sales_cancellation")
            allocations = _cancellation_allocations(cancellation)
            rows = [
                _allocation_row(base, allocation, sequence, "sales_cancellation", cancellation=True)
                for sequence, allocation in enumerate(allocations, start=1)
            ]
            if not rows:
                row = dict(base)
                row.update(fifo_cost_amount=event.get("cost_amount"), allocation_type="sales_cancellation")
                rows = [row]
        else:
            continue
        transaction_rows.append(((_fifo_product_sort_key(rows[0]["product_id"]), *event_sort_key(event, "sales")), rows))

    # Modern reconciliation payloads publish cancellation cost events.  This
    # fallback keeps older public payloads reviewable when only their explicit
    # cancellation event is available; it still consumes no inferred layers.
    for reference, cancellation in cancellation_events.items():
        if reference in seen_cancellations or not _fifo_event_is_in_display_range(cancellation, "purchase" if cancellation.get("type") == "purchase_cancellation" else "sales", period_end, backfill_purchases):
            continue
        is_purchase = cancellation.get("type") == "purchase_cancellation"
        source = _source_record(cancellation, purchase_records if is_purchase else sales_records)
        source_quantity = source.get("quantity")
        event = {
            **cancellation,
            "event_type": cancellation.get("type"),
            "transaction_id": _transaction_id(cancellation),
            "quantity": source_quantity if source_quantity is not None else -_row_number(cancellation.get("requested_quantity")),
        }
        transaction_type = "purchase_cancellation" if is_purchase else "sales_cancellation"
        base = _fifo_base_row(event, source, transaction_type)
        rows = [
            _allocation_row(base, allocation, sequence, transaction_type, cancellation=True)
            for sequence, allocation in enumerate(_cancellation_allocations(cancellation), start=1)
        ]
        if not rows:
            rows = [base]
        transaction_rows.append(((_fifo_product_sort_key(base["product_id"]), *event_sort_key(event, "purchase" if is_purchase else "sales")), rows))

    transaction_rows.sort(key=lambda item: item[0])
    return [row for _, rows in transaction_rows for row in rows]


def _write_fifo_calculation_sheet(workbook: Workbook, report_data: dict[str, Any]) -> Worksheet:
    ws = _new_sheet(workbook, "FIFO계산")
    rows = build_fifo_calculation_rows(report_data)
    header_row, end_row, columns = _write_table(
        ws,
        rows,
        FIFO_CALCULATION_COLUMNS,
        include_empty_columns=True,
    )
    _set_tabular_sheet_options(ws, header_row, end_row, columns)

    product_column = columns.index("product_id") + 1
    previous_product_id = None
    for row_number in range(header_row + 1, end_row + 1):
        product_id = ws.cell(row_number, product_column).value
        if previous_product_id is not None and product_id != previous_product_id:
            for cell in ws[row_number]:
                cell.border = Border(top=FIFO_PRODUCT_BOUNDARY)
        previous_product_id = product_id
    return ws


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


def _inventory_flow(report_data: dict[str, Any]) -> list[dict[str, Any]]:
    return weekly_inventory_flow_rows(
        report_data["records"]["purchase"],
        report_data["records"]["sales"],
        report_data["records"]["inventory"],
        report_data["reconciliation"],
    )


def _write_inventory_sheet(
    workbook: Workbook,
    report_data: dict[str, Any],
    flow: list[dict[str, Any]],
) -> tuple[Worksheet, tuple[int, int, list[str]]]:
    ws = _new_sheet(workbook, "재고분석")
    inventory_rows = _write_table(
        ws,
        report_data["analysis"]["inventory_rows"],
        ["product_id", "item_name", "specification", "opening_stock_quantity", "period_purchase_quantity", "period_sales_quantity", "ending_signed_stock_quantity", "ending_fifo_inventory_amount", "inventory_quantity_difference", "cost_status", "quantity_reconciliation_status", "quantity_reconciliation_validation_status", "profit_status"],
        title="품목별 재고 대사 및 FIFO 상태",
        include_empty_columns=True,
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


def _chart_data_issues(
    report_data: dict[str, Any],
    inventory_flow: list[dict[str, Any]],
    validation_rows: list[dict[str, Any]],
) -> dict[str, str]:
    """Return chart identifiers and non-fatal reasons when their source table is unusable."""
    requirements = [
        (
            "purchase_sales",
            "기간별 매입 원가·매출 공급가액",
            inventory_flow,
            ["week_start", "label", "purchase_increase", "outbound_cost_estimate", "sales_amount", "net_change", "estimated_inventory_amount"],
            "label",
            ["purchase_increase", "sales_amount"],
        ),
        (
            "weekly_margin",
            "기간별 매출·매출원가·매출총이익",
            report_data["analysis"]["weekly_margin"],
            ["week_start", "label", "row_count", "quantity", "sales_amount", "cost_amount", "margin_amount", "margin_rate", "margin_status", "amount_validation_status"],
            "label",
            ["sales_amount", "cost_amount", "margin_amount"],
        ),
        (
            "inventory_value",
            "기간별 재고금액 추이",
            inventory_flow,
            ["week_start", "label", "purchase_increase", "outbound_cost_estimate", "sales_amount", "net_change", "estimated_inventory_amount"],
            "label",
            ["estimated_inventory_amount"],
        ),
        (
            "top_companies",
            "상위 거래처 매출",
            report_data["analysis"]["top_sales_by_company"],
            ["name", "row_count", "quantity", "total_amount"],
            "name",
            ["total_amount"],
        ),
        (
            "top_items",
            "상위 품목 매출",
            report_data["analysis"]["top_sales_by_item"],
            ["product_id", "item_name", "row_count", "quantity", "total_amount"],
            "item_name",
            ["total_amount"],
        ),
        (
            "validation_types",
            "검증 경고 유형별 건수",
            _warning_type_rows(validation_rows),
            ["type", "count"],
            "type",
            ["count"],
        ),
    ]
    issues: dict[str, str] = {}
    for chart_id, title, records, preferred_columns, category, series in requirements:
        if not records:
            # A clean analysis has no validation entries.  Its type-count
            # chart is intentionally absent, not evidence of missing data.
            if chart_id == "validation_types" and not validation_rows:
                continue
            issues[chart_id] = f"{title}: 차트 데이터 행이 제공되지 않았다."
            continue
        columns = _stable_columns(records, preferred_columns)
        missing = [column for column in [category, *series] if column not in columns]
        if missing:
            issues[chart_id] = f"{title}: 차트 필수 열이 없다 ({', '.join(missing)})."
            continue
        if not any(_present(record.get(category)) for record in records):
            issues[chart_id] = f"{title}: 범주 값이 제공되지 않았다."
            continue
        empty_series = [
            column
            for column in series
            if not any(isinstance(record.get(column), (int, float, Decimal)) and not isinstance(record.get(column), bool) for record in records)
        ]
        if empty_series:
            issues[chart_id] = f"{title}: 수치 데이터가 없다 ({', '.join(empty_series)})."
    return issues


def _append_chart_warnings(report_data: dict[str, Any], chart_issues: dict[str, str]) -> None:
    if not chart_issues:
        return
    report_data["validation"]["warnings"].extend(
        {
            "code": "chart_data_unavailable",
            "description": description,
            "chart_id": chart_id,
        }
        for chart_id, description in chart_issues.items()
    )
    report_data["status"] = "completed_with_warnings"


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
    value_axis_title: str = "금액",
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
    chart.x_axis.title = value_axis_title
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
    unavailable_chart_ids: set[str],
) -> None:
    if "purchase_sales" not in unavailable_chart_ids:
        _add_bar_chart(summary_ws, inventory_ws, inventory_flow_table, category="label", series=["purchase_increase", "sales_amount"], title="기간별 매입 원가·매출 공급가액", anchor="E2")
    if "weekly_margin" not in unavailable_chart_ids:
        _add_bar_chart(summary_ws, profit_ws, profit_table, category="label", series=["sales_amount", "cost_amount", "margin_amount"], title="기간별 매출·매출원가·매출총이익", anchor="N2")
    if "inventory_value" not in unavailable_chart_ids:
        _add_line_chart(summary_ws, inventory_ws, inventory_flow_table, category="label", series="estimated_inventory_amount", title="기간별 재고금액 추이", anchor="E17")
    if "top_companies" not in unavailable_chart_ids:
        _add_bar_chart(summary_ws, sales_ws, company_table, category="name", series=["total_amount"], title="상위 거래처 매출", anchor="N17")
    if "top_items" not in unavailable_chart_ids:
        _add_bar_chart(summary_ws, sales_ws, item_table, category="item_name", series=["total_amount"], title="상위 품목 매출", anchor="E32")
    if "validation_types" not in unavailable_chart_ids:
        _add_bar_chart(summary_ws, validation_ws, validation_table, category="type", series=["count"], title="검증 경고 유형별 건수", anchor="N32", value_axis_title="건수")


def _save_workbook_atomically(workbook: Workbook, output_path: Path) -> None:
    """Write, reopen, and atomically publish the workbook without a partial final file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output_path.parent,
            prefix=f".{output_path.stem}.",
            suffix=".xlsx",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
        workbook.save(temporary_path)
        reopened = load_workbook(temporary_path, read_only=True, data_only=False)
        reopened.close()
        os.replace(temporary_path, output_path)
    except Exception:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def export_workbook(
    sources: dict[str, Any],
    output_path: Path,
    *,
    spec: dict[str, Any] | None = None,
    required_paths: dict[str, Path] | None = None,
    optional_paths: dict[str, Path] | None = None,
) -> None:
    report_data = report_core.build_report_data(sources, spec)
    inventory_flow = _inventory_flow(report_data)
    validation_rows = _validation_rows(report_data)
    chart_issues = _chart_data_issues(report_data, inventory_flow, validation_rows)
    _append_chart_warnings(report_data, chart_issues)
    validation_rows = _validation_rows(report_data)
    workbook = Workbook()
    summary_ws = _write_summary_sheet(workbook, report_data, validation_rows)
    _write_record_sheet(workbook, "매입", report_data["records"]["purchase"], "purchase")
    _write_record_sheet(workbook, "매출", report_data["records"]["sales"], "sales")
    _write_record_sheet(workbook, "재고", report_data["records"]["inventory"], "inventory")
    _write_metadata_sheet(workbook, report_data)
    profit_ws, profit_table = _write_profit_sheet(workbook, report_data)
    inventory_ws, inventory_flow_table = _write_inventory_sheet(workbook, report_data, inventory_flow)
    _write_fifo_calculation_sheet(workbook, report_data)
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
        set(chart_issues),
    )
    if workbook.sheetnames != SHEET_NAMES:
        raise RuntimeError(f"unexpected worksheet order: {workbook.sheetnames}")
    _save_workbook_atomically(workbook, output_path)


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
