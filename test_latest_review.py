from __future__ import annotations

from decimal import Decimal
import unittest

from analyze_inventory import build_reconciliation
from generate_analysis_report_html_core import principal_sales_cost_rows, weekly_purchase_sales_amounts


def purchase(day, qty=1, price=100, product=1, voucher=1):
    return {"date": day, "quantity": qty, "unit_price": price, "supply_amount": qty * Decimal(str(price)), "vat": 0, "total_amount": qty * Decimal(str(price)), "product_id": product, "voucher": voucher, "excel_row": voucher}


def sale(day, qty=1, amount=1, product=1, voucher=1):
    return {"date": day, "quantity": qty, "supply_amount": amount, "vat": 0, "total_amount": amount, "product_id": product, "voucher": voucher, "excel_row": voucher}


def reconcile(purchases, sales, start="2026-01-01", end="2026-01-31"):
    return build_reconciliation({"records": purchases}, {"records": sales}, {"records": []}, start, end, end)


class LatestReviewTests(unittest.TestCase):
    def exact_fixture(self):
        buys = [purchase("2026-01-01", 2, "0.49")]
        sells = [sale("2026-01-02", voucher=1), sale("2026-01-12", voucher=2)]
        result = reconcile(buys, sells)
        metadata = {"records": [{"voucher_key": "2026-01-02-1", "principal": "A"}, {"voucher_key": "2026-01-12-2", "principal": "B"}]}
        return result, buys, sells, metadata

    def test_weekly_exact_cost_total(self):
        r, b, s, _ = self.exact_fixture()
        self.assertEqual(sum(Decimal(x["cost_amount_exact"]) for x in weekly_purchase_sales_amounts(b, s, [], r)), Decimal("0.98"))

    def test_principal_exact_cost_total(self):
        r, b, s, m = self.exact_fixture()
        self.assertEqual(sum(Decimal(x["cost_amount_exact"]) for x in principal_sales_cost_rows(s, b, m, r)), Decimal("0.98"))

    def test_summary_exact_cost_total(self):
        r, *_ = self.exact_fixture(); self.assertEqual(r["summary"]["fifo_sales_cost_amount_exact"], "0.98")

    def test_weekly_display_rounds_each_group(self):
        r, b, s, _ = self.exact_fixture(); self.assertEqual(sum(x["cost_amount"] for x in weekly_purchase_sales_amounts(b, s, [], r)), 0)

    def test_principal_display_rounds_each_group(self):
        r, b, s, m = self.exact_fixture(); self.assertEqual(sum(x["cost_amount"] for x in principal_sales_cost_rows(s, b, m, r)), 0)

    def test_prior_sale_backfill_marks_item_backfilled(self):
        r = reconcile([purchase("2026-02-01", 10, 100)], [sale("2025-12-20", 10, 1000)], end="2026-01-31")
        self.assertEqual(r["rows"][0]["cost_status"], "backfilled")

    def test_prior_sale_backfill_is_not_period_backfill(self):
        r = reconcile([purchase("2026-02-01", 10, 100)], [sale("2025-12-20", 10, 1000)], end="2026-01-31")
        self.assertEqual((r["summary"]["period_backfilled_quantity"], r["summary"]["all_backfilled_quantity"]), (0, 10))

    def test_invalid_sales_date_makes_vat_error(self):
        r = reconcile([purchase("2026-01-01")], [sale("2026/01/15")]); self.assertEqual(r["summary"]["vat_settlement_status"], "validation_error")

    def test_invalid_purchase_date_makes_vat_error(self):
        r = reconcile([purchase("", 1)], [sale("2026-01-15")]); self.assertEqual(r["summary"]["vat_settlement_status"], "validation_error")

    def test_inventory_only_error_does_not_make_vat_error(self):
        r = reconcile([purchase("2026-01-01")], [sale("2026-01-15")]); self.assertEqual(r["summary"]["vat_settlement_status"], "zero")

    def test_related_post_purchase_error_marks_week_error(self):
        bad = purchase("2026-02-01", 1, 100); bad["unit_price"] = None
        r = reconcile([bad], [sale("2026-01-15")])
        self.assertEqual(weekly_purchase_sales_amounts([], [sale("2026-01-15")], [], r)[0]["margin_status"], "error")

    def test_related_post_purchase_error_marks_principal_error(self):
        bad = purchase("2026-02-01", 1, 100); bad["unit_price"] = None; s = [sale("2026-01-15")]
        r = reconcile([bad], s); m = {"records": [{"voucher_key": "2026-01-15-1", "principal": "A"}]}
        self.assertEqual(principal_sales_cost_rows(s, [], m, r)[0]["margin_status"], "error")

    def test_unrelated_post_purchase_error_does_not_propagate(self):
        bad = purchase("2026-02-01", 1, 100, product=2); bad["unit_price"] = None
        self.assertEqual(reconcile([bad], [sale("2026-01-15", product=1)])["summary"]["gross_profit_status"], "provisional")

    def test_exact_sales_summary_exists(self):
        r, *_ = self.exact_fixture(); self.assertEqual(r["summary"]["period_sales_supply_amount_exact"], "2")

    def test_exact_margin_summary_exists(self):
        r, *_ = self.exact_fixture(); self.assertEqual(r["summary"]["gross_profit_exact"], "1.02")

    def test_weekly_final_unconfirmed_invariant(self):
        r = reconcile([], [sale("2026-01-15", 2)]); w = weekly_purchase_sales_amounts([], [sale("2026-01-15", 2)], [], r)
        self.assertEqual(sum(x["current_unconfirmed_quantity"] for x in w), r["summary"]["period_unconfirmed_quantity"])

    def test_principal_final_unconfirmed_invariant(self):
        s = [sale("2026-01-15", 2)]; r = reconcile([], s); m = {"records": [{"voucher_key": "2026-01-15-1", "principal": "A"}]}
        self.assertEqual(sum(x["current_unconfirmed_quantity"] for x in principal_sales_cost_rows(s, [], m, r)), r["summary"]["period_unconfirmed_quantity"])

    def test_backfill_dependency_is_exposed(self):
        bad = purchase("2026-02-01"); bad["unit_price"] = None; r = reconcile([bad], [sale("2026-01-15")])
        self.assertTrue(any(error.get("backfill_dependency") for error in r["errors"]))

    def test_group_exact_margin_is_present(self):
        r, b, s, _ = self.exact_fixture(); self.assertEqual(weekly_purchase_sales_amounts(b, s, [], r)[0]["margin_amount_exact"], "0.51")

    def test_group_rounding_difference_is_present(self):
        r, b, s, _ = self.exact_fixture(); self.assertEqual(weekly_purchase_sales_amounts(b, s, [], r)[0]["rounding_difference"], "0.49")

    def test_unassigned_post_purchase_error_is_global_not_principal_error(self):
        bad = purchase("2026-02-01"); bad["product_id"] = None; bad["unit_price"] = None
        s = [sale("2026-01-15")]; r = reconcile([bad], s); m = {"records": [{"voucher_key": "2026-01-15-1", "principal": "A"}]}
        self.assertTrue(r["summary"]["unassigned_backfill_validation_error"])
        self.assertEqual(principal_sales_cost_rows(s, [], m, r)[0]["margin_status"], "provisional")
