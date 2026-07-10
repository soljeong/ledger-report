"""Deterministic FIFO costing and stock-quantity reconciliation.

The module deliberately keeps the costing state independent from inventory-sheet
prices.  ``Decimal`` is used until the final JSON-shaped result is produced.
"""

from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable


ZERO = Decimal("0")


def dec(value: Any) -> Decimal:
    if value is None or value == "":
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"not a numeric value: {value!r}") from exc


def out_number(value: Decimal) -> int | float:
    value = value.normalize()
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def out_money(value: Decimal) -> int | float:
    rounded = value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return out_number(rounded)


def iso_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"date must be ISO YYYY-MM-DD: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date must be ISO YYYY-MM-DD: {value!r}") from exc


def voucher_sort_key(value: Any) -> tuple[int, Any]:
    if value is None or value == "":
        return (1, 1, "")
    try:
        return (0, 0, Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return (0, 1, str(value))


def event_sort_key(row: dict[str, Any], transaction_type: str) -> tuple[Any, ...]:
    return (
        iso_date(row.get("date")),
        0 if transaction_type == "purchase" else 1,
        voucher_sort_key(row.get("voucher")),
        row.get("excel_row") if row.get("excel_row") is not None else 10**12,
    )


def sale_key(row: dict[str, Any]) -> str:
    return f"{row.get('date')}|{row.get('voucher')}|{row.get('excel_row')}"


@dataclass
class Layer:
    product_id: Any
    purchase_date: date
    voucher: Any
    excel_row: Any
    original_quantity: Decimal
    remaining_quantity: Decimal
    unit_cost: Decimal
    post_period_end: bool
    order_id: int


@dataclass
class SaleState:
    product_id: Any
    row: dict[str, Any]
    original_quantity: Decimal
    active_quantity: Decimal
    allocations: list[dict[str, Any]] = field(default_factory=list)
    unresolved_quantity: Decimal = ZERO
    canceled_quantity: Decimal = ZERO
    error: bool = False


class ProductEngine:
    def __init__(self, product_id: Any) -> None:
        self.product_id = product_id
        self.layers: list[Layer] = []
        self.post_period_layers: list[Layer] = []
        self.unconfirmed: deque[SaleState] = deque()
        self.sales: list[SaleState] = []
        self.cancellations: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._layer_sequence = 0

    def _new_layer(self, row: dict[str, Any], quantity: Decimal, *, post_period_end: bool) -> Layer:
        self._layer_sequence += 1
        layer = Layer(
            product_id=self.product_id,
            purchase_date=iso_date(row["date"]),
            voucher=row.get("voucher"),
            excel_row=row.get("excel_row"),
            original_quantity=quantity,
            remaining_quantity=quantity,
            unit_cost=dec(row.get("unit_price")),
            post_period_end=post_period_end,
            order_id=self._layer_sequence,
        )
        return layer

    def _resolve_unconfirmed(self, layer: Layer, *, source: str) -> Decimal:
        used = ZERO
        for sale in list(self.unconfirmed):
            if sale.unresolved_quantity <= ZERO:
                continue
            available = min(layer.remaining_quantity, sale.unresolved_quantity)
            if available <= ZERO:
                continue
            sale.allocations.append(
                {
                    "purchase_date": layer.purchase_date.isoformat(),
                    "purchase_voucher": layer.voucher,
                    "purchase_excel_row": layer.excel_row,
                    "purchase_quantity": out_number(available),
                    "unit_cost": out_number(layer.unit_cost),
                    "cost_amount": out_money(available * layer.unit_cost),
                    "backfilled": layer.post_period_end,
                    "allocation_source": source,
                    "_quantity": available,
                    "_unit_cost": layer.unit_cost,
                }
            )
            layer.remaining_quantity -= available
            sale.unresolved_quantity -= available
            used += available
            if sale.unresolved_quantity <= ZERO:
                sale.unresolved_quantity = ZERO
        while self.unconfirmed and self.unconfirmed[0].unresolved_quantity <= ZERO:
            self.unconfirmed.popleft()
        return used

    def add_positive_purchase(self, row: dict[str, Any], *, post_period_end: bool, source: str) -> None:
        quantity = dec(row.get("quantity"))
        if quantity <= ZERO:
            raise ValueError("positive purchase required")
        layer = self._new_layer(row, quantity, post_period_end=post_period_end)
        self._resolve_unconfirmed(layer, source=source)
        if layer.remaining_quantity > ZERO:
            (self.post_period_layers if post_period_end else self.layers).append(layer)

    def cancel_purchase(self, row: dict[str, Any], *, include_post_period_layers: bool = False) -> None:
        requested = abs(dec(row.get("quantity")))
        candidate_layers = self.layers + (self.post_period_layers if include_post_period_layers else [])
        available = sum((layer.remaining_quantity for layer in candidate_layers), ZERO)
        if requested > available:
            self._error(
                "purchase_cancellation_exceeds_remaining",
                row,
                requested_quantity=out_number(requested),
                cancellable_quantity=out_number(available),
            )
            return
        remaining = requested
        allocations: list[dict[str, Any]] = []
        for layer in sorted(candidate_layers, key=lambda value: value.order_id, reverse=True):
            if remaining <= ZERO:
                break
            if layer.remaining_quantity <= ZERO:
                continue
            canceled = min(layer.remaining_quantity, remaining)
            layer.remaining_quantity -= canceled
            remaining -= canceled
            allocations.append(
                {
                    "purchase_date": layer.purchase_date.isoformat(),
                    "purchase_voucher": layer.voucher,
                    "purchase_excel_row": layer.excel_row,
                    "quantity": out_number(canceled),
                    "unit_cost": out_number(layer.unit_cost),
                    "amount": out_money(canceled * layer.unit_cost),
                }
            )
        self.cancellations.append(
            {
                "type": "purchase_cancellation",
                "product_id": self.product_id,
                "date": row.get("date"),
                "voucher": row.get("voucher"),
                "excel_row": row.get("excel_row"),
                "requested_quantity": out_number(requested),
                "allocations": allocations,
            }
        )

    def add_positive_sale(self, row: dict[str, Any]) -> SaleState:
        quantity = dec(row.get("quantity"))
        sale = SaleState(self.product_id, row, quantity, quantity)
        remaining_to_allocate = quantity
        for layer in self.layers:
            if remaining_to_allocate <= ZERO:
                break
            if layer.remaining_quantity <= ZERO:
                continue
            allocated = min(layer.remaining_quantity, remaining_to_allocate)
            layer.remaining_quantity -= allocated
            remaining_to_allocate -= allocated
            sale.allocations.append(
                {
                    "purchase_date": layer.purchase_date.isoformat(),
                    "purchase_voucher": layer.voucher,
                    "purchase_excel_row": layer.excel_row,
                    "purchase_quantity": out_number(allocated),
                    "unit_cost": out_number(layer.unit_cost),
                    "cost_amount": out_money(allocated * layer.unit_cost),
                    "backfilled": False,
                    "allocation_source": "period_fifo",
                    "_layer": layer,
                    "_quantity": allocated,
                    "_unit_cost": layer.unit_cost,
                }
            )
        sale.unresolved_quantity = remaining_to_allocate
        if sale.unresolved_quantity > ZERO:
            self.unconfirmed.append(sale)
        self.sales.append(sale)
        return sale

    def cancel_sale(self, row: dict[str, Any]) -> None:
        requested = abs(dec(row.get("quantity")))
        available = sum((sale.active_quantity for sale in self.sales), ZERO)
        if requested > available:
            self._error(
                "sales_cancellation_exceeds_previous_sales",
                row,
                requested_quantity=out_number(requested),
                cancellable_quantity=out_number(available),
            )
            return
        remaining = requested
        cancellations: list[dict[str, Any]] = []
        for sale in reversed(self.sales):
            if remaining <= ZERO:
                break
            cancel_amount = min(sale.active_quantity, remaining)
            if cancel_amount <= ZERO:
                continue

            unresolved_cancel = min(sale.unresolved_quantity, cancel_amount)
            sale.unresolved_quantity -= unresolved_cancel
            left = cancel_amount - unresolved_cancel
            restored: list[dict[str, Any]] = []
            for allocation in reversed(sale.allocations):
                if left <= ZERO:
                    break
                allocated = allocation.get("_quantity", ZERO)
                if allocated <= ZERO:
                    continue
                restore = min(allocated, left)
                allocation["_quantity"] = allocated - restore
                layer = allocation.get("_layer")
                if layer is not None:
                    layer.remaining_quantity += restore
                left -= restore
                restored.append(
                    {
                        "purchase_date": allocation["purchase_date"],
                        "purchase_voucher": allocation["purchase_voucher"],
                        "purchase_excel_row": allocation["purchase_excel_row"],
                        "quantity": out_number(restore),
                        "unit_cost": allocation["unit_cost"],
                    }
                )
            sale.active_quantity -= cancel_amount
            sale.canceled_quantity += cancel_amount
            remaining -= cancel_amount
            cancellations.append(
                {
                    "sale_id": sale_key(sale.row),
                    "quantity": out_number(cancel_amount),
                    "unconfirmed_quantity": out_number(unresolved_cancel),
                    "restored_allocations": restored,
                }
            )
        self.cancellations.append(
            {
                "type": "sales_cancellation",
                "product_id": self.product_id,
                "date": row.get("date"),
                "voucher": row.get("voucher"),
                "excel_row": row.get("excel_row"),
                "requested_quantity": out_number(requested),
                "sales": cancellations,
            }
        )

    def process(self, row: dict[str, Any], transaction_type: str, *, post_period_end: bool = False, source: str = "period", include_post_period_layers: bool = False) -> None:
        quantity = dec(row.get("quantity"))
        if quantity == ZERO:
            return
        if transaction_type == "purchase":
            if quantity > ZERO:
                self.add_positive_purchase(row, post_period_end=post_period_end, source=source)
            else:
                self.cancel_purchase(row, include_post_period_layers=include_post_period_layers)
        else:
            if quantity > ZERO:
                self.add_positive_sale(row)
            else:
                self.cancel_sale(row)

    def _error(self, code: str, row: dict[str, Any], **details: Any) -> None:
        self.errors.append(
            {
                "code": code,
                "product_id": self.product_id,
                "date": row.get("date"),
                "voucher": row.get("voucher"),
                "excel_row": row.get("excel_row"),
                **details,
            }
        )


def _clean_allocation(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def _sale_output(sale: SaleState, period_start: date, period_end: date) -> dict[str, Any]:
    row = sale.row
    allocations: list[dict[str, Any]] = []
    cost = ZERO
    backfilled = ZERO
    for value in sale.allocations:
        quantity = value.get("_quantity", dec(value.get("purchase_quantity")))
        if quantity <= ZERO:
            continue
        output = _clean_allocation(value)
        output["purchase_quantity"] = out_number(quantity)
        unit_cost = value.get("_unit_cost", dec(value["unit_cost"]))
        output["cost_amount"] = out_money(quantity * unit_cost)
        allocations.append(output)
        cost += quantity * unit_cost
        if value.get("backfilled"):
            backfilled += quantity
    status = "unconfirmed" if sale.unresolved_quantity > ZERO else ("backfilled" if backfilled > ZERO else "confirmed")
    if sale.error:
        status = "error"
    sale_date = iso_date(row["date"])
    return {
        "sale_id": sale_key(row),
        "product_id": sale.product_id,
        "date": row.get("date"),
        "voucher": row.get("voucher"),
        "excel_row": row.get("excel_row"),
        "company": row.get("company"),
        "item_name": row.get("item_name"),
        "specification": row.get("specification"),
        "sale_quantity": out_number(sale.original_quantity),
        "effective_quantity": out_number(sale.active_quantity),
        "canceled_quantity": out_number(sale.canceled_quantity),
        "allocations": allocations,
        "fifo_cost_amount": out_money(cost),
        "unconfirmed_quantity": out_number(sale.unresolved_quantity),
        "backfilled_quantity": out_number(backfilled),
        "_fifo_cost_decimal": cost,
        "_backfilled_amount_decimal": sum(
            (
                value.get("_quantity", dec(value.get("purchase_quantity"))) * value.get("_unit_cost", dec(value["unit_cost"]))
                for value in sale.allocations
                if value.get("backfilled") and value.get("_quantity", dec(value.get("purchase_quantity"))) > ZERO
            ),
            ZERO,
        ),
        "cost_status": status,
        "in_analysis_period": period_start <= sale_date <= period_end,
    }


def _record_error(source: str, row: dict[str, Any], code: str, **details: Any) -> dict[str, Any]:
    return {
        "code": code,
        "source": source,
        "product_id": row.get("product_id"),
        "date": row.get("date"),
        "voucher": row.get("voucher"),
        "excel_row": row.get("excel_row"),
        **details,
    }


def calculate_fifo(
    purchase_records: Iterable[dict[str, Any]],
    sales_records: Iterable[dict[str, Any]],
    inventory_records: Iterable[dict[str, Any]],
    period_start: str | date,
    period_end: str | date,
    inventory_date: str | date,
) -> dict[str, Any]:
    start = iso_date(period_start)
    end = iso_date(period_end)
    stock_date = iso_date(inventory_date)
    if not start <= end <= stock_date:
        raise ValueError("date relationship must satisfy period_start <= period_end <= inventory_date")

    purchases = list(purchase_records)
    sales = list(sales_records)
    inventory = list(inventory_records)
    all_rows: list[tuple[dict[str, Any], str]] = [(row, "purchase") for row in purchases] + [(row, "sales") for row in sales]
    errors: list[dict[str, Any]] = []
    valid_events: list[tuple[dict[str, Any], str]] = []
    product_ids: set[Any] = set()
    ledger_product_ids: set[Any] = set()
    names: dict[Any, set[str]] = defaultdict(set)
    specifications: dict[Any, set[str]] = defaultdict(set)

    for row, kind in all_rows:
        product_id = row.get("product_id")
        try:
            row_date = iso_date(row.get("date"))
            quantity = dec(row.get("quantity"))
            if row.get("quantity") is None:
                raise ValueError("quantity is required")
            if kind == "purchase" and row.get("unit_price") is None:
                raise ValueError("purchase unit_price is required")
        except ValueError as exc:
            errors.append(_record_error(kind, row, "invalid_record", reason=str(exc)))
            continue
        if product_id is None or product_id == "":
            errors.append(_record_error(kind, row, "missing_product_id", reason="record excluded from FIFO and quantity calculations"))
            continue
        product_ids.add(product_id)
        ledger_product_ids.add(product_id)
        if row.get("item_name"):
            names[product_id].add(str(row["item_name"]))
        if row.get("specification"):
            specifications[product_id].add(str(row["specification"]))
        valid_events.append((row, kind))

    for row in inventory:
        product_id = row.get("product_id")
        if product_id is None or product_id == "":
            errors.append(_record_error("inventory", row, "missing_product_id", reason="inventory row excluded from reconciliation"))
            continue
        product_ids.add(product_id)
        if row.get("item_name"):
            names[product_id].add(str(row["item_name"]))
        if row.get("specification"):
            specifications[product_id].add(str(row["specification"]))

    warnings: list[dict[str, Any]] = []
    for product_id in sorted(product_ids, key=str):
        if len(names[product_id]) > 1:
            warnings.append({"code": "item_name_mismatch", "product_id": product_id, "values": sorted(names[product_id])})
        if len(specifications[product_id]) > 1:
            warnings.append({"code": "specification_mismatch", "product_id": product_id, "values": sorted(specifications[product_id])})

    events = sorted(valid_events, key=lambda item: event_sort_key(item[0], item[1]))
    engines = {product_id: ProductEngine(product_id) for product_id in product_ids}
    pre_events = [(row, kind) for row, kind in events if iso_date(row["date"]) < start]
    period_events = [(row, kind) for row, kind in events if start <= iso_date(row["date"]) <= end]
    for row, kind in pre_events:
        engines[row["product_id"]].process(row, kind)

    opening_layers = {
        product_id: deepcopy(engine.layers)
        for product_id, engine in engines.items()
    }
    for row, kind in period_events:
        engines[row["product_id"]].process(row, kind)

    # Only purchases after the analysis end are used in this second pass.  Sales
    # after the end never consume these layers and therefore cannot compete with
    # the backfill of analysis-period shortages.
    backfill_rows = [row for row, kind in events if kind == "purchase" and iso_date(row["date"]) > end]
    for row in backfill_rows:
        engines[row["product_id"]].process(
            row,
            "purchase",
            post_period_end=True,
            source="backfill",
            include_post_period_layers=True,
        )

    inventory_quantities: dict[Any, Decimal] = defaultdict(lambda: ZERO)
    for row, kind in events:
        if iso_date(row["date"]) > stock_date:
            continue
        quantity = dec(row.get("quantity"))
        if kind == "purchase":
            inventory_quantities[row["product_id"]] += quantity
        else:
            inventory_quantities[row["product_id"]] -= quantity

    inventory_by_product: dict[Any, Decimal] = defaultdict(lambda: ZERO)
    for row in inventory:
        if row.get("product_id") in product_ids:
            inventory_by_product[row["product_id"]] += dec(row.get("stock_quantity"))

    period_purchase_rows = [
        row for row, kind in period_events if kind == "purchase"
    ]
    period_sales_rows = [row for row, kind in period_events if kind == "sales"]
    sale_outputs: list[dict[str, Any]] = []
    for engine in engines.values():
        sale_outputs.extend(_sale_output(sale, start, end) for sale in engine.sales)
    sale_outputs.sort(key=lambda row: (row["date"], voucher_sort_key(row["voucher"]), row["excel_row"] or 10**12))

    sale_by_product: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in sale_outputs:
        sale_by_product[row["product_id"]].append(row)

    rows: list[dict[str, Any]] = []
    unconfirmed: list[dict[str, Any]] = []
    backfill_allocations: list[dict[str, Any]] = []
    for product_id in sorted(product_ids, key=str):
        engine = engines[product_id]
        product_purchases = [row for row in period_purchase_rows if row["product_id"] == product_id]
        product_sales = [row for row in period_sales_rows if row["product_id"] == product_id]
        opening_qty = sum((dec(row.get("quantity")) for row, kind in pre_events if row["product_id"] == product_id and kind == "purchase"), ZERO) - sum((dec(row.get("quantity")) for row, kind in pre_events if row["product_id"] == product_id and kind == "sales"), ZERO)
        opening_amount = sum((layer.remaining_quantity * layer.unit_cost for layer in opening_layers[product_id]), ZERO)
        period_purchase_quantity = sum((dec(row.get("quantity")) for row in product_purchases), ZERO)
        period_purchase_cost = sum((dec(row.get("quantity")) * dec(row.get("unit_price")) for row in product_purchases), ZERO)
        period_sales_quantity = sum((dec(row.get("quantity")) for row in product_sales), ZERO)
        period_sales_amount = sum((dec(row.get("total_amount")) for row in product_sales), ZERO)
        product_sale_outputs = sale_by_product[product_id]
        period_sale_outputs = [row for row in product_sale_outputs if row["in_analysis_period"]]
        fifo_cost = sum((row["_fifo_cost_decimal"] for row in period_sale_outputs), ZERO)
        backfilled_qty = sum((dec(row["backfilled_quantity"]) for row in product_sale_outputs), ZERO)
        backfilled_amount = sum(
            (
                sale["_backfilled_amount_decimal"]
                for sale in product_sale_outputs
                if sale["_backfilled_amount_decimal"] > ZERO
            ),
            ZERO,
        )
        unconfirmed_qty = sum((dec(row["unconfirmed_quantity"]) for row in product_sale_outputs), ZERO)
        period_end_layers = [layer for layer in engine.layers if layer.remaining_quantity > ZERO]
        normal_qty = sum((layer.remaining_quantity for layer in period_end_layers), ZERO)
        normal_amount = sum((layer.remaining_quantity * layer.unit_cost for layer in period_end_layers), ZERO)
        signed_end_qty = sum((dec(row.get("quantity")) for row, kind in pre_events + period_events if row["product_id"] == product_id and kind == "purchase"), ZERO) - sum((dec(row.get("quantity")) for row, kind in pre_events + period_events if row["product_id"] == product_id and kind == "sales"), ZERO)
        negative_end_qty = max(-signed_end_qty, ZERO)
        status_values = {row["cost_status"] for row in product_sale_outputs if dec(row["effective_quantity"]) > ZERO}
        if any(error["product_id"] == product_id for error in engine.errors):
            status = "error"
        elif unconfirmed_qty > ZERO:
            status = "unconfirmed"
        elif "backfilled" in status_values or backfilled_qty > ZERO:
            status = "backfilled"
        else:
            status = "confirmed"
        inv_qty = inventory_by_product.get(product_id)
        book_qty = inventory_quantities.get(product_id, ZERO)
        diff = inv_qty - book_qty if product_id in inventory_by_product else None
        if product_id not in ledger_product_ids:
            recon_status = "inventory_only"
        elif inv_qty is None:
            recon_status = "ledger_only"
        elif inv_qty < ZERO:
            recon_status = "inventory_negative_stock"
        elif diff == ZERO:
            recon_status = "match"
        elif diff > ZERO:
            recon_status = "inventory_more"
        else:
            recon_status = "ledger_more"
        layer_output = [
            {
                "product_id": layer.product_id,
                "purchase_date": layer.purchase_date.isoformat(),
                "voucher": layer.voucher,
                "excel_row": layer.excel_row,
                "original_quantity": out_number(layer.original_quantity),
                "remaining_quantity": out_number(layer.remaining_quantity),
                "unit_cost": out_number(layer.unit_cost),
                "post_period_end": layer.post_period_end,
                "remaining_amount": out_money(layer.remaining_quantity * layer.unit_cost),
            }
            for layer in period_end_layers
        ]
        row = {
            "product_id": product_id,
            "item_name": sorted(names[product_id])[0] if names[product_id] else None,
            "item_names": sorted(names[product_id]),
            "specification": sorted(specifications[product_id])[0] if specifications[product_id] else None,
            "specifications": sorted(specifications[product_id]),
            "opening_stock_quantity": out_number(opening_qty),
            "opening_stock_amount": out_money(opening_amount),
            "period_purchase_quantity": out_number(period_purchase_quantity),
            "period_purchase_cost_amount": out_money(period_purchase_cost),
            "period_sales_quantity": out_number(period_sales_quantity),
            "period_sales_amount": out_money(period_sales_amount),
            "fifo_sales_cost_amount": out_money(fifo_cost),
            "gross_profit": out_money(period_sales_amount - fifo_cost),
            "ending_signed_stock_quantity": out_number(signed_end_qty),
            "ending_normal_stock_quantity": out_number(normal_qty),
            "ending_fifo_inventory_amount": out_money(normal_amount),
            "ending_negative_stock_quantity": out_number(negative_end_qty),
            "backfilled_quantity": out_number(backfilled_qty),
            "backfilled_amount": out_money(backfilled_amount),
            "unconfirmed_quantity": out_number(unconfirmed_qty),
            "cost_status": status,
            "inventory_book_quantity": out_number(book_qty),
            "inventory_sheet_quantity": out_number(inv_qty) if inv_qty is not None else None,
            "inventory_quantity_difference": out_number(diff) if diff is not None else None,
            "quantity_reconciliation_status": recon_status,
            "remaining_cost_layers": layer_output,
            # Compatibility aliases for consumers of the original report JSON.
            "purchase_quantity": out_number(period_purchase_quantity),
            "sales_quantity": out_number(period_sales_quantity),
            "calculated_stock_quantity": out_number(book_qty),
            "inventory_quantity": out_number(inv_qty) if inv_qty is not None else None,
            "quantity_difference": out_number(diff) if diff is not None else None,
            "purchase_amount": out_money(sum((dec(row.get("total_amount")) for row in product_purchases), ZERO)),
            "sales_amount": out_money(period_sales_amount),
        }
        rows.append(row)
        for sale in product_sale_outputs:
            if sale["unconfirmed_quantity"]:
                unconfirmed.append(sale)
            for allocation in sale["allocations"]:
                if allocation.get("backfilled"):
                    backfill_allocations.append({"sale_id": sale["sale_id"], "product_id": product_id, **allocation})

    status_counts = {status: sum(row["cost_status"] == status for row in rows) for status in ("confirmed", "backfilled", "unconfirmed", "error")}
    recon_counts = {status: sum(row["quantity_reconciliation_status"] == status for row in rows) for status in ("match", "inventory_more", "ledger_more", "ledger_only", "inventory_only", "inventory_negative_stock")}
    all_purchase_dates = [iso_date(row["date"]) for row in purchases if row.get("product_id") not in (None, "") and row.get("date")]
    all_transaction_dates = [iso_date(row["date"]) for row, _ in valid_events]
    used_backfill_dates = [iso_date(row["purchase_date"]) for row in backfill_allocations]
    summary = {
        "opening_stock_quantity": out_number(sum((dec(row["opening_stock_quantity"]) for row in rows), ZERO)),
        "opening_stock_amount": out_money(sum((dec(row["opening_stock_amount"]) for row in rows), ZERO)),
        "period_purchase_quantity": out_number(sum((dec(row["period_purchase_quantity"]) for row in rows), ZERO)),
        "period_purchase_cost_amount": out_money(sum((dec(row["period_purchase_cost_amount"]) for row in rows), ZERO)),
        "period_sales_quantity": out_number(sum((dec(row["period_sales_quantity"]) for row in rows), ZERO)),
        "period_sales_amount": out_money(sum((dec(row["period_sales_amount"]) for row in rows), ZERO)),
        "fifo_sales_cost_amount": out_money(sum((dec(row["fifo_sales_cost_amount"]) for row in rows), ZERO)),
        "gross_profit": out_money(sum((dec(row["gross_profit"]) for row in rows), ZERO)),
        "ending_signed_stock_quantity": out_number(sum((dec(row["ending_signed_stock_quantity"]) for row in rows), ZERO)),
        "ending_normal_stock_quantity": out_number(sum((dec(row["ending_normal_stock_quantity"]) for row in rows), ZERO)),
        "ending_fifo_inventory_amount": out_money(sum((dec(row["ending_fifo_inventory_amount"]) for row in rows), ZERO)),
        "ending_negative_stock_quantity": out_number(sum((dec(row["ending_negative_stock_quantity"]) for row in rows), ZERO)),
        "backfilled_quantity": out_number(sum((dec(row["backfilled_quantity"]) for row in rows), ZERO)),
        "backfilled_amount": out_money(sum((dec(row["backfilled_amount"]) for row in rows), ZERO)),
        "unconfirmed_quantity": out_number(sum((dec(row["unconfirmed_quantity"]) for row in rows), ZERO)),
        "cost_status_counts": status_counts,
        "status_counts": status_counts,
        "quantity_reconciliation_mismatch_count": sum(row["quantity_reconciliation_status"] not in ("match",) for row in rows),
        "inventory_reconciliation_mismatch_count": sum(row["quantity_reconciliation_status"] not in ("match",) for row in rows),
        "error_count": len(errors) + sum(len(engine.errors) for engine in engines.values()),
        "reconciliation_status_counts": recon_counts,
        # Compatibility names used by the original report.
        "purchase_amount": out_money(sum((dec(row["purchase_amount"]) for row in rows), ZERO)),
        "sales_amount": out_money(sum((dec(row["sales_amount"]) for row in rows), ZERO)),
        "inventory_amount_at_fifo": out_money(sum((dec(row["ending_fifo_inventory_amount"]) for row in rows), ZERO)),
        "remainder_at_fifo": out_money(sum((dec(row["gross_profit"]) for row in rows), ZERO)),
        "inventory_amount_at_average_cost": None,
        "inventory_amount_at_latest_purchase_price": None,
        "remainder_at_average_cost": None,
        "remainder_at_latest_purchase_price": None,
    }
    engine_errors = [error for engine in engines.values() for error in engine.errors]
    public_sale_outputs = [
        {key: value for key, value in sale.items() if not key.startswith("_")}
        for sale in sale_outputs
    ]
    public_unconfirmed = [
        {key: value for key, value in sale.items() if not key.startswith("_")}
        for sale in unconfirmed
    ]
    return {
        "metadata": {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "inventory_date": stock_date.isoformat(),
            "analysis_start_date": start.isoformat(),
            "analysis_end_date": end.isoformat(),
            "inventory_reference_date": stock_date.isoformat(),
            "input_data_first_transaction_date": min(all_transaction_dates).isoformat() if all_transaction_dates else None,
            "input_data_last_transaction_date": max(all_transaction_dates).isoformat() if all_transaction_dates else None,
            "input_data_last_purchase_date": max(all_purchase_dates).isoformat() if all_purchase_dates else None,
            "input_first_transaction_date": min(all_transaction_dates).isoformat() if all_transaction_dates else None,
            "input_last_transaction_date": max(all_transaction_dates).isoformat() if all_transaction_dates else None,
            "input_last_purchase_date": max(all_purchase_dates).isoformat() if all_purchase_dates else None,
            "backfill_last_purchase_date": max(used_backfill_dates).isoformat() if used_backfill_dates else None,
            "costing_method": "FIFO",
            "inventory_unit_costs_used": False,
            "inventory_price_fields_ignored": ["average_cost", "latest_purchase_price", "sales_price"],
        },
        "summary": summary,
        "rows": rows,
        "sales_allocations": public_sale_outputs,
        "unconfirmed_shipments": public_unconfirmed,
        "backfill_allocations": backfill_allocations,
        "cancellation_events": [event for engine in engines.values() for event in engine.cancellations],
        "errors": errors + engine_errors,
        "warnings": warnings,
        "calculated_negative_stock": [row for row in rows if dec(row["ending_signed_stock_quantity"]) < ZERO],
        "inventory_negative_stock": [row for row in rows if row["quantity_reconciliation_status"] == "inventory_negative_stock"],
        "quantity_mismatches": [row for row in rows if row["quantity_reconciliation_status"] not in ("match", "ledger_only")],
    }
