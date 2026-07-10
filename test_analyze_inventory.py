from __future__ import annotations

import unittest

from analyze_inventory import build_reconciliation


def purchase(date: str, quantity: int, unit_price: int, *, product_id=1, voucher=1, excel_row=1, name="Item", spec="A") -> dict:
    return {
        "date": date, "quantity": quantity, "unit_price": unit_price,
        "total_amount": quantity * unit_price, "supply_amount": quantity * unit_price,
        "product_id": product_id, "voucher": voucher, "excel_row": excel_row,
        "item_name": name, "specification": spec,
    }


def sale(date: str, quantity: int, *, product_id=1, voucher=1, excel_row=1, amount=0, name="Item", spec="A") -> dict:
    return {
        "date": date, "quantity": quantity, "unit_price": 999999,
        "total_amount": amount, "supply_amount": amount,
        "product_id": product_id, "voucher": voucher, "excel_row": excel_row,
        "item_name": name, "specification": spec,
    }


def inventory(quantity: int, *, product_id=1, average_cost=999999, latest_purchase_price=888888, name="Item", spec="A") -> dict:
    return {
        "product_id": product_id, "stock_quantity": quantity,
        "average_cost": average_cost, "latest_purchase_price": latest_purchase_price,
        "item_name": name, "specification": spec,
    }


def analyze(purchases, sales, inventories=None, start="2026-01-01", end="2026-12-31", stock_date="2026-12-31"):
    return build_reconciliation(
        {"records": purchases}, {"records": sales}, {"records": inventories or []}, start, end, stock_date
    )


class InventoryAnalysisTests(unittest.TestCase):
    def test_fifo_consumes_multiple_purchase_layers(self):
        result = analyze(
            [purchase("2026-01-01", 5, 100), purchase("2026-01-02", 5, 200, voucher=2, excel_row=2)],
            [sale("2026-01-03", 8)],
            [inventory(2)],
        )
        row = result["rows"][0]
        self.assertEqual(row["fifo_sales_cost_amount"], 1100)
        self.assertEqual(row["ending_fifo_inventory_amount"], 400)
        self.assertEqual(result["sales_allocations"][0]["allocations"][1]["purchase_quantity"], 3)

    def test_period_start_creates_opening_fifo_layers(self):
        result = analyze(
            [purchase("2025-12-01", 10, 100)],
            [sale("2026-01-05", 4)],
            [inventory(6)],
            start="2026-01-01",
        )
        row = result["rows"][0]
        self.assertEqual(row["opening_stock_quantity"], 10)
        self.assertEqual(row["opening_stock_amount"], 1000)
        self.assertEqual(row["fifo_sales_cost_amount"], 400)

    def test_same_day_purchase_is_processed_before_sale(self):
        result = analyze(
            [purchase("2026-01-02", 5, 120)],
            [sale("2026-01-02", 5)],
            [inventory(0)],
        )
        self.assertEqual(result["summary"]["fifo_sales_cost_amount"], 600)
        self.assertEqual(result["summary"]["unconfirmed_quantity"], 0)

    def test_post_end_transactions_do_not_change_end_inventory(self):
        base = analyze([purchase("2026-01-01", 10, 100)], [sale("2026-01-02", 4)], [inventory(6)], end="2026-01-02", stock_date="2026-01-02")
        later = analyze(
            [purchase("2026-01-01", 10, 100), purchase("2026-01-03", 20, 200, voucher=2, excel_row=2)],
            [sale("2026-01-02", 4), sale("2026-01-04", 3, voucher=2, excel_row=2)],
            [inventory(23)], end="2026-01-02", stock_date="2026-01-04",
        )
        self.assertEqual(base["summary"]["ending_fifo_inventory_amount"], 600)
        self.assertEqual(later["summary"]["ending_fifo_inventory_amount"], 600)
        self.assertEqual(later["rows"][0]["ending_signed_stock_quantity"], 6)

    def test_post_end_negative_purchase_cannot_change_end_snapshot(self):
        result = analyze(
            [purchase("2026-01-01", 10, 100), purchase("2026-01-02", -5, 100, voucher=2, excel_row=2)],
            [], [inventory(5)], end="2026-01-01", stock_date="2026-01-02",
        )
        row = result["rows"][0]
        self.assertEqual((row["ending_normal_stock_quantity"], row["ending_fifo_inventory_amount"]), (10, 1000))
        self.assertEqual(result["errors"][0]["code"], "purchase_cancellation_exceeds_remaining")

    def test_post_end_negative_purchase_can_cancel_only_post_end_remainder(self):
        result = analyze(
            [purchase("2026-01-01", 10, 100), purchase("2026-01-02", 7, 200, voucher=2, excel_row=2), purchase("2026-01-03", -5, 200, voucher=3, excel_row=3)],
            [], [inventory(12)], end="2026-01-01", stock_date="2026-01-03",
        )
        self.assertEqual(result["rows"][0]["ending_fifo_inventory_amount"], 1000)
        self.assertEqual(result["errors"], [])

    def test_followup_purchases_backfill_old_shortage_in_fifo_order(self):
        result = analyze(
            [purchase("2026-01-03", 6, 100), purchase("2026-01-04", 10, 120, voucher=2, excel_row=2)],
            [sale("2026-01-01", 10)],
            [inventory(6)], end="2026-01-01", stock_date="2026-01-04",
        )
        sale_result = result["sales_allocations"][0]
        self.assertEqual(sale_result["cost_status"], "backfilled")
        self.assertEqual(sale_result["fifo_cost_amount"], 1080)
        self.assertEqual(result["summary"]["backfilled_quantity"], 10)
        self.assertEqual(result["metadata"]["backfill_last_purchase_date"], "2026-01-04")

    def test_partial_shortage_remains_unconfirmed(self):
        result = analyze(
            [purchase("2026-01-03", 6, 100)], [sale("2026-01-01", 10)], [inventory(6)], end="2026-01-01", stock_date="2026-01-03"
        )
        self.assertEqual(result["summary"]["unconfirmed_quantity"], 4)
        self.assertEqual(result["rows"][0]["cost_status"], "unconfirmed")

    def test_backfilled_status_is_used_when_any_followup_layer_is_used(self):
        result = analyze([purchase("2026-01-03", 10, 100)], [sale("2026-01-01", 10)], [inventory(10)], end="2026-01-01", stock_date="2026-01-03")
        self.assertEqual(result["rows"][0]["cost_status"], "backfilled")

    def test_negative_purchase_cancels_latest_remaining_layer_first(self):
        result = analyze(
            [purchase("2026-01-01", 5, 100), purchase("2026-01-02", 5, 200, voucher=2, excel_row=2), purchase("2026-01-03", -3, 200, voucher=3, excel_row=3)],
            [], [inventory(7)],
        )
        layers = result["rows"][0]["remaining_cost_layers"]
        self.assertEqual([(layer["unit_cost"], layer["remaining_quantity"]) for layer in layers], [(100, 5), (200, 2)])

    def test_negative_purchase_can_cross_multiple_layers(self):
        result = analyze(
            [purchase("2026-01-01", 5, 100), purchase("2026-01-02", 5, 200, voucher=2, excel_row=2), purchase("2026-01-03", -7, 200, voucher=3, excel_row=3)],
            [], [inventory(3)],
        )
        self.assertEqual(result["rows"][0]["ending_normal_stock_quantity"], 3)
        self.assertEqual(result["rows"][0]["ending_fifo_inventory_amount"], 300)
        self.assertEqual(result["summary"]["period_purchase_cost_amount"], 300)

    def test_negative_purchase_overage_is_error(self):
        result = analyze([purchase("2026-01-01", 2, 100), purchase("2026-01-02", -3, 100, voucher=2, excel_row=2)], [], [inventory(2)])
        self.assertEqual(result["rows"][0]["cost_status"], "error")
        self.assertEqual(result["errors"][0]["requested_quantity"], 3)
        self.assertEqual(result["errors"][0]["cancellable_quantity"], 2)

    def test_negative_sale_restores_recent_sale_allocations(self):
        result = analyze(
            [purchase("2026-01-01", 10, 100)],
            [sale("2026-01-02", 4), sale("2026-01-03", 3, voucher=2, excel_row=2), sale("2026-01-04", -5, voucher=3, excel_row=3)],
            [inventory(8)],
        )
        self.assertEqual(result["summary"]["period_sales_quantity"], 2)
        self.assertEqual(result["summary"]["fifo_sales_cost_amount"], 200)
        self.assertEqual(result["rows"][0]["ending_fifo_inventory_amount"], 800)

    def test_backfilled_sale_cancellation_restores_layer_and_reuses_it(self):
        result = analyze(
            [purchase("2026-01-02", 10, 100)],
            [sale("2026-01-01", 10), sale("2026-01-03", -5, voucher=2, excel_row=2), sale("2026-01-04", 5, voucher=3, excel_row=3)],
            [inventory(0)], end="2026-01-04", stock_date="2026-01-04",
        )
        self.assertEqual(result["summary"]["ending_normal_stock_quantity"], 0)
        self.assertEqual(result["summary"]["fifo_sales_cost_amount"], 1000)
        self.assertEqual([event["cost_amount"] for event in result["sales_cost_events"]], [1000, -500, 500])

    def test_pre_period_sale_cancellation_is_current_period_negative_cogs(self):
        result = analyze(
            [purchase("2025-12-19", 4, 100)],
            [sale("2025-12-20", 4), sale("2026-01-10", -2, voucher=2, excel_row=2)],
            [inventory(2)], start="2026-01-01", end="2026-01-31", stock_date="2026-01-31",
        )
        row = result["rows"][0]
        self.assertEqual((row["period_sales_quantity"], row["fifo_sales_cost_amount"]), (-2, -200))
        self.assertEqual(row["ending_fifo_inventory_amount"], 200)

    def test_unconfirmed_sale_cancellation_has_no_cost_event(self):
        result = analyze([], [sale("2026-01-01", 10), sale("2026-01-02", -4, voucher=2, excel_row=2)], [inventory(-6)])
        self.assertEqual(result["summary"]["unconfirmed_quantity"], 6)
        self.assertEqual(result["summary"]["fifo_sales_cost_amount"], 0)
        self.assertEqual(result["sales_cost_events"][1]["cost_amount"], 0)

    def test_amount_identity_holds_for_non_error_product(self):
        result = analyze(
            [purchase("2025-12-01", 10, 100), purchase("2026-01-02", 5, 200)],
            [sale("2026-01-03", 8)], [inventory(7)], start="2026-01-01", end="2026-01-31", stock_date="2026-01-31",
        )
        row = result["rows"][0]
        self.assertEqual(
            row["opening_stock_amount"] + row["period_purchase_cost_amount"] + row["backfilled_amount"],
            row["fifo_sales_cost_amount"] + row["ending_fifo_inventory_amount"],
        )

    def test_negative_sale_overage_is_error(self):
        result = analyze([purchase("2026-01-01", 2, 100)], [sale("2026-01-02", -3)], [inventory(2)])
        self.assertEqual(result["rows"][0]["cost_status"], "error")
        self.assertEqual(result["errors"][0]["requested_quantity"], 3)

    def test_inventory_reconciliation_uses_inventory_date_only(self):
        result = analyze(
            [purchase("2026-01-01", 10, 100), purchase("2026-01-05", 5, 200, voucher=2, excel_row=2)],
            [sale("2026-01-02", 4), sale("2026-01-06", 2, voucher=2, excel_row=2)],
            [inventory(6)], end="2026-01-02", stock_date="2026-01-04",
        )
        self.assertEqual(result["rows"][0]["inventory_book_quantity"], 6)
        self.assertEqual(result["rows"][0]["quantity_reconciliation_status"], "match")

    def test_post_inventory_date_purchase_can_backfill_but_not_book_quantity(self):
        result = analyze([purchase("2026-01-05", 10, 100)], [sale("2026-01-01", 10)], [inventory(0)], end="2026-01-01", stock_date="2026-01-02")
        self.assertEqual(result["rows"][0]["cost_status"], "backfilled")
        self.assertEqual(result["rows"][0]["inventory_book_quantity"], -10)

    def test_inventory_prices_do_not_affect_fifo(self):
        purchases = [purchase("2026-01-01", 10, 100)]
        sales = [sale("2026-01-02", 4)]
        first = analyze(purchases, sales, [inventory(6, average_cost=1, latest_purchase_price=2)])
        second = analyze(purchases, sales, [inventory(6, average_cost=999999, latest_purchase_price=888888)])
        self.assertEqual(first["summary"]["ending_fifo_inventory_amount"], second["summary"]["ending_fifo_inventory_amount"])
        self.assertEqual(first["summary"]["fifo_sales_cost_amount"], 400)

    def test_missing_product_id_is_reported_and_not_merged(self):
        result = analyze([purchase("2026-01-01", 2, 100, product_id=None)], [sale("2026-01-02", 1, product_id=None)], [])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["summary"]["error_count"], 2)
        self.assertTrue(all(error["code"] == "missing_product_id" for error in result["errors"]))

    def test_name_and_specification_mismatch_are_warnings(self):
        result = analyze([purchase("2026-01-01", 2, 100, name="First", spec="A")], [sale("2026-01-02", 1, name="Second", spec="B")], [inventory(1)])
        codes = {warning["code"] for warning in result["warnings"]}
        self.assertEqual(codes, {"item_name_mismatch", "specification_mismatch"})

    def test_inventory_only_and_ledger_only_are_distinguished(self):
        result = analyze(
            [purchase("2026-01-01", 2, 100, product_id=1)],
            [sale("2026-01-02", 1, product_id=2)],
            [inventory(3, product_id=3)],
        )
        statuses = {row["product_id"]: row["quantity_reconciliation_status"] for row in result["rows"]}
        self.assertEqual(statuses, {1: "ledger_only", 2: "ledger_only", 3: "inventory_only"})

    def test_date_relationship_is_validated(self):
        with self.assertRaises(ValueError):
            analyze([purchase("2026-01-01", 1, 100)], [], [inventory(1)], start="2026-02-01", end="2026-01-01", stock_date="2026-01-01")


if __name__ == "__main__":
    unittest.main()
