"""Moving weighted-average valuation for current stock.

Positive stock entries are valued at their movement cost and folded into the
current weighted-average unit cost. Negative stock movements consume inventory
at the current weighted-average cost, so they do not change the unit average
unless stock reaches zero.

The replay helpers are also used to repair legacy product valuations from the
immutable stock movement history without changing stock quantities.
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
    """Return the unit valuation after one stock movement.

    Receipts and positive returns add value at their own snapshot cost. Outflows
    consume stock at the current moving-average cost and therefore keep the same
    unit valuation. Empty stock is reset to zero so the next receipt starts from
    its actual acquisition cost.
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
    value_before = quantity_before * cost_before
    incoming_value = quantity_delta * incoming_cost
    return ((value_before + incoming_value) / quantity_after).quantize(
        COST_QUANT,
        rounding=ROUND_HALF_UP,
    )


def replay_product_valuation(bar_id: int, product_id: int) -> dict:
    """Rebuild one product's current valuation from stock movement history."""
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
    quantity = ZERO
    unit_cost = ZERO.quantize(COST_QUANT)
    anomaly = None

    for movement in movements:
        delta = _decimal(movement.quantity_delta)
        try:
            next_cost = moving_average_cost(
                quantity,
                unit_cost,
                delta,
                movement.unit_cost_snapshot,
            )
        except ValueError as exc:
            anomaly = str(exc)
            break
        quantity += delta
        unit_cost = next_cost

    balance = db.session.scalar(
        select(StockBalance).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id == product_id,
        )
    )
    balance_quantity = _decimal(balance.quantity if balance else 0)
    quantity_matches = anomaly is None and quantity == balance_quantity

    return {
        "product": product,
        "movement_count": len(movements),
        "replayed_quantity": quantity,
        "balance_quantity": balance_quantity,
        "quantity_matches": quantity_matches,
        "old_unit_cost": _cost(product.valuation_unit_cost),
        "new_unit_cost": unit_cost,
        "anomaly": anomaly,
    }


def rebuild_bar_valuations(bar_id: int, apply: bool = False) -> dict:
    """Preview or apply valuation repairs for all products of one bar."""
    products = list(
        db.session.scalars(
            select(Product).where(Product.bar_id == bar_id).order_by(Product.id)
        )
    )
    rows = []
    changed = 0
    skipped = 0

    for product in products:
        row = replay_product_valuation(bar_id, product.id)
        if row["movement_count"] == 0:
            row["status"] = "NO_HISTORY"
            skipped += 1
        elif not row["quantity_matches"]:
            row["status"] = "QUANTITY_MISMATCH"
            skipped += 1
        elif row["old_unit_cost"] != row["new_unit_cost"]:
            row["status"] = "CHANGED"
            changed += 1
            if apply:
                product.valuation_unit_cost = row["new_unit_cost"]
        else:
            row["status"] = "OK"
        rows.append(row)

    return {
        "bar_id": bar_id,
        "products": len(products),
        "changed": changed,
        "skipped": skipped,
        "rows": rows,
        "applied": bool(apply),
    }
