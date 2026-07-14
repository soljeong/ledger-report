#!/usr/bin/env python3
"""Extend the analysis workbook with principal-grouped review sheets.

The established exporter remains in ``_generate_analysis_result_excel_base``.
This module preserves its public API and inserts two Excel-only review sheets
without changing the shared HTML/report-data calculation contract.
"""
from __future__ import annotations

from typing import Any

from openpyxl import Workbook

import _generate_analysis_result_excel_base as _base
from _generate_analysis_result_excel_base import *  # noqa: F401,F403
from src.principal_excel_sheets import (
    PRINCIPAL_ITEM_SHEET,
    PRINCIPAL_VOUCHER_SHEET,
    write_principal_item_sheet,
    write_principal_voucher_sheet,
)


SHEET_NAMES = [
    "요약",
    "매입",
    "매출",
    "재고",
    "전표메타",
    "손익분석",
    "재고분석",
    "FIFO계산",
    "매출분석",
    PRINCIPAL_VOUCHER_SHEET,
    PRINCIPAL_ITEM_SHEET,
    "검증결과",
    "실행정보",
]

# Keep the base exporter's runtime worksheet-order assertion aligned with the
# extended workbook contract.
_base.SHEET_NAMES = SHEET_NAMES

if not hasattr(_base, "_principal_sheets_original_write_sales_sheet"):
    _base._principal_sheets_original_write_sales_sheet = _base._write_sales_sheet


def _write_sales_sheet_with_principal_sheets(
    workbook: Workbook,
    report_data: dict[str, Any],
):
    """Write the existing sales analysis, then the two principal drilldowns."""
    result = _base._principal_sheets_original_write_sales_sheet(workbook, report_data)
    fifo_rows = _base.build_fifo_calculation_rows(report_data)
    write_principal_voucher_sheet(workbook, report_data)
    write_principal_item_sheet(workbook, report_data, fifo_rows)
    return result


_base._write_sales_sheet = _write_sales_sheet_with_principal_sheets


def _sync_patchable_dependencies() -> None:
    # Existing tests and callers patch this module path.  Keep those patches
    # visible to the preserved base implementation before delegating.
    _base.load_workbook = load_workbook


def export_workbook(*args, **kwargs) -> None:
    _sync_patchable_dependencies()
    return _base.export_workbook(*args, **kwargs)


def generate_report(*args, **kwargs) -> None:
    _sync_patchable_dependencies()
    return _base.generate_report(*args, **kwargs)


def main() -> int:
    _sync_patchable_dependencies()
    return _base.main()


if __name__ == "__main__":
    raise SystemExit(main())
