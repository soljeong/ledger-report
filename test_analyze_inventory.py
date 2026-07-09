#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

from analyze_inventory import build_reconciliation, load_payloads


BASE_DIR = Path(__file__).resolve().parent
EXAMPLE_DIR = BASE_DIR / "examples" / "dummy_json"


class InventoryAnalysisTests(unittest.TestCase):
    def test_reconciles_purchase_sales_and_inventory_by_product_id(self) -> None:
        purchase, sales, inventory = load_payloads(EXAMPLE_DIR)
        analysis = build_reconciliation(purchase, sales, inventory)

        summary = analysis["summary"]
        self.assertEqual(summary["purchase_product_ids"], 4)
        self.assertEqual(summary["sales_product_ids"], 4)
        self.assertEqual(summary["inventory_product_ids"], 4)
        self.assertEqual(summary["sales_without_purchase_product_id_count"], 0)
        self.assertEqual(summary["purchase_without_inventory_product_id_count"], 0)
        self.assertEqual(summary["sales_without_inventory_product_id_count"], 0)
        self.assertEqual(summary["calculated_negative_stock_count"], 0)
        self.assertEqual(summary["inventory_negative_stock_count"], 0)
        self.assertEqual(summary["quantity_mismatch_count"], 0)
        self.assertEqual(summary["purchase_amount"], 1045000)
        self.assertEqual(summary["sales_amount"], 627000)
        self.assertEqual(summary["inventory_amount_at_average_cost"], 676500)
        self.assertEqual(summary["inventory_amount_at_latest_purchase_price"], 676500)
        self.assertEqual(summary["remainder_at_average_cost"], 258500)
        self.assertEqual(summary["remainder_at_latest_purchase_price"], 258500)
        self.assertEqual(analysis["calculated_negative_stock"], [])


if __name__ == "__main__":
    unittest.main()
