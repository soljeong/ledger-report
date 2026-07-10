"""Deterministic FIFO costing and dated stock-quantity reconciliation.

Costing deliberately has two states: the immutable analysis-end snapshot and
the continuation used solely to price shortages with later purchases.
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
    return int(value) if value == value.to_integral_value() else float(value)


def out_money(value: Decimal) -> int | float:
    return out_number(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def exact_money(value: Decimal) -> str:
    """JSON-safe Decimal representation used for all cost-event aggregation."""
    return format(value, "f")


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


def event_sort_key(row: dict[str, Any], kind: str) -> tuple[Any, ...]:
    return (iso_date(row["date"]), 0 if kind == "purchase" else 1,
            voucher_sort_key(row.get("voucher")), row.get("excel_row") or 10**12)


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
    layer_id: str
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
    event: dict[str, Any] = field(default_factory=dict)


def layer_to_row(layer: Layer) -> dict[str, Any]:
    """Return the trace fields needed when a layer creates a later event."""
    return {
        "date": layer.purchase_date.isoformat(),
        "voucher": layer.voucher,
        "excel_row": layer.excel_row,
    }


class ProductEngine:
    """FIFO state for a single product, retaining zero-quantity layers."""
    def __init__(self, product_id: Any) -> None:
        self.product_id = product_id
        self.layers: list[Layer] = []
        self.post_period_layers: list[Layer] = []
        self.unconfirmed: deque[SaleState] = deque()
        self.sales: list[SaleState] = []
        self.cancellations: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self.purchase_cost_events: list[dict[str, Any]] = []
        self.sales_cost_events: list[dict[str, Any]] = []
        self.unconfirmed_quantity_events: list[dict[str, Any]] = []
        # These events are deliberately separate from sale COGS events.  A
        # later purchase can settle an older shortage on the purchase date,
        # which changes stock value even though it is not new-period COGS.
        self.inventory_cost_events: list[dict[str, Any]] = []
        self._layer_sequence = 0

    def _new_layer(self, row: dict[str, Any], quantity: Decimal, post: bool) -> Layer:
        self._layer_sequence += 1
        return Layer(self.product_id, iso_date(row["date"]), row.get("voucher"), row.get("excel_row"),
                     quantity, quantity, dec(row.get("unit_price")), post,
                     f"{self.product_id}|{row.get('date')}|{row.get('voucher')}|{row.get('excel_row')}|{self._layer_sequence}",
                     self._layer_sequence)

    @staticmethod
    def _allocation(layer: Layer, quantity: Decimal, *, source: str) -> dict[str, Any]:
        cost = quantity * layer.unit_cost
        return {
            "layer_id": layer.layer_id, "purchase_date": layer.purchase_date.isoformat(),
            "purchase_voucher": layer.voucher, "purchase_excel_row": layer.excel_row,
            "purchase_quantity": out_number(quantity), "unit_cost": out_number(layer.unit_cost),
            "cost_amount": out_money(cost), "cost_amount_exact": exact_money(cost), "backfilled": layer.post_period_end,
            "allocation_source": source, "_layer": layer, "_quantity": quantity, "_unit_cost": layer.unit_cost,
        }

    def _cost_event(self, row: dict[str, Any], quantity: Decimal, cost: Decimal, event_type: str, **extra: Any) -> dict[str, Any]:
        return {"transaction_id": sale_key(row), "date": row.get("date"), "voucher": row.get("voucher"),
                "excel_row": row.get("excel_row"), "product_id": self.product_id,
                "quantity": out_number(quantity), "cost_amount": out_money(cost), "cost_amount_exact": exact_money(cost), "_cost_decimal": cost,
                "event_type": event_type, **extra}

    def _resolve_unconfirmed(self, layer: Layer, source: str) -> None:
        for sale in list(self.unconfirmed):
            if layer.remaining_quantity <= ZERO:
                break
            quantity = min(layer.remaining_quantity, sale.unresolved_quantity)
            if quantity <= ZERO:
                continue
            allocation = self._allocation(layer, quantity, source=source)
            sale.allocations.append(allocation)
            sale.event.setdefault("cost_allocations", []).append(dict(allocation))
            layer.remaining_quantity -= quantity
            sale.unresolved_quantity -= quantity
            sale.event["cost_amount"] = out_money(dec(sale.event.get("_cost_decimal")) + quantity * layer.unit_cost)
            sale.event["_cost_decimal"] = dec(sale.event.get("_cost_decimal")) + quantity * layer.unit_cost
            sale.event["cost_amount_exact"] = exact_money(sale.event["_cost_decimal"])
            # The original sale event remains an immutable accounting delta.
            # Its current state is published separately for traceability.
            sale.event["current_unconfirmed_quantity"] = out_number(sale.unresolved_quantity)
            self.unconfirmed_quantity_events.append(self._cost_event(
                layer_to_row(layer), quantity, ZERO, "shortage_backfill",
                sale_date=iso_date(sale.row["date"]).isoformat(),
                sale_transaction_id=sale_key(sale.row),
                unconfirmed_quantity_delta=out_number(-quantity),
                current_unconfirmed_quantity=out_number(sale.unresolved_quantity),
                settlement_scope="current_period" if source == "period" else source,
            ))
            if not layer.post_period_end:
                sale_date = iso_date(sale.row["date"])
                event_type = "shortage_backfill_consumption"
                self.inventory_cost_events.append(
                    self._cost_event(
                        layer_to_row(layer), -quantity, -(quantity * layer.unit_cost), event_type,
                        sale_date=sale_date.isoformat(), sale_transaction_id=sale_key(sale.row),
                        settlement_scope="current_period" if source == "period" else source,
                    )
                )
        while self.unconfirmed and self.unconfirmed[0].unresolved_quantity <= ZERO:
            self.unconfirmed.popleft()

    def add_positive_purchase(self, row: dict[str, Any], *, post: bool, source: str) -> None:
        quantity = dec(row["quantity"])
        layer = self._new_layer(row, quantity, post)
        self.layers.append(layer)  # retain even fully allocated layers for later sales reversal
        if post:
            self.post_period_layers.append(layer)
        self.purchase_cost_events.append(self._cost_event(row, quantity, quantity * layer.unit_cost, "purchase", layer_id=layer.layer_id, post_period_end=post))
        if not post:
            self.inventory_cost_events.append(self._cost_event(row, quantity, quantity * layer.unit_cost, "purchase", layer_id=layer.layer_id))
        self._resolve_unconfirmed(layer, source)

    def cancel_purchase(self, row: dict[str, Any], *, post_only: bool) -> None:
        requested = abs(dec(row["quantity"]))
        candidates = self.post_period_layers if post_only else self.layers
        available = sum((layer.remaining_quantity for layer in candidates), ZERO)
        if requested > available:
            self._error("purchase_cancellation_exceeds_remaining", row, requested_quantity=out_number(requested), cancellable_quantity=out_number(available))
            return
        remaining, cost, allocations = requested, ZERO, []
        for layer in sorted(candidates, key=lambda value: value.order_id, reverse=True):
            if remaining <= ZERO:
                break
            quantity = min(layer.remaining_quantity, remaining)
            if quantity <= ZERO:
                continue
            layer.remaining_quantity -= quantity
            remaining -= quantity
            cost += quantity * layer.unit_cost
            amount = quantity * layer.unit_cost
            allocations.append({"layer_id": layer.layer_id, "purchase_date": layer.purchase_date.isoformat(), "purchase_voucher": layer.voucher, "purchase_excel_row": layer.excel_row, "quantity": out_number(quantity), "unit_cost": out_number(layer.unit_cost), "amount": out_money(amount), "amount_exact": exact_money(amount)})
        self.purchase_cost_events.append(self._cost_event(row, -requested, -cost, "purchase_cancellation", allocations=allocations, post_period_end=post_only))
        if not post_only:
            self.inventory_cost_events.append(self._cost_event(row, -requested, -cost, "purchase_cancellation", allocations=allocations))
        self.cancellations.append({"type": "purchase_cancellation", "product_id": self.product_id, "date": row.get("date"), "voucher": row.get("voucher"), "excel_row": row.get("excel_row"), "requested_quantity": out_number(requested), "allocations": allocations})

    def add_positive_sale(self, row: dict[str, Any]) -> None:
        quantity, remaining = dec(row["quantity"]), dec(row["quantity"])
        sale = SaleState(self.product_id, row, quantity, quantity)
        for layer in self.layers:
            if remaining <= ZERO:
                break
            allocated = min(layer.remaining_quantity, remaining)
            if allocated <= ZERO:
                continue
            layer.remaining_quantity -= allocated
            remaining -= allocated
            sale.allocations.append(self._allocation(layer, allocated, source="period_fifo"))
            if not layer.post_period_end:
                self.inventory_cost_events.append(
                    self._cost_event(row, -allocated, -(allocated * layer.unit_cost), "sale_inventory_outflow", layer_id=layer.layer_id)
                )
        sale.unresolved_quantity = remaining
        cost = sum((value["_quantity"] * value["_unit_cost"] for value in sale.allocations), ZERO)
        sale.event = self._cost_event(row, quantity, cost, "sale", unconfirmed_quantity_delta=out_number(remaining), current_unconfirmed_quantity=out_number(remaining), inventory_cost_amount=out_money(cost), inventory_cost_amount_exact=exact_money(cost), cost_allocations=[dict(value) for value in sale.allocations])
        self.sales_cost_events.append(sale.event)
        self.unconfirmed_quantity_events.append(sale.event)
        self.sales.append(sale)
        if remaining > ZERO:
            self.unconfirmed.append(sale)

    def cancel_sale(self, row: dict[str, Any]) -> None:
        requested = abs(dec(row["quantity"]))
        available = sum((sale.active_quantity for sale in self.sales), ZERO)
        if requested > available:
            self._error("sales_cancellation_exceeds_previous_sales", row, requested_quantity=out_number(requested), cancellable_quantity=out_number(available))
            return
        remaining, restored_cost, restored_inventory_cost, unconfirmed_cancel, details = requested, ZERO, ZERO, ZERO, []
        for sale in reversed(self.sales):
            if remaining <= ZERO:
                break
            cancel = min(sale.active_quantity, remaining)
            unresolved = min(sale.unresolved_quantity, cancel)
            sale.unresolved_quantity -= unresolved
            unconfirmed_cancel += unresolved
            left, restored = cancel - unresolved, []
            for allocation in reversed(sale.allocations):
                if left <= ZERO:
                    break
                quantity = min(allocation["_quantity"], left)
                if quantity <= ZERO:
                    continue
                allocation["_quantity"] -= quantity
                allocation["purchase_quantity"] = out_number(allocation["_quantity"])
                allocation["cost_amount"] = out_money(allocation["_quantity"] * allocation["_unit_cost"])
                allocation["cost_amount_exact"] = exact_money(allocation["_quantity"] * allocation["_unit_cost"])
                allocation["_layer"].remaining_quantity += quantity
                amount = quantity * allocation["_unit_cost"]
                restored_cost += amount
                if not allocation["backfilled"]:
                    restored_inventory_cost += amount
                    self.inventory_cost_events.append(
                        self._cost_event(row, quantity, amount, "sale_cancellation_restore", layer_id=allocation["layer_id"], original_sale_id=sale_key(sale.row))
                    )
                left -= quantity
                restored.append({"layer_id": allocation["layer_id"], "purchase_date": allocation["purchase_date"], "purchase_voucher": allocation["purchase_voucher"], "purchase_excel_row": allocation["purchase_excel_row"], "quantity": out_number(quantity), "unit_cost": allocation["unit_cost"], "amount": out_money(amount), "amount_exact": exact_money(amount)})
            sale.active_quantity -= cancel
            sale.canceled_quantity += cancel
            remaining -= cancel
            details.append({"sale_id": sale_key(sale.row), "quantity": out_number(cancel), "unconfirmed_quantity": out_number(unresolved), "restored_allocations": restored})
        event = self._cost_event(row, -requested, -restored_cost, "sale_cancellation", unconfirmed_quantity_delta=out_number(-unconfirmed_cancel), current_unconfirmed_quantity=out_number(sum((sale.unresolved_quantity for sale in self.sales), ZERO)), inventory_cost_amount=out_money(restored_inventory_cost), inventory_cost_amount_exact=exact_money(restored_inventory_cost), restoration_details=details)
        self.sales_cost_events.append(event)
        self.unconfirmed_quantity_events.append(event)
        self.cancellations.append({"type": "sales_cancellation", "product_id": self.product_id, "date": row.get("date"), "voucher": row.get("voucher"), "excel_row": row.get("excel_row"), "requested_quantity": out_number(requested), "sales": details})

    def process(self, row: dict[str, Any], kind: str, *, post: bool = False, source: str = "period") -> None:
        quantity = dec(row["quantity"])
        if quantity == ZERO:
            return
        if kind == "purchase":
            if quantity > ZERO:
                self.add_positive_purchase(row, post=post, source=source)
            else:
                self.cancel_purchase(row, post_only=post)
        elif quantity > ZERO:
            self.add_positive_sale(row)
        else:
            self.cancel_sale(row)

    def _error(self, code: str, row: dict[str, Any], **details: Any) -> None:
        self.errors.append({"code": code, "source": "purchase" if "purchase" in code else "sales", "product_id": self.product_id, "date": row.get("date"), "voucher": row.get("voucher"), "excel_row": row.get("excel_row"), "affects_quantity": True, "affects_fifo_cost": True, "affects_revenue": True, "affects_vat": True, "affects_inventory_reconciliation": True, **details})


def _clean(value: dict[str, Any]) -> dict[str, Any]:
    def clean_item(item: Any) -> Any:
        if isinstance(item, dict):
            return {key: clean_item(child) for key, child in item.items() if not key.startswith("_")}
        if isinstance(item, list):
            return [clean_item(child) for child in item]
        return item
    return clean_item(value)


def _sale_output(sale: SaleState, start: date, end: date) -> dict[str, Any]:
    allocations, cost, backfill_qty, backfill_amount = [], ZERO, ZERO, ZERO
    for value in sale.allocations:
        quantity = value["_quantity"]
        if quantity <= ZERO:
            continue
        output = _clean(value)
        output["purchase_quantity"] = out_number(quantity)
        output["cost_amount"] = out_money(quantity * value["_unit_cost"])
        output["cost_amount_exact"] = exact_money(quantity * value["_unit_cost"])
        allocations.append(output)
        cost += quantity * value["_unit_cost"]
        if value["backfilled"]:
            backfill_qty += quantity
            backfill_amount += quantity * value["_unit_cost"]
    sale_date = iso_date(sale.row["date"])
    return {"sale_id": sale_key(sale.row), "product_id": sale.product_id, "date": sale.row.get("date"), "voucher": sale.row.get("voucher"), "excel_row": sale.row.get("excel_row"), "company": sale.row.get("company"), "item_name": sale.row.get("item_name"), "specification": sale.row.get("specification"), "sale_quantity": out_number(sale.original_quantity), "effective_quantity": out_number(sale.active_quantity), "canceled_quantity": out_number(sale.canceled_quantity), "allocations": allocations, "fifo_cost_amount": out_money(cost), "fifo_cost_amount_exact": exact_money(cost), "unconfirmed_quantity": out_number(sale.unresolved_quantity), "backfilled_quantity": out_number(backfill_qty), "backfilled_amount": out_money(backfill_amount), "backfilled_amount_exact": exact_money(backfill_amount), "cost_status": "unconfirmed" if sale.unresolved_quantity > ZERO else ("backfilled" if backfill_qty > ZERO else "confirmed"), "in_analysis_period": start <= sale_date <= end}


def _record_error(source: str, row: dict[str, Any], code: str, **details: Any) -> dict[str, Any]:
    scopes = details.pop("affects", None)
    if scopes is None:
        scopes = {
            "affects_quantity": source in {"purchase", "sales"},
            "affects_fifo_cost": source in {"purchase", "sales"},
            "affects_revenue": source == "sales",
            "affects_vat": source in {"purchase", "sales"},
            "affects_inventory_reconciliation": source in {"purchase", "sales", "inventory"},
        }
    return {"code": code, "source": source, "product_id": row.get("product_id"), "date": row.get("date"), "voucher": row.get("voucher"), "excel_row": row.get("excel_row"), **scopes, **details}


def _amount_validation_error(source: str, row: dict[str, Any]) -> dict[str, Any] | None:
    fields = ("supply_amount", "vat", "total_amount")
    missing = [field for field in fields if row.get(field) in (None, "")]
    if missing:
        return _record_error(
            source, row, "amount_components_missing", missing_fields=missing,
            affects={"affects_quantity": False, "affects_fifo_cost": False, "affects_revenue": source == "sales", "affects_vat": True, "affects_inventory_reconciliation": False},
            supply_amount=row.get("supply_amount"), vat=row.get("vat"), total_amount=row.get("total_amount"),
        )
    try:
        supply_amount, vat, total_amount = (dec(row[field]) for field in fields)
    except ValueError as exc:
        return _record_error(source, row, "amount_components_invalid", reason=str(exc), affects={"affects_quantity": False, "affects_fifo_cost": False, "affects_revenue": source == "sales", "affects_vat": True, "affects_inventory_reconciliation": False}, **{field: row.get(field) for field in fields})
    calculated = supply_amount + vat
    if calculated != total_amount:
        return _record_error(
            source, row, "amount_components_mismatch", supply_amount=out_number(supply_amount), vat=out_number(vat),
            total_amount=out_number(total_amount), calculated_amount=out_number(calculated), difference=out_number(calculated - total_amount),
            affects={"affects_quantity": False, "affects_fifo_cost": False, "affects_revenue": source == "sales", "affects_vat": True, "affects_inventory_reconciliation": False},
        )
    return None


def _sum_rows(rows: Iterable[dict[str, Any]], field: str) -> Decimal:
    return sum((dec(row.get(field)) for row in rows), ZERO)


def calculate_fifo(purchase_records: Iterable[dict[str, Any]], sales_records: Iterable[dict[str, Any]], inventory_records: Iterable[dict[str, Any]], period_start: str | date, period_end: str | date, inventory_date: str | date) -> dict[str, Any]:
    start, end, stock_date = iso_date(period_start), iso_date(period_end), iso_date(inventory_date)
    if not start <= end <= stock_date:
        raise ValueError("date relationship must satisfy period_start <= period_end <= inventory_date")
    purchases, sales, inventory = list(purchase_records), list(sales_records), list(inventory_records)
    errors: list[dict[str, Any]] = []
    amount_validation_errors: list[dict[str, Any]] = []
    valid_amount_keys: set[tuple[str, Any, Any, Any]] = set()
    events, product_ids, ledger_ids = [], set(), set()
    names: dict[Any, set[str]] = defaultdict(set); specs: dict[Any, set[str]] = defaultdict(set)
    for row, kind in [(r, "purchase") for r in purchases] + [(r, "sales") for r in sales]:
        try:
            iso_date(row.get("date")); dec(row.get("quantity"))
            if row.get("quantity") is None or (kind == "purchase" and row.get("unit_price") is None): raise ValueError("required field is missing")
        except ValueError as exc:
            errors.append(_record_error(kind, row, "invalid_record", reason=str(exc))); continue
        product_id = row.get("product_id")
        if product_id in (None, ""):
            errors.append(_record_error(kind, row, "missing_product_id", reason="record excluded from FIFO and quantity calculations")); continue
        amount_error = _amount_validation_error(kind, row)
        if amount_error:
            amount_validation_errors.append(amount_error)
        else:
            valid_amount_keys.add((kind, row.get("date"), row.get("voucher"), row.get("excel_row")))
        product_ids.add(product_id); ledger_ids.add(product_id); events.append((row, kind))
        if row.get("item_name"): names[product_id].add(str(row["item_name"]))
        if row.get("specification"): specs[product_id].add(str(row["specification"]))
    for row in inventory:
        product_id = row.get("product_id")
        if product_id in (None, ""):
            errors.append(_record_error("inventory", row, "missing_product_id", reason="inventory row excluded from reconciliation")); continue
        product_ids.add(product_id)
        if row.get("item_name"): names[product_id].add(str(row["item_name"]))
        if row.get("specification"): specs[product_id].add(str(row["specification"]))
    events.sort(key=lambda pair: event_sort_key(*pair))
    def has_valid_amount(row: dict[str, Any], kind: str) -> bool:
        return (kind, row.get("date"), row.get("voucher"), row.get("excel_row")) in valid_amount_keys
    engines = {product_id: ProductEngine(product_id) for product_id in product_ids}
    pre = [(r, k) for r, k in events if iso_date(r["date"]) < start]
    period = [(r, k) for r, k in events if start <= iso_date(r["date"]) <= end]
    for row, kind in pre: engines[row["product_id"]].process(row, kind)
    opening = deepcopy(engines)
    for row, kind in period: engines[row["product_id"]].process(row, kind)
    ending = deepcopy(engines)  # immutable, before any continuation mutation
    continuation = deepcopy(engines)
    for row, kind in events:
        if kind == "purchase" and iso_date(row["date"]) > end:
            continuation[row["product_id"]].process(row, kind, post=True, source="backfill")
    inventory_qty: dict[Any, Decimal] = defaultdict(lambda: ZERO)
    for row, kind in events:
        if iso_date(row["date"]) <= stock_date: inventory_qty[row["product_id"]] += dec(row["quantity"]) if kind == "purchase" else -dec(row["quantity"])
    sheet_qty: dict[Any, Decimal] = defaultdict(lambda: ZERO)
    for row in inventory:
        if row.get("product_id") in product_ids: sheet_qty[row["product_id"]] += dec(row.get("stock_quantity"))
    all_sales = [_sale_output(s, start, end) for engine in continuation.values() for s in engine.sales]
    all_sales.sort(key=lambda row: (row["date"], voucher_sort_key(row["voucher"]), row["excel_row"] or 10**12))
    period_shortage_sales = [sale for sale in all_sales if sale["in_analysis_period"] and dec(sale["unconfirmed_quantity"]) > ZERO]
    period_shortage_products = {sale["product_id"] for sale in period_shortage_sales}
    all_engine_errors = [event for engine in continuation.values() for event in engine.errors]
    linked_backfill_error_products: set[Any] = set()
    for error in [*errors, *amount_validation_errors, *all_engine_errors]:
        if error.get("source") != "purchase" or not error.get("affects_fifo_cost"):
            continue
        try:
            if iso_date(error.get("date")) > end and error.get("product_id") in period_shortage_products:
                linked_backfill_error_products.add(error["product_id"])
        except ValueError:
            continue
    sales_by_product: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for sale in all_sales: sales_by_product[sale["product_id"]].append(sale)
    rows, unconfirmed, backfills = [], [], []
    for product_id in sorted(product_ids, key=str):
        snapshot, future, opened = ending[product_id], continuation[product_id], opening[product_id]
        product_pre = [(r,k) for r,k in pre if r["product_id"] == product_id]; product_period = [(r,k) for r,k in period if r["product_id"] == product_id]
        opened_layers = [l for l in opened.layers if l.remaining_quantity > ZERO]
        end_layers = [l for l in snapshot.layers if l.remaining_quantity > ZERO and not l.post_period_end]
        p_events = [e for e in snapshot.purchase_cost_events if start <= iso_date(e["date"]) <= end]
        s_events = [e for e in future.sales_cost_events if start <= iso_date(e["date"]) <= end]
        purchases_period = [r for r,k in product_period if k == "purchase"]; sales_period = [r for r,k in product_period if k == "sales"]
        normal_qty = sum((l.remaining_quantity for l in end_layers), ZERO); normal_amt = sum((l.remaining_quantity*l.unit_cost for l in end_layers), ZERO)
        signed = sum((dec(r["quantity"]) if k == "purchase" else -dec(r["quantity"]) for r,k in product_pre + product_period), ZERO)
        outputs = sales_by_product[product_id]
        period_outputs = [sale for sale in outputs if sale["in_analysis_period"]]
        period_backfilled_qty = sum((dec(s["backfilled_quantity"]) for s in period_outputs), ZERO)
        period_backfilled_amt = sum((dec(s["backfilled_amount"]) for s in period_outputs), ZERO)
        all_backfilled_qty = sum((dec(s["backfilled_quantity"]) for s in outputs), ZERO)
        all_backfilled_amt = sum((dec(s["backfilled_amount"]) for s in outputs), ZERO)
        # Cost events make sale cancellations signed, so a +10 shortage and a
        # -4 cancellation reports the correct net shortage of six.
        # A sale's outstanding quantity is its final state after every later
        # purchase has been considered.  It is deliberately not reconstructed
        # from dated events: those events describe history, not current state.
        opening_outputs = [sale for sale in outputs if iso_date(sale["date"]) < start]
        opening_unconfirmed = sum((dec(sale["unconfirmed_quantity"]) for sale in opening_outputs), ZERO)
        period_unconfirmed = sum((dec(sale["unconfirmed_quantity"]) for sale in period_outputs), ZERO)
        all_unconfirmed = opening_unconfirmed + period_unconfirmed
        product_amount_errors = [error for error in amount_validation_errors if error.get("product_id") == product_id and error.get("date") and start <= iso_date(error["date"]) <= end]
        status = "error" if future.errors or product_id in linked_backfill_error_products or any(error["affects_fifo_cost"] or error["affects_revenue"] for error in product_amount_errors) else ("unconfirmed" if all_unconfirmed > ZERO else ("backfilled" if all_backfilled_qty > ZERO else "confirmed"))
        inv = sheet_qty.get(product_id); book = inventory_qty.get(product_id, ZERO); difference = inv-book if product_id in sheet_qty else None
        recon = "inventory_only" if product_id not in ledger_ids else ("ledger_only" if inv is None else ("inventory_negative_stock" if inv < ZERO else ("match" if difference == ZERO else ("inventory_more" if difference > ZERO else "ledger_more"))))
        layer_output = [{"layer_id":l.layer_id,"product_id":l.product_id,"purchase_date":l.purchase_date.isoformat(),"voucher":l.voucher,"excel_row":l.excel_row,"original_quantity":out_number(l.original_quantity),"remaining_quantity":out_number(l.remaining_quantity),"unit_cost":out_number(l.unit_cost),"post_period_end":False,"remaining_amount":out_money(l.remaining_quantity*l.unit_cost)} for l in end_layers]
        prior_settlement_events = [event for event in snapshot.inventory_cost_events if event["event_type"] == "shortage_backfill_consumption" and iso_date(event["date"]) >= start and event.get("sale_date") and iso_date(event["sale_date"]) < start]
        prior_settlement = -sum((dec(event["_cost_decimal"]) for event in prior_settlement_events), ZERO)
        prior_settlement_quantity = -sum((dec(event["quantity"]) for event in prior_settlement_events), ZERO)
        valid_sales_period = [row for row in sales_period if has_valid_amount(row, "sales")]
        valid_purchases_period = [row for row in purchases_period if has_valid_amount(row, "purchase")]
        period_sales_supply = _sum_rows(valid_sales_period, "supply_amount")
        period_sales_vat = _sum_rows(valid_sales_period, "vat")
        period_sales_total = _sum_rows(valid_sales_period, "total_amount")
        period_purchase_supply = _sum_rows(valid_purchases_period, "supply_amount")
        period_purchase_vat = _sum_rows(valid_purchases_period, "vat")
        period_purchase_total = _sum_rows(valid_purchases_period, "total_amount")
        period_unconfirmed_supply = sum((
            dec(sale.row.get("supply_amount")) * sale.unresolved_quantity / sale.original_quantity
            for sale in future.sales
            if sale.original_quantity and start <= iso_date(sale.row["date"]) <= end and has_valid_amount(sale.row, "sales")
        ), ZERO)
        fifo_sales_cost = sum((dec(e["_cost_decimal"]) for e in s_events), ZERO)
        gross_profit = period_sales_supply - fifo_sales_cost
        row = {"product_id":product_id,"item_name":sorted(names[product_id])[0] if names[product_id] else None,"item_names":sorted(names[product_id]),"specification":sorted(specs[product_id])[0] if specs[product_id] else None,"specifications":sorted(specs[product_id]),"opening_stock_quantity":out_number(sum((l.remaining_quantity for l in opened_layers),ZERO)),"opening_stock_amount":out_money(sum((l.remaining_quantity*l.unit_cost for l in opened_layers),ZERO)),"period_purchase_quantity":out_number(sum((dec(r["quantity"]) for r in purchases_period),ZERO)),"period_purchase_cost_amount":out_money(sum((dec(e["_cost_decimal"]) for e in p_events),ZERO)),"period_sales_quantity":out_number(sum((dec(r["quantity"]) for r in sales_period),ZERO)),"period_sales_supply_amount":out_money(period_sales_supply),"period_sales_supply_amount_exact":exact_money(period_sales_supply),"period_sales_vat_amount":out_money(period_sales_vat),"period_sales_total_amount":out_money(period_sales_total),"period_sales_amount":out_money(period_sales_supply),"fifo_sales_cost_amount":out_money(fifo_sales_cost),"fifo_sales_cost_amount_exact":exact_money(fifo_sales_cost),"ending_signed_stock_quantity":out_number(signed),"ending_normal_stock_quantity":out_number(normal_qty),"ending_fifo_inventory_amount":out_money(normal_amt),"ending_negative_stock_quantity":out_number(max(-signed,ZERO)),"period_backfilled_quantity":out_number(period_backfilled_qty),"period_backfilled_amount":out_money(period_backfilled_amt),"all_backfilled_quantity":out_number(all_backfilled_qty),"all_backfilled_amount":out_money(all_backfilled_amt),"post_period_backfill_quantity":out_number(period_backfilled_qty),"post_period_backfill_amount":out_money(period_backfilled_amt),"prior_period_shortage_settlement_quantity":out_number(prior_settlement_quantity),"prior_period_shortage_settlement_amount":out_money(prior_settlement),"backfilled_quantity":out_number(period_backfilled_qty),"backfilled_amount":out_money(period_backfilled_amt),"opening_unconfirmed_quantity":out_number(opening_unconfirmed),"period_unconfirmed_quantity":out_number(period_unconfirmed),"all_unconfirmed_quantity":out_number(all_unconfirmed),"unconfirmed_quantity":out_number(all_unconfirmed),"unconfirmed_sales_supply_amount":out_money(period_unconfirmed_supply),"cost_status":status,"inventory_book_quantity":out_number(book),"inventory_sheet_quantity":out_number(inv) if inv is not None else None,"inventory_quantity_difference":out_number(difference) if difference is not None else None,"quantity_reconciliation_status":recon,"remaining_cost_layers":layer_output,"purchase_quantity":out_number(sum((dec(r["quantity"]) for r in purchases_period),ZERO)),"sales_quantity":out_number(sum((dec(r["quantity"]) for r in sales_period),ZERO)),"calculated_stock_quantity":out_number(book),"inventory_quantity":out_number(inv) if inv is not None else None,"quantity_difference":out_number(difference) if difference is not None else None,"period_purchase_supply_amount":out_money(period_purchase_supply),"period_purchase_vat_amount":out_money(period_purchase_vat),"period_purchase_total_amount":out_money(period_purchase_total),"purchase_amount":out_money(period_purchase_supply),"sales_amount":out_money(period_sales_supply)}
        row["gross_profit"] = out_money(gross_profit)
        row["gross_profit_rate"] = out_number(gross_profit / period_sales_supply * Decimal("100")) if period_sales_supply and status == "confirmed" else None
        rows.append(row)
        for sale in outputs:
            if sale["unconfirmed_quantity"]: unconfirmed.append(sale)
            for allocation in sale["allocations"]:
                if allocation["backfilled"]: backfills.append({"sale_id":sale["sale_id"],"product_id":product_id,**allocation})
    status_counts={s:sum(r["cost_status"]==s for r in rows) for s in ("confirmed","backfilled","unconfirmed","error")}; recon_statuses=("match","inventory_more","ledger_more","ledger_only","inventory_only","inventory_negative_stock"); recon_counts={s:sum(r["quantity_reconciliation_status"]==s for r in rows) for s in recon_statuses}
    # Do not re-sum row-level display values: individual values are rounded for
    # JSON readability, while every summary remains a direct Decimal sum.
    period_purchases = [row for row, kind in period if kind == "purchase"]
    period_sales = [row for row, kind in period if kind == "sales"]
    opening_amount = sum((sum((layer.remaining_quantity * layer.unit_cost for layer in engine.layers if layer.remaining_quantity > ZERO), ZERO) for engine in opening.values()), ZERO)
    ending_amount = sum((sum((layer.remaining_quantity * layer.unit_cost for layer in engine.layers if layer.remaining_quantity > ZERO and not layer.post_period_end), ZERO) for engine in ending.values()), ZERO)
    period_purchase_cost = sum((event["_cost_decimal"] for engine in ending.values() for event in engine.purchase_cost_events if start <= iso_date(event["date"]) <= end), ZERO)
    fifo_sales_cost = sum((event["_cost_decimal"] for engine in continuation.values() for event in engine.sales_cost_events if start <= iso_date(event["date"]) <= end), ZERO)
    valid_period_sales = [row for row in period_sales if has_valid_amount(row, "sales")]
    valid_period_purchases = [row for row in period_purchases if has_valid_amount(row, "purchase")]
    period_sales_supply = _sum_rows(valid_period_sales, "supply_amount")
    period_sales_vat = _sum_rows(valid_period_sales, "vat")
    period_sales_total = _sum_rows(valid_period_sales, "total_amount")
    period_purchase_supply = _sum_rows(valid_period_purchases, "supply_amount")
    period_purchase_vat = _sum_rows(valid_period_purchases, "vat")
    period_purchase_total = _sum_rows(valid_period_purchases, "total_amount")
    gross_profit = period_sales_supply - fifo_sales_cost
    vat_settlement = period_sales_vat - period_purchase_vat
    opening_unconfirmed_quantity = sum((dec(row["opening_unconfirmed_quantity"]) for row in rows), ZERO)
    period_unconfirmed_quantity = sum((dec(row["period_unconfirmed_quantity"]) for row in rows), ZERO)
    all_unconfirmed_quantity = opening_unconfirmed_quantity + period_unconfirmed_quantity
    unconfirmed_sales_supply = sum((
        dec(sale.row.get("supply_amount")) * sale.unresolved_quantity / sale.original_quantity
        for engine in continuation.values() for sale in engine.sales
        if sale.original_quantity and start <= iso_date(sale.row["date"]) <= end and has_valid_amount(sale.row, "sales")
    ), ZERO)
    period_post_period_backfill = sum((
        allocation["_quantity"] * allocation["_unit_cost"]
        for engine in continuation.values() for sale in engine.sales
        if start <= iso_date(sale.row["date"]) <= end
        for allocation in sale.allocations if allocation["backfilled"]
    ), ZERO)
    all_post_period_backfill = sum((
        allocation["_quantity"] * allocation["_unit_cost"]
        for engine in continuation.values() for sale in engine.sales
        for allocation in sale.allocations if allocation["backfilled"]
    ), ZERO)
    all_post_period_backfill_quantity = sum((
        allocation["_quantity"]
        for engine in continuation.values() for sale in engine.sales
        for allocation in sale.allocations if allocation["backfilled"]
    ), ZERO)
    prior_settlement_events = [
        event
        for engine in ending.values() for event in engine.inventory_cost_events
        if event["event_type"] == "shortage_backfill_consumption" and event.get("sale_date") and iso_date(event["date"]) >= start and iso_date(event["sale_date"]) < start
    ]
    prior_settlement = -sum((event["_cost_decimal"] for event in prior_settlement_events), ZERO)
    prior_settlement_quantity = -sum((dec(event["quantity"]) for event in prior_settlement_events), ZERO)
    engine_errors=all_engine_errors
    unconfirmed_period_products = {
        sale["product_id"] for sale in all_sales
        if sale["in_analysis_period"] and dec(sale["unconfirmed_quantity"]) > ZERO
    }
    def error_can_affect_period(error: dict[str, Any]) -> bool:
        value = error.get("date")
        if not value:
            return error.get("source") != "inventory"
        try:
            error_date = iso_date(value)
            if start <= error_date <= end:
                return True
            # A later purchase error matters only when that same product still
            # needs it to price a current-period shortage.  It never affects
            # VAT because it is outside the settlement period.
            return error.get("source") == "purchase" and error_date > end and (
                error.get("product_id") in unconfirmed_period_products
                or (error.get("product_id") in (None, "") and bool(period_shortage_sales))
            )
        except ValueError:
            return error.get("source") != "inventory"
    calculation_errors = [error for error in [*errors, *amount_validation_errors, *engine_errors] if error_can_affect_period(error)]
    related_errors_by_sale: dict[str, list[str]] = defaultdict(list)
    unassigned_backfill_validation_error = False
    for error in calculation_errors:
        if error.get("source") != "purchase" or not error.get("affects_fifo_cost"):
            continue
        try:
            is_post_period = iso_date(error.get("date")) > end
        except ValueError:
            is_post_period = False
        if not is_post_period:
            continue
        product_id = error.get("product_id")
        if product_id in period_shortage_products:
            affected = [sale["sale_id"] for sale in period_shortage_sales if sale["product_id"] == product_id]
            error.update(affected_sale_ids=affected, affected_product_ids=[product_id], backfill_dependency=True, used_for_backfill=True)
            for sale_id in affected:
                related_errors_by_sale[sale_id].append(str(error.get("code") or "backfill_validation_error"))
        elif product_id in (None, "") and period_shortage_sales:
            error.update(affected_sale_ids=[], affected_product_ids=[], backfill_dependency=True, used_for_backfill=None)
            unassigned_backfill_validation_error = True
    sale_final_states = []
    for sale in all_sales:
        if not sale["in_analysis_period"]:
            continue
        related = related_errors_by_sale.get(sale["sale_id"], [])
        sale_final_states.append({"sale_id": sale["sale_id"], "product_id": sale["product_id"], "date": sale["date"], "final_unconfirmed_quantity": sale["unconfirmed_quantity"], "final_cost_status": "error" if related else sale["cost_status"], "related_error_codes": related, "related_backfill_error": bool(related)})
    gross_profit_status = "error" if unassigned_backfill_validation_error or any(error.get("affects_fifo_cost") or error.get("affects_revenue") for error in calculation_errors) else ("provisional" if period_unconfirmed_quantity > ZERO else "confirmed")
    vat_errors = []
    for error in calculation_errors:
        if not error.get("affects_vat"):
            continue
        try:
            if error.get("date") is None or not str(error.get("date")).strip():
                vat_errors.append(error)
            elif start <= iso_date(error["date"]) <= end:
                vat_errors.append(error)
        except ValueError:
            vat_errors.append(error)
    summary={
        "opening_stock_amount":out_money(opening_amount), "period_purchase_cost_amount":out_money(period_purchase_cost),
        "period_sales_supply_amount":out_money(period_sales_supply), "period_sales_supply_amount_exact":exact_money(period_sales_supply), "period_sales_vat_amount":out_money(period_sales_vat), "period_sales_total_amount":out_money(period_sales_total),
        "period_purchase_supply_amount":out_money(period_purchase_supply), "period_purchase_vat_amount":out_money(period_purchase_vat), "period_purchase_total_amount":out_money(period_purchase_total),
        "period_sales_amount":out_money(period_sales_supply), "purchase_amount":out_money(period_purchase_supply), "sales_amount":out_money(period_sales_supply),
        "fifo_sales_cost_amount":out_money(fifo_sales_cost), "fifo_sales_cost_amount_exact":exact_money(fifo_sales_cost), "gross_profit":out_money(gross_profit), "gross_profit_exact":exact_money(gross_profit),
        "gross_profit_rate":out_number(gross_profit / period_sales_supply * Decimal("100")) if period_sales_supply and gross_profit_status == "confirmed" else None,
        "gross_profit_status":gross_profit_status, "unconfirmed_sales_supply_amount":out_money(unconfirmed_sales_supply),
        "vat_settlement_amount":out_money(vat_settlement), "vat_settlement_status":"validation_error" if vat_errors else ("payable" if vat_settlement > ZERO else ("refundable" if vat_settlement < ZERO else "zero")),
        "post_vat_reference_amount":out_money(gross_profit - vat_settlement),
        "post_vat_reference_status":"error" if vat_errors or gross_profit_status == "error" else ("provisional" if gross_profit_status != "confirmed" else "confirmed"),
        "ending_fifo_inventory_amount":out_money(ending_amount), "inventory_amount_at_fifo":out_money(ending_amount),
        "period_backfilled_quantity":out_number(sum((dec(row["period_backfilled_quantity"]) for row in rows), ZERO)), "period_backfilled_amount":out_money(period_post_period_backfill), "all_backfilled_quantity":out_number(all_post_period_backfill_quantity), "all_backfilled_amount":out_money(all_post_period_backfill),
        "post_period_backfill_amount":out_money(period_post_period_backfill), "backfilled_amount":out_money(period_post_period_backfill),
        "prior_period_shortage_settlement_quantity":out_number(prior_settlement_quantity), "prior_period_shortage_settlement_amount":out_money(prior_settlement),
        "remainder_at_fifo":out_money(gross_profit), "inventory_amount_at_average_cost":None,"inventory_amount_at_latest_purchase_price":None,"remainder_at_average_cost":None,"remainder_at_latest_purchase_price":None,
    }
    def total(key: str) -> Decimal: return sum((dec(row[key]) for row in rows), ZERO)
    summary.update({key:out_number(total(key)) for key in ("opening_stock_quantity","period_purchase_quantity","period_sales_quantity","ending_signed_stock_quantity","ending_normal_stock_quantity","ending_negative_stock_quantity","backfilled_quantity")})
    summary["opening_unconfirmed_quantity"] = out_number(opening_unconfirmed_quantity)
    summary["period_unconfirmed_quantity"] = out_number(period_unconfirmed_quantity)
    summary["all_unconfirmed_quantity"] = out_number(all_unconfirmed_quantity)
    summary["unconfirmed_quantity"] = out_number(all_unconfirmed_quantity)
    summary.update({"cost_status_counts":status_counts,"status_counts":status_counts,"quantity_reconciliation_mismatch_count":sum(r["quantity_reconciliation_status"] != "match" for r in rows),"inventory_reconciliation_mismatch_count":sum(r["quantity_reconciliation_status"] != "match" for r in rows),"error_count":len(errors)+len(amount_validation_errors)+len(engine_errors),"amount_validation_errors":amount_validation_errors,"amount_validation_error_count":len(amount_validation_errors),"reconciliation_status_counts":recon_counts,"unassigned_backfill_validation_error":unassigned_backfill_validation_error})
    summary.update({
        "period_purchase_record_count": len(period_purchases),
        "period_sales_record_count": len(period_sales),
        "period_purchase_company_count": len({str(row.get("company")).strip() for row in period_purchases if row.get("company") not in (None, "")}),
        "period_sales_company_count": len({str(row.get("company")).strip() for row in period_sales if row.get("company") not in (None, "")}),
    })
    warnings=[]
    for product_id in sorted(product_ids,key=str):
        if len(names[product_id])>1: warnings.append({"code":"item_name_mismatch","product_id":product_id,"values":sorted(names[product_id])})
        if len(specs[product_id])>1: warnings.append({"code":"specification_mismatch","product_id":product_id,"values":sorted(specs[product_id])})
    public_events=[_clean(e) for engine in continuation.values() for e in engine.sales_cost_events]; public_unconfirmed_events=[_clean(e) for engine in continuation.values() for e in engine.unconfirmed_quantity_events]; public_purchases=[_clean(e) for engine in continuation.values() for e in engine.purchase_cost_events]; public_inventory_events=[_clean(e) for engine in ending.values() for e in engine.inventory_cost_events]
    all_dates=[iso_date(r["date"]) for r,k in events]; purchase_dates=[iso_date(r["date"]) for r,k in events if k=="purchase"]
    return {"metadata":{"period_start":start.isoformat(),"period_end":end.isoformat(),"inventory_date":stock_date.isoformat(),"analysis_start_date":start.isoformat(),"analysis_end_date":end.isoformat(),"inventory_reference_date":stock_date.isoformat(),"input_data_first_transaction_date":min(all_dates).isoformat() if all_dates else None,"input_data_last_transaction_date":max(all_dates).isoformat() if all_dates else None,"input_data_last_purchase_date":max(purchase_dates).isoformat() if purchase_dates else None,"input_first_transaction_date":min(all_dates).isoformat() if all_dates else None,"input_last_transaction_date":max(all_dates).isoformat() if all_dates else None,"input_last_purchase_date":max(purchase_dates).isoformat() if purchase_dates else None,"backfill_last_purchase_date":max((a["purchase_date"] for a in backfills),default=None),"costing_method":"FIFO","inventory_unit_costs_used":False,"inventory_price_fields_ignored":["average_cost","latest_purchase_price","sales_price"]},"summary":summary,"rows":rows,"sales_allocations":all_sales,"sales_cost_events":public_events,"unconfirmed_quantity_events":public_unconfirmed_events,"purchase_cost_events":public_purchases,"inventory_cost_events":public_inventory_events,"unconfirmed_shipments":unconfirmed,"backfill_allocations":backfills,"cancellation_events":[event for engine in continuation.values() for event in engine.cancellations],"amount_validation_errors":amount_validation_errors,"amount_validation_error_count":len(amount_validation_errors),"errors":errors+amount_validation_errors+engine_errors,"warnings":warnings,"calculated_negative_stock":[row for row in rows if dec(row["ending_signed_stock_quantity"])<ZERO],"inventory_negative_stock":[row for row in rows if row["quantity_reconciliation_status"]=="inventory_negative_stock"],"quantity_mismatches":[row for row in rows if row["quantity_reconciliation_status"]!="match"]}
