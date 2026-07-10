"""Focused regression coverage for the PR #5 review cases."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import unittest

from analyze_inventory import build_reconciliation
from generate_analysis_report_html import load_sources, render_report_html


BASE = Path(__file__).resolve().parent / "examples" / "dummy_json"


def _purchase(day, quantity=1, price=100, product=1):
    return {"date": day, "quantity": quantity, "unit_price": price, "supply_amount": quantity * price, "vat": 0, "total_amount": quantity * price, "product_id": product, "voucher": 1, "excel_row": 1}


def _sale(day, quantity=1, amount=200, product=1):
    return {"date": day, "quantity": quantity, "supply_amount": amount, "vat": 0, "total_amount": amount, "product_id": product, "voucher": 1, "excel_row": 1}


def _reconcile(purchases, sales, start="2026-01-01", end="2026-01-31"):
    return build_reconciliation({"records": purchases}, {"records": sales}, {"records": []}, start, end, end)


class ReviewRegressionTests(unittest.TestCase):
    def test_normal_placeholder_is_replaced_with_chart(self):
        sources = deepcopy(load_sources(BASE))
        summary = sources["reconciliation"]["summary"]
        summary.update(gross_profit_status="confirmed", opening_stock_amount=0, period_purchase_cost_amount=100, period_sales_supply_amount=200, sales_amount=200, inventory_amount_at_fifo=0, ending_fifo_inventory_amount=0, post_period_backfill_amount=0, prior_period_shortage_settlement_amount=0, gross_profit=100)
        self.assertIn('id="amount-balance-chart"', render_report_html(sources))

    def test_post_period_backfill_confirms_original_sale_week(self):
        r = _reconcile([_purchase("2026-01-08")], [_sale("2026-01-02")], end="2026-01-05")
        self.assertEqual(r["sales_allocations"][0]["cost_status"], "backfilled")

    def test_partial_backfill_remains_provisional(self):
        r = _reconcile([_purchase("2026-01-08")], [_sale("2026-01-02", quantity=2)], end="2026-01-05")
        self.assertEqual(r["summary"]["gross_profit_status"], "provisional")

    def test_post_period_amount_error_does_not_change_vat(self):
        p = _purchase("2026-02-01"); p["total_amount"] += 1
        r = _reconcile([_purchase("2026-01-01"), p], [_sale("2026-01-02")])
        self.assertEqual(r["summary"]["vat_settlement_status"], "zero")

    def test_invalid_blank_date_is_excluded(self):
        bad = _sale("")
        self.assertEqual(_reconcile([_purchase("2026-01-01")], [bad])["summary"]["period_sales_record_count"], 0)

    def test_invalid_none_date_is_excluded(self):
        bad = _sale(None)
        self.assertEqual(_reconcile([_purchase("2026-01-01")], [bad])["summary"]["period_sales_record_count"], 0)

    def test_invalid_impossible_date_is_excluded(self):
        bad = _sale("2026-02-30")
        self.assertEqual(_reconcile([_purchase("2026-01-01")], [bad])["summary"]["period_sales_record_count"], 0)

    def test_vat_error_sets_reference_error_status(self):
        sale = _sale("2026-01-02"); sale["total_amount"] += 1
        self.assertEqual(_reconcile([_purchase("2026-01-01")], [sale])["summary"]["post_vat_reference_status"], "error")

    def test_current_period_confirmed_with_opening_shortage(self):
        r = _reconcile([], [_sale("2025-12-31")])
        self.assertEqual(r["summary"]["gross_profit_status"], "confirmed")

    def test_all_shortages_are_shown_in_unconfirmed_shipments(self):
        later = _sale("2026-01-02"); later.update(voucher=2, excel_row=2)
        r = _reconcile([], [_sale("2025-12-31"), later])
        self.assertEqual(len(r["unconfirmed_shipments"]), 2)

    def test_error_date_does_not_create_principal_group(self):
        sources = deepcopy(load_sources(BASE)); sources["sales"]["records"].append(_sale("bad-date", product=77))
        self.assertNotIn("bad-date", render_report_html(sources))

    def test_decimal_costs_are_rounded_after_aggregation(self):
        r = _reconcile([_purchase("2026-01-01", quantity=10, price="0.49")], [_sale("2026-01-02", quantity=10)])
        self.assertEqual(r["summary"]["fifo_sales_cost_amount"], 5)

    def test_negative_sales_amount_renders_note(self):
        sources = deepcopy(load_sources(BASE)); sources["reconciliation"]["summary"].update(gross_profit_status="confirmed", period_sales_supply_amount=-1, sales_amount=-1)
        self.assertIn("금액 밸런스 차트", render_report_html(sources))

    def test_negative_purchase_amount_renders_note(self):
        sources = deepcopy(load_sources(BASE)); sources["reconciliation"]["summary"].update(gross_profit_status="confirmed", period_purchase_cost_amount=-1)
        self.assertIn("금액 밸런스 차트", render_report_html(sources))
