"""Read-only V2 reconciliation against the stock movement journal.

Never adjusts stock, payments, invoices or debts. The movement ID is an
ordering cursor, not a physical measurement timestamp: counts entered after
customers receive goods still require human verification.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import select

from app.extensions import db
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_count_models import InventoryLineCountState
from app.models import InventoryLine, Product, StockBalance, StockMovement

ZERO = Decimal("0")


def _qty(value):
    return str(Decimal(value))


def inventory_live_reconciliation(inv, *, lock=False):
    assignment = db.session.scalar(
        select(InventoryCashierAssignment).where(
            InventoryCashierAssignment.bar_id == inv.bar_id,
            InventoryCashierAssignment.inventory_id == inv.id,
        )
    )
    if assignment is None:
        raise ValueError("INVENTORY_V2_REQUIRED")

    items_query = (
        select(InventoryLine, Product)
        .join(Product, (Product.bar_id == InventoryLine.bar_id)
              & (Product.id == InventoryLine.product_id))
        .where(InventoryLine.bar_id == inv.bar_id,
               InventoryLine.inventory_id == inv.id)
        .order_by(Product.name, Product.id)
    )
    items = db.session.execute(items_query.with_for_update() if lock else items_query).all()
    ids = [line.product_id for line, _ in items]
    states = {
        state.inventory_line_id: state
        for state in db.session.scalars(
            select(InventoryLineCountState).where(
                InventoryLineCountState.bar_id == inv.bar_id,
                InventoryLineCountState.inventory_line_id.in_(
                    [line.id for line, _ in items] or [-1]
                ),
            )
        )
    }
    balances_query = select(StockBalance).where(
        StockBalance.bar_id == inv.bar_id,
        StockBalance.product_id.in_(ids or [-1]),
    )
    balances = {
        bal.product_id: bal for bal in db.session.scalars(
            balances_query.with_for_update() if lock else balances_query
        )
    }
    baseline = assignment.movement_checkpoint_id
    movements = defaultdict(list)
    if baseline is not None:
        # One query for every relevant movement, including sales, returns,
        # losses, purchases, reversals, and inventory adjustments.
        movement_query = select(StockMovement).where(
            StockMovement.bar_id == inv.bar_id,
            StockMovement.product_id.in_(ids or [-1]),
            StockMovement.id > baseline,
        ).order_by(StockMovement.id)
        for mv in db.session.scalars(movement_query.with_for_update() if lock else movement_query):
            movements[mv.product_id].append(mv)

    result = []
    for line, product in items:
        state = states.get(line.id)
        current_balance = balances.get(line.product_id)
        actual = Decimal(current_balance.quantity) if current_balance else ZERO
        counted = Decimal(line.counted_quantity) if line.counted_quantity is not None else None
        row = {
            "product_id": str(line.product_id),
            "product_name": product.name,
            "base_unit": product.base_unit,
            "opening_stock": _qty(line.expected_quantity_snapshot),
            "current_machine": _qty(actual),
            "physical_count": _qty(counted) if counted is not None else None,
            "opening_checkpoint_id": str(baseline) if baseline is not None else None,
            "count_checkpoint_id": (
                str(state.movement_checkpoint_id)
                if state and state.movement_checkpoint_id is not None else None
            ),
            "counted_at": state.counted_at.isoformat() if state and state.counted_at else None,
            "expected_at_count": None,
            "expected_now": None,
            "projected_physical_now": None,
            "difference_now": None,
            "delta_before_count": None,
            "delta_after_count": None,
            "movement_breakdown": [],
            "status": "NOT_COUNTED",
            "review_required": False,
        }
        if baseline is None:
            row["status"] = "REFERENCE_MISSING"
            row["review_required"] = True
            result.append(row)
            continue
        if counted is not None and (not state or state.movement_checkpoint_id is None
                                   or state.counted_at is None):
            row["status"] = "COUNT_CHECKPOINT_MISSING"
            row["review_required"] = True
            result.append(row)
            continue

        checkpoint = int(state.movement_checkpoint_id) if counted is not None else None
        if checkpoint is not None and checkpoint < baseline:
            row["status"] = "INVALID_CHECKPOINT"
            row["review_required"] = True
            result.append(row)
            continue

        before = ZERO
        after = ZERO
        by_type = defaultdict(Decimal)
        total = ZERO
        for mv in movements[line.product_id]:
            qty = Decimal(mv.quantity_delta)
            total += qty
            by_type[mv.movement_type] += qty
            if checkpoint is not None:
                if mv.id <= checkpoint:
                    before += qty
                else:
                    after += qty

        expected_now = Decimal(line.expected_quantity_snapshot) + total
        row["expected_now"] = _qty(expected_now)
        row["movement_breakdown"] = [
            {"type": movement_type, "quantity_delta": _qty(value)}
            for movement_type, value in sorted(by_type.items())
        ]
        if counted is not None:
            at_count = Decimal(line.expected_quantity_snapshot) + before
            projection = counted + after
            row.update({
                "expected_at_count": _qty(at_count),
                "projected_physical_now": _qty(projection),
                "difference_now": _qty(projection - actual),
                "delta_before_count": _qty(before),
                "delta_after_count": _qty(after),
            })
        if expected_now != actual:
            # A mismatch means missing movements or out-of-band stock writes.
            row["status"] = "LEDGER_MISMATCH"
            row["review_required"] = True
        elif counted is None:
            row["status"] = "NOT_COUNTED"
        elif Decimal(row["projected_physical_now"]) < 0:
            row["status"] = "NEGATIVE_PROJECTION"
            row["review_required"] = True
        elif Decimal(row["difference_now"]) != 0:
            row["status"] = "DIFFERENCE"
            row["review_required"] = True
        else:
            row["status"] = "RECONCILED"
        result.append(row)

    return {
        "inventory_id": str(inv.id),
        "bar_id": str(inv.bar_id),
        "read_only": True,
        "baseline_available": baseline is not None,
        "total": len(result),
        "counted": sum(row["physical_count"] is not None for row in result),
        "requires_review": sum(bool(row["review_required"]) for row in result),
        "lines": result,
    }
