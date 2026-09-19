"""Moving weighted-average valuation for current stock.

Positive stock entries are folded into the current weighted-average unit cost.
Negative operational movements consume inventory at the current average cost.

Legacy installations can contain opening stock with a zero cost because valuation
was not maintained historically. They can also contain purchases entered before
CASE/BOTTLE metadata existed: the quantity was stored as bottles while the unit
cost actually held a case price. Replay repairs recognise conservative case-price
signatures from either explicit received CASE history or the configured default,
convert them to a base-unit cost, and mark the result as estimated so production
application remains explicit.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select

from app.extensions import db
from app.models import Bar, Product, PurchaseLine, StockBalance, StockMovement
from app.purchase_defaults import default_purchase_price, default_units_per_case

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

    if incoming_cost == 0 and cost_before > 0:
        incoming_cost = cost_before

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


def _received_case_signatures(movements) -> set[tuple[int, Decimal]]:
    """Return observed ``(case_size, case_price)`` pairs from received CASE stock.

    Only purchase lines that actually produced a PURCHASE stock movement are used,
    so drafts and unreceived purchase documents cannot influence legacy repair.
    """
    signatures: set[tuple[int, Decimal]] = set()
    for movement in movements:
        if movement.movement_type != "PURCHASE" or movement.purchase_line_id is None:
            continue
        line = db.session.get(PurchaseLine, movement.purchase_line_id)
        if not line:
            continue
        if line.bar_id != movement.bar_id or line.product_id != movement.product_id:
            continue
        if str(getattr(line, "purchase_unit", "") or "").upper() != "CASE":
            continue
        case_size = getattr(line, "units_per_case_snapshot", None)
        if not case_size:
            continue
        try:
            case_size = int(case_size)
        except (TypeError, ValueError):
            continue
        if case_size <= 1:
            continue
        case_price = _cost(getattr(line, "purchase_unit_price_snapshot", 0))
        if case_price > 0:
            signatures.add((case_size, case_price))
    return signatures


def _legacy_purchase_cost(
    product: Product,
    movement: StockMovement,
    currency: str,
    received_case_signatures: set[tuple[int, Decimal]],
):
    """Return a replay cost and optional legacy-normalisation reason.

    Old rows migrated before purchase-unit metadata was trustworthy can look like
    ``BOTTLE @ 7500`` even though 7500 was the supplier case price. We normalise
    only when the entire legacy line carries the same price and either:

    * an actually received CASE line for the same product proves the same case
      price and case size, or
    * the price exactly matches the configured default case price.

    Arbitrary expensive bottle purchases are therefore never guessed.
    """
    raw_cost = _cost(movement.unit_cost_snapshot)
    if movement.movement_type != "PURCHASE" or movement.purchase_line_id is None:
        return raw_cost, None

    line = db.session.get(PurchaseLine, movement.purchase_line_id)
    if not line or line.bar_id != movement.bar_id or line.product_id != movement.product_id:
        return raw_cost, None

    if str(getattr(line, "purchase_unit", "") or "").upper() != "BOTTLE":
        return raw_cost, None
    if getattr(line, "units_per_case_snapshot", None) not in (None, 0):
        return raw_cost, None

    case_size = default_units_per_case(product.name, product.units_per_case)
    if not case_size or int(case_size) <= 1:
        return raw_cost, None
    case_size = int(case_size)

    line_purchase_price = _cost(getattr(line, "purchase_unit_price_snapshot", raw_cost))
    line_unit_cost = _cost(getattr(line, "unit_cost_snapshot", raw_cost))
    if not (raw_cost == line_purchase_price == line_unit_cost):
        return raw_cost, None

    reason = None
    if (case_size, raw_cost) in received_case_signatures:
        reason = "LEGACY_CASE_PRICE_MATCHED_TO_CASE_HISTORY"
    else:
        configured_case_price = default_purchase_price(
            product.name,
            currency,
            Decimal("-1"),
        )
        if configured_case_price > 0 and raw_cost == _cost(configured_case_price):
            reason = "LEGACY_CASE_PRICE_NORMALIZED"

    if reason is None:
        return raw_cost, None

    base_cost = (raw_cost / Decimal(case_size)).quantize(
        COST_QUANT,
        rounding=ROUND_HALF_UP,
    )
    return base_cost, reason


def replay_product_valuation(bar_id: int, product_id: int) -> dict:
    """Rebuild one product valuation from immutable stock movement history.

    ``valuation_basis`` is ``EXACT_HISTORY`` when every required cost was present,
    ``ESTIMATED_LEGACY`` when legacy data required a conservative inference, and
    ``NO_COST_HISTORY`` when no usable acquisition cost exists.
    """
    product = db.session.scalar(
        select(Product).where(Product.bar_id == bar_id, Product.id == product_id)
    )
    if not product:
        raise LookupError("NOT_FOUND")
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    movements = list(
        db.session.scalars(
            select(StockMovement)
            .where(StockMovement.bar_id == bar_id, StockMovement.product_id == product_id)
            .order_by(StockMovement.occurred_at, StockMovement.id)
        )
    )
    received_case_signatures = _received_case_signatures(movements)
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

            replay_cost, normalization_reason = _legacy_purchase_cost(
                product,
                movement,
                bar.currency,
                received_case_signatures,
            )
            if normalization_reason:
                estimated = True
                estimate_reasons.add(normalization_reason)

            if delta > 0:
                if replay_cost == 0 and unit_cost > 0:
                    estimated = True
                    estimate_reasons.add("ZERO_COST_INFLOW_INHERITED")
                elif quantity > 0 and unit_cost == 0 and replay_cost > 0:
                    estimated = True
                    estimate_reasons.add("OPENING_COST_INFERRED")

            effective_cost = replay_cost
            if delta > 0 and replay_cost == 0 and unit_cost > 0:
                effective_cost = unit_cost
            elif delta > 0 and quantity > 0 and unit_cost == 0 and replay_cost > 0:
                effective_cost = replay_cost

            next_cost = moving_average_cost(
                quantity,
                unit_cost,
                delta,
                replay_cost,
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
