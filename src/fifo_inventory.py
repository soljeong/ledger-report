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
        self._layer_sequence = 0

    def _new_layer(self, row: dict[str, Any], quantity: Decimal, post: bool) -> Layer:
        self._layer_sequence += 1
        return Layer(self.product_id, iso_date(row["date"]), row.get("voucher"), row.get("excel_row"),
                     quantity, quantity, dec(row.get("unit_price")), post,
                     f"{self.product_id}|{row.get('date')}|{row.get('voucher')}|{row.get('excel_row')}|{self._layer_sequence}",
                     self._layer_sequence)

    @staticmethod
    def _allocation(layer: Layer, quantity: Decimal, *, source: str) -> dict[str, Any]:
        return {
            "layer_id": layer.layer_id, "purchase_date": layer.purchase_date.isoformat(),
            "purchase_voucher": layer.voucher, "purchase_excel_row": layer.excel_row,
            "purchase_quantity": out_number(quantity), "unit_cost": out_number(layer.unit_cost),
            "cost_amount": out_money(quantity * layer.unit_cost), "backfilled": layer.post_period_end,
            "allocation_source": source, "_layer": layer, "_quantity": quantity, "_unit_cost": layer.unit_cost,
        }

    def _cost_event(self, row: dict[str, Any], quantity: Decimal, cost: Decimal, event_type: str, **extra: Any) -> dict[str, Any]:
        return {"transaction_id": sale_key(row), "date": row.get("date"), "voucher": row.get("voucher"),
                "excel_row": row.get("excel_row"), "product_id": self.product_id,
                "quantity": out_number(quantity), "cost_amount": out_money(cost), "_cost_decimal": cost,
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
            sale.event["unconfirmed_quantity"] = out_number(sale.unresolved_quantity)
        while self.unconfirmed and self.unconfirmed[0].unresolved_quantity <= ZERO:
            self.unconfirmed.popleft()

    def add_positive_purchase(self, row: dict[str, Any], *, post: bool, source: str) -> None:
        quantity = dec(row["quantity"])
        layer = self._new_layer(row, quantity, post)
        self.layers.append(layer)  # retain even fully allocated layers for later sales reversal
        if post:
            self.post_period_layers.append(layer)
        self.purchase_cost_events.append(self._cost_event(row, quantity, quantity * layer.unit_cost, "purchase", layer_id=layer.layer_id, post_period_end=post))
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
            allocations.append({"layer_id": layer.layer_id, "purchase_date": layer.purchase_date.isoformat(), "purchase_voucher": layer.voucher, "purchase_excel_row": layer.excel_row, "quantity": out_number(quantity), "unit_cost": out_number(layer.unit_cost), "amount": out_money(quantity * layer.unit_cost)})
        self.purchase_cost_events.append(self._cost_event(row, -requested, -cost, "purchase_cancellation", allocations=allocations, post_period_end=post_only))
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
        sale.unresolved_quantity = remaining
        cost = sum((value["_quantity"] * value["_unit_cost"] for value in sale.allocations), ZERO)
        sale.event = self._cost_event(row, quantity, cost, "sale", unconfirmed_quantity=out_number(remaining), inventory_cost_amount=out_money(cost), cost_allocations=[dict(value) for value in sale.allocations])
        self.sales_cost_events.append(sale.event)
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
                allocation["_layer"].remaining_quantity += quantity
                amount = quantity * allocation["_unit_cost"]
                restored_cost += amount
                if not allocation["backfilled"]:
                    restored_inventory_cost += amount
                left -= quantity
                restored.append({"layer_id": allocation["layer_id"], "purchase_date": allocation["purchase_date"], "purchase_voucher": allocation["purchase_voucher"], "purchase_excel_row": allocation["purchase_excel_row"], "quantity": out_number(quantity), "unit_cost": allocation["unit_cost"], "amount": out_money(amount)})
            sale.active_quantity -= cancel
            sale.canceled_quantity += cancel
            remaining -= cancel
            details.append({"sale_id": sale_key(sale.row), "quantity": out_number(cancel), "unconfirmed_quantity": out_number(unresolved), "restored_allocations": restored})
        event = self._cost_event(row, -requested, -restored_cost, "sale_cancellation", unconfirmed_quantity=out_number(-unconfirmed_cancel), inventory_cost_amount=out_money(restored_inventory_cost), restoration_details=details)
        self.sales_cost_events.append(event)
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
        self.errors.append({"code": code, "product_id": self.product_id, "date": row.get("date"), "voucher": row.get("voucher"), "excel_row": row.get("excel_row"), **details})


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
        allocations.append(output)
        cost += quantity * value["_unit_cost"]
        if value["backfilled"]:
            backfill_qty += quantity
            backfill_amount += quantity * value["_unit_cost"]
    sale_date = iso_date(sale.row["date"])
    return {"sale_id": sale_key(sale.row), "product_id": sale.product_id, "date": sale.row.get("date"), "voucher": sale.row.get("voucher"), "excel_row": sale.row.get("excel_row"), "company": sale.row.get("company"), "item_name": sale.row.get("item_name"), "specification": sale.row.get("specification"), "sale_quantity": out_number(sale.original_quantity), "effective_quantity": out_number(sale.active_quantity), "canceled_quantity": out_number(sale.canceled_quantity), "allocations": allocations, "fifo_cost_amount": out_money(cost), "unconfirmed_quantity": out_number(sale.unresolved_quantity), "backfilled_quantity": out_number(backfill_qty), "backfilled_amount": out_money(backfill_amount), "cost_status": "unconfirmed" if sale.unresolved_quantity > ZERO else ("backfilled" if backfill_qty > ZERO else "confirmed"), "in_analysis_period": start <= sale_date <= end}


def _record_error(source: str, row: dict[str, Any], code: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "source": source, "product_id": row.get("product_id"), "date": row.get("date"), "voucher": row.get("voucher"), "excel_row": row.get("excel_row"), **details}


def calculate_fifo(purchase_records: Iterable[dict[str, Any]], sales_records: Iterable[dict[str, Any]], inventory_records: Iterable[dict[str, Any]], period_start: str | date, period_end: str | date, inventory_date: str | date) -> dict[str, Any]:
    start, end, stock_date = iso_date(period_start), iso_date(period_end), iso_date(inventory_date)
    if not start <= end <= stock_date:
        raise ValueError("date relationship must satisfy period_start <= period_end <= inventory_date")
    purchases, sales, inventory = list(purchase_records), list(sales_records), list(inventory_records)
    errors: list[dict[str, Any]] = []
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
        backfilled_qty = sum((dec(s["backfilled_quantity"]) for s in outputs), ZERO); backfilled_amt = sum((dec(s["backfilled_amount"]) for s in outputs), ZERO); unresolved = sum((dec(s["unconfirmed_quantity"]) for s in outputs), ZERO)
        status = "error" if future.errors else ("unconfirmed" if unresolved > ZERO else ("backfilled" if backfilled_qty > ZERO else "confirmed"))
        inv = sheet_qty.get(product_id); book = inventory_qty.get(product_id, ZERO); difference = inv-book if product_id in sheet_qty else None
        recon = "inventory_only" if product_id not in ledger_ids else ("ledger_only" if inv is None else ("inventory_negative_stock" if inv < ZERO else ("match" if difference == ZERO else ("inventory_more" if difference > ZERO else "ledger_more"))))
        layer_output = [{"layer_id":l.layer_id,"product_id":l.product_id,"purchase_date":l.purchase_date.isoformat(),"voucher":l.voucher,"excel_row":l.excel_row,"original_quantity":out_number(l.original_quantity),"remaining_quantity":out_number(l.remaining_quantity),"unit_cost":out_number(l.unit_cost),"post_period_end":False,"remaining_amount":out_money(l.remaining_quantity*l.unit_cost)} for l in end_layers]
        row = {"product_id":product_id,"item_name":sorted(names[product_id])[0] if names[product_id] else None,"item_names":sorted(names[product_id]),"specification":sorted(specs[product_id])[0] if specs[product_id] else None,"specifications":sorted(specs[product_id]),"opening_stock_quantity":out_number(sum((l.remaining_quantity for l in opened_layers),ZERO)),"opening_stock_amount":out_money(sum((l.remaining_quantity*l.unit_cost for l in opened_layers),ZERO)),"period_purchase_quantity":out_number(sum((dec(r["quantity"]) for r in purchases_period),ZERO)),"period_purchase_cost_amount":out_money(sum((dec(e["_cost_decimal"]) for e in p_events),ZERO)),"period_sales_quantity":out_number(sum((dec(r["quantity"]) for r in sales_period),ZERO)),"period_sales_amount":out_money(sum((dec(r.get("total_amount")) for r in sales_period),ZERO)),"fifo_sales_cost_amount":out_money(sum((dec(e["_cost_decimal"]) for e in s_events),ZERO)),"ending_signed_stock_quantity":out_number(signed),"ending_normal_stock_quantity":out_number(normal_qty),"ending_fifo_inventory_amount":out_money(normal_amt),"ending_negative_stock_quantity":out_number(max(-signed,ZERO)),"backfilled_quantity":out_number(backfilled_qty),"backfilled_amount":out_money(backfilled_amt),"unconfirmed_quantity":out_number(unresolved),"cost_status":status,"inventory_book_quantity":out_number(book),"inventory_sheet_quantity":out_number(inv) if inv is not None else None,"inventory_quantity_difference":out_number(difference) if difference is not None else None,"quantity_reconciliation_status":recon,"remaining_cost_layers":layer_output,"purchase_quantity":out_number(sum((dec(r["quantity"]) for r in purchases_period),ZERO)),"sales_quantity":out_number(sum((dec(r["quantity"]) for r in sales_period),ZERO)),"calculated_stock_quantity":out_number(book),"inventory_quantity":out_number(inv) if inv is not None else None,"quantity_difference":out_number(difference) if difference is not None else None,"purchase_amount":out_money(sum((dec(r.get("total_amount")) for r in purchases_period),ZERO)),"sales_amount":out_money(sum((dec(r.get("total_amount")) for r in sales_period),ZERO))}
        row["gross_profit"] = out_money(dec(row["period_sales_amount"])-dec(row["fifo_sales_cost_amount"])); rows.append(row)
        for sale in outputs:
            if sale["unconfirmed_quantity"]: unconfirmed.append(sale)
            for allocation in sale["allocations"]:
                if allocation["backfilled"]: backfills.append({"sale_id":sale["sale_id"],"product_id":product_id,**allocation})
    status_counts={s:sum(r["cost_status"]==s for r in rows) for s in ("confirmed","backfilled","unconfirmed","error")}; recon_statuses=("match","inventory_more","ledger_more","ledger_only","inventory_only","inventory_negative_stock"); recon_counts={s:sum(r["quantity_reconciliation_status"]==s for r in rows) for s in recon_statuses}
    def total(key: str) -> Decimal: return sum((dec(row[key]) for row in rows), ZERO)
    summary={key:out_money(total(key)) for key in ("opening_stock_amount","period_purchase_cost_amount","period_sales_amount","fifo_sales_cost_amount","ending_fifo_inventory_amount","backfilled_amount","gross_profit")}
    summary.update({key:out_number(total(key)) for key in ("opening_stock_quantity","period_purchase_quantity","period_sales_quantity","ending_signed_stock_quantity","ending_normal_stock_quantity","ending_negative_stock_quantity","backfilled_quantity","unconfirmed_quantity")})
    summary.update({"cost_status_counts":status_counts,"status_counts":status_counts,"quantity_reconciliation_mismatch_count":sum(r["quantity_reconciliation_status"] != "match" for r in rows),"inventory_reconciliation_mismatch_count":sum(r["quantity_reconciliation_status"] != "match" for r in rows),"error_count":len(errors)+sum(len(e.errors) for e in continuation.values()),"reconciliation_status_counts":recon_counts,"purchase_amount":out_money(total("purchase_amount")),"sales_amount":out_money(total("sales_amount")),"inventory_amount_at_fifo":out_money(total("ending_fifo_inventory_amount")),"remainder_at_fifo":out_money(total("gross_profit")),"inventory_amount_at_average_cost":None,"inventory_amount_at_latest_purchase_price":None,"remainder_at_average_cost":None,"remainder_at_latest_purchase_price":None})
    warnings=[]
    for product_id in sorted(product_ids,key=str):
        if len(names[product_id])>1: warnings.append({"code":"item_name_mismatch","product_id":product_id,"values":sorted(names[product_id])})
        if len(specs[product_id])>1: warnings.append({"code":"specification_mismatch","product_id":product_id,"values":sorted(specs[product_id])})
    public_events=[_clean(e) for engine in continuation.values() for e in engine.sales_cost_events]; public_purchases=[_clean(e) for engine in continuation.values() for e in engine.purchase_cost_events]
    all_dates=[iso_date(r["date"]) for r,k in events]; purchase_dates=[iso_date(r["date"]) for r,k in events if k=="purchase"]; engine_errors=[e for engine in continuation.values() for e in engine.errors]
    return {"metadata":{"period_start":start.isoformat(),"period_end":end.isoformat(),"inventory_date":stock_date.isoformat(),"analysis_start_date":start.isoformat(),"analysis_end_date":end.isoformat(),"inventory_reference_date":stock_date.isoformat(),"input_data_first_transaction_date":min(all_dates).isoformat() if all_dates else None,"input_data_last_transaction_date":max(all_dates).isoformat() if all_dates else None,"input_data_last_purchase_date":max(purchase_dates).isoformat() if purchase_dates else None,"input_first_transaction_date":min(all_dates).isoformat() if all_dates else None,"input_last_transaction_date":max(all_dates).isoformat() if all_dates else None,"input_last_purchase_date":max(purchase_dates).isoformat() if purchase_dates else None,"backfill_last_purchase_date":max((a["purchase_date"] for a in backfills),default=None),"costing_method":"FIFO","inventory_unit_costs_used":False,"inventory_price_fields_ignored":["average_cost","latest_purchase_price","sales_price"]},"summary":summary,"rows":rows,"sales_allocations":all_sales,"sales_cost_events":public_events,"purchase_cost_events":public_purchases,"unconfirmed_shipments":unconfirmed,"backfill_allocations":backfills,"cancellation_events":[event for engine in continuation.values() for event in engine.cancellations],"errors":errors+engine_errors,"warnings":warnings,"calculated_negative_stock":[row for row in rows if dec(row["ending_signed_stock_quantity"])<ZERO],"inventory_negative_stock":[row for row in rows if row["quantity_reconciliation_status"]=="inventory_negative_stock"],"quantity_mismatches":[row for row in rows if row["quantity_reconciliation_status"]!="match"]}
