"""Moving weighted-average valuation for current stock.

Positive stock entries are folded into the current weighted-average unit cost.
Negative operational movements consume inventory at the current average cost.

Legacy installations can contain opening stock with a zero cost because valuation
was not maintained historically. A zero cost in that situation means "unknown",
not necessarily "free". The first later non-zero receipt therefore becomes a
proxy for the unknown opening stock cost instead of diluting the average toward
zero. Replays mark such results as estimated so production repairs remain
explicit and auditable.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from app.extensions import db
from app.models import Product, StockBalance, StockMovement

COST_QUANT = Decimal("0.0001")
ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _cost(value) -> Decimal:
    result = _decimal(value)
    if result < 0:
        raise ValueError("NEGATIVE_UNIT_COST")
    return result.quantize(COST_QUANT, rounding=ROUND_HALF_UP)


def moving_average_cost(quantity_before, cost_before, quantity_delta, movement_cost) -> Decimal:
    """Return the current unit valuation after one ordinary stock movement.

    For legacy/current stock that has a positive quantity but a zero valuation,
    the first later non-zero receipt is used as the best available proxy for the
    unknown opening cost. Likewise, a positive legacy movement carrying a zero
    snapshot after a cost is already known inherits the current average instead
    of being treated as free inventory.
    """
    quantity_before = _decimal(quantity_before)
    cost_before = _cost(cost_before)
    quantity_delta = _decimal(quantity_delta)
    quantity_after = quantity_before + quantity_delta

    if quantity_before < 0 or quantity_after < 0:
        raise ValueError("NEGATIVE_STOCK_QUANTITY")
    if quantity_after == 0:
        return ZERO.quantize(COST_QUANT)
    if quantity_delta <= 0:
        return cost_before

    incoming_cost = _cost(movement_cost)

    # A positive zero-cost legacy movement is unknown-cost stock, not free stock.
    if incoming_cost == 0 and cost_before > 0:
        incoming_cost = cost_before

    # Legacy opening stock may have quantity but no valuation. Once an actual
    # acquisition cost is known, use it as the proxy for the unknown opening lot.
    if quantity_before > 0 and cost_before == 0 and incoming_cost > 0:
        return incoming_cost

    value_before = quantity_before * cost_before
    incoming_value = quantity_delta * incoming_cost
    return ((value_before + incoming_value) / quantity_after).quantize(
        COST_QUANT,
        rounding=ROUND_HALF_UP,
    )


def remove_receipt_cost(quantity_before, cost_before, receipt_quantity, receipt_cost) -> Decimal:
    """Remove one unconsumed receipt from current moving-average inventory value."""
    quantity_before = _decimal(quantity_before)
    cost_before = _cost(cost_before)
    receipt_quantity = _decimal(receipt_quantity)
    receipt_cost = _cost(receipt_cost)
    quantity_after = quantity_before - receipt_quantity

    if receipt_quantity <= 0 or quantity_after < 0:
        raise ValueError("INVALID_RECEIPT_REVERSAL")
    if quantity_after == 0:
        return ZERO.quantize(COST_QUANT)

    remaining_value = quantity_before * cost_before - receipt_quantity * receipt_cost
    if remaining_value < 0:
        raise ValueError("NEGATIVE_STOCK_VALUE")
    return (remaining_value / quantity_after).quantize(COST_QUANT, rounding=ROUND_HALF_UP)


def replay_product_valuation(bar_id: int, product_id: int) -> dict:
    """Rebuild one product valuation from immutable stock movement history.

    ``valuation_basis`` is ``EXACT_HISTORY`` when every required cost was present,
    ``ESTIMATED_LEGACY`` when at least one unknown zero-cost legacy quantity had to
    inherit a known cost, and ``NO_COST_HISTORY`` when no usable cost exists.
    """
    product = db.session.scalar(
        select(Product).where(Product.bar_id == bar_id, Product.id == product_id)
    )
    if not product:
        raise LookupError("NOT_FOUND")

    movements = list(
        db.session.scalars(
            select(StockMovement)
            .where(StockMovement.bar_id == bar_id, StockMovement.product_id == product_id)
            .order_by(StockMovement.occurred_at, StockMovement.id)
        )
    )
    by_id = {movement.id: movement for movement in movements}
    effective_cost_by_id: dict[int, Decimal] = {}
    quantity = ZERO
    unit_cost = ZERO.quantize(COST_QUANT)
    anomaly = None
    last_real_outflow_id = None
    estimated = False
    estimate_reasons: set[str] = set()

    for movement in movements:
        delta = _decimal(movement.quantity_delta)
        try:
            if delta < 0 and movement.reversal_of_id is not None:
                source = by_id.get(movement.reversal_of_id)
                if source and source.movement_type == "PURCHASE":
                    if last_real_outflow_id is not None and source.id < last_real_outflow_id < movement.id:
                        raise ValueError("UNSAFE_PURCHASE_REVERSAL_HISTORY")
                    source_cost = effective_cost_by_id.get(source.id, _cost(source.unit_cost_snapshot))
                    unit_cost = remove_receipt_cost(
                        quantity,
                        unit_cost,
                        source.quantity_delta,
                        source_cost,
                    )
                    quantity += delta
                    continue

            raw_cost = _cost(movement.unit_cost_snapshot)
            if delta > 0:
                if raw_cost == 0 and unit_cost > 0:
                    estimated = True
                    estimate_reasons.add("ZERO_COST_INFLOW_INHERITED")
                elif quantity > 0 and unit_cost == 0 and raw_cost > 0:
                    estimated = True
                    estimate_reasons.add("OPENING_COST_INFERRED")

            effective_cost = raw_cost
            if delta > 0 and raw_cost == 0 and unit_cost > 0:
                effective_cost = unit_cost
            elif delta > 0 and quantity > 0 and unit_cost == 0 and raw_cost > 0:
                effective_cost = raw_cost

            next_cost = moving_average_cost(
                quantity,
                unit_cost,
                delta,
                raw_cost,
            )
            quantity += delta
            unit_cost = next_cost
            if delta > 0:
                effective_cost_by_id[movement.id] = effective_cost
            if delta < 0 and movement.reversal_of_id is None:
                last_real_outflow_id = movement.id
        except ValueError as exc:
            anomaly = str(exc)
            break

    balance = db.session.scalar(
        select(StockBalance).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id == product_id,
        )
    )
    balance_quantity = _decimal(balance.quantity if balance else 0)
    quantity_matches = anomaly is None and quantity == balance_quantity

    if anomaly is not None:
        valuation_basis = "ANOMALY"
    elif balance_quantity > 0 and unit_cost == 0:
        valuation_basis = "NO_COST_HISTORY"
    elif estimated:
        valuation_basis = "ESTIMATED_LEGACY"
    else:
        valuation_basis = "EXACT_HISTORY"

    return {
        "product": product,
        "movement_count": len(movements),
        "replayed_quantity": quantity,
        "balance_quantity": balance_quantity,
        "quantity_matches": quantity_matches,
        "old_unit_cost": _cost(product.valuation_unit_cost),
        "new_unit_cost": unit_cost,
        "valuation_basis": valuation_basis,
        "estimate_reasons": sorted(estimate_reasons),
        "anomaly": anomaly,
    }


def rebuild_bar_valuations(bar_id: int, apply: bool = False, include_estimates: bool = False) -> dict:
    """Preview or apply valuation repairs for all products of one bar.

    Estimated legacy values are never written unless ``include_estimates`` is
    explicitly enabled together with ``apply``.
    """
    products = list(
        db.session.scalars(
            select(Product).where(Product.bar_id == bar_id).order_by(Product.id)
        )
    )
    rows = []
    changed = 0
    estimated_changed = 0
    applied_count = 0
    skipped = 0

    for product in products:
        row = replay_product_valuation(bar_id, product.id)
        basis = row["valuation_basis"]
        if row["movement_count"] == 0:
            row["status"] = "NO_HISTORY"
            skipped += 1
        elif not row["quantity_matches"]:
            row["status"] = "QUANTITY_MISMATCH"
            skipped += 1
        elif basis == "NO_COST_HISTORY":
            row["status"] = "NO_COST_HISTORY"
            skipped += 1
        elif row["old_unit_cost"] != row["new_unit_cost"]:
            changed += 1
            if basis == "ESTIMATED_LEGACY":
                row["status"] = "ESTIMATED_CHANGE"
                estimated_changed += 1
            else:
                row["status"] = "CHANGED"
            if apply and (basis != "ESTIMATED_LEGACY" or include_estimates):
                product.valuation_unit_cost = row["new_unit_cost"]
                applied_count += 1
        else:
            row["status"] = "ESTIMATED_OK" if basis == "ESTIMATED_LEGACY" else "OK"
        rows.append(row)

    return {
        "bar_id": bar_id,
        "products": len(products),
        "changed": changed,
        "estimated_changed": estimated_changed,
        "applied_count": applied_count,
        "skipped": skipped,
        "rows": rows,
        "applied": bool(apply),
        "include_estimates": bool(include_estimates),
    }
