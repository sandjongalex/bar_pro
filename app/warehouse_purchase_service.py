"""Physical supplier receipts belong to MAIN, never to CASHIER.

The historical stock balance remains the sales source of truth until the
separate warehouse cutover. Before cutover we retain a MAIN receipt record
without mutating provisional V2 balances; at LIVE we also journal MAIN only.
Every caller must keep legacy stock, purchase status and these rows within one
database transaction.
"""
from decimal import Decimal

from sqlalchemy import select

from app.audit import record
from app.extensions import db
from app.models import PurchaseLine, StockMovement, utcnow
from app.warehouse_models import WarehouseAllocation, WarehouseStockMovement
from app.warehouse_purchase_models import WarehousePurchaseReceiptLine
from app.warehouse_service import warehouse_service


class PurchaseWarehouseError(ValueError):
    pass


def _location_and_mode(bar_id):
    locations = warehouse_service.ensure_locations(bar_id)
    state = db.session.scalar(
        select(WarehouseAllocation).where(WarehouseAllocation.bar_id == bar_id).with_for_update()
    )
    return locations["MAIN"], ("LIVE" if state is not None and state.status == "LIVE" else "PRE_CUTOVER")


def record_main_receipt(actor, bar_id, purchase, lines):
    """Record a unique physical receipt per PurchaseLine; do not touch CASHIER."""
    main, mode = _location_and_mode(bar_id)
    if not lines:
        raise PurchaseWarehouseError("PURCHASE_EMPTY")
    for line in lines:
        if line.bar_id != bar_id or line.purchase_id != purchase.id:
            raise LookupError("NOT_FOUND")
        if db.session.scalar(select(WarehousePurchaseReceiptLine.id).where(
            WarehousePurchaseReceiptLine.bar_id == bar_id,
            WarehousePurchaseReceiptLine.purchase_line_id == line.id,
        )):
            raise PurchaseWarehouseError("PURCHASE_ALREADY_RECEIVED")
        movement_id = None
        if mode == "LIVE":
            movement = warehouse_service._move(
                actor=actor, bar_id=bar_id, product_id=line.product_id,
                location_id=main.id, kind="PURCHASE_IN",
                delta=Decimal(line.quantity),
                cost=Decimal(line.unit_cost_snapshot),
                reason=f"Réception fournisseur {purchase.reference} · ligne {line.id}",
            )
            db.session.flush()
            movement_id = movement.id
        receipt = WarehousePurchaseReceiptLine(
            bar_id=bar_id, purchase_line_id=line.id, location_id=main.id,
            product_id=line.product_id, quantity=line.quantity,
            unit_cost_snapshot=line.unit_cost_snapshot,
            mode=mode, warehouse_movement_id=movement_id,
            received_by_id=actor.id, received_at=utcnow(),
        )
        db.session.add(receipt)
    db.session.flush()
    record(actor, bar_id, "warehouse.purchase.main.receive", "purchases", purchase.id,
           f"Réception MAIN {purchase.reference} ({mode}); {len(lines)} ligne(s)")
    return mode


def reverse_main_receipt(actor, bar_id, purchase, lines, reason):
    """Reverse MAIN only, preserving receipt evidence and original cost snapshots."""
    receipts = {
        row.purchase_line_id: row for row in db.session.scalars(
            select(WarehousePurchaseReceiptLine).where(
                WarehousePurchaseReceiptLine.bar_id == bar_id,
                WarehousePurchaseReceiptLine.purchase_line_id.in_([line.id for line in lines]),
            ).with_for_update()
        )
    }
    # Older POSTED purchases predate this ledger: their old stock reversal
    # remains valid, but no synthetic MAIN movement may be invented.
    if not receipts:
        return
    if set(receipts) != {line.id for line in lines}:
        raise PurchaseWarehouseError("PARTIAL_PURCHASE_RECEIPT_LEDGER")
    main, _ = _location_and_mode(bar_id)
    for line in lines:
        receipt = receipts[line.id]
        if receipt.location_id != main.id or receipt.product_id != line.product_id:
            raise PurchaseWarehouseError("PURCHASE_LOCATION_MISMATCH")
        if receipt.reversed_at is not None:
            raise PurchaseWarehouseError("PURCHASE_ALREADY_REVERSED")
        if receipt.mode == "LIVE":
            # A transfer/loss after the MAIN receipt must not silently restate
            # a consumed lot's unit-cost valuation.
            later_outflow = db.session.scalar(select(WarehouseStockMovement.id).where(
                WarehouseStockMovement.bar_id == bar_id,
                WarehouseStockMovement.location_id == main.id,
                WarehouseStockMovement.product_id == line.product_id,
                WarehouseStockMovement.id > receipt.warehouse_movement_id,
                WarehouseStockMovement.quantity_delta < 0,
                WarehouseStockMovement.movement_type != "PURCHASE_REVERSAL",
            ).limit(1))
            if later_outflow:
                raise PurchaseWarehouseError("PURCHASE_REVERSAL_AFTER_MAIN_OUTFLOW")
            reversal = warehouse_service._move(
                actor=actor, bar_id=bar_id, product_id=line.product_id,
                location_id=main.id, kind="PURCHASE_REVERSAL",
                delta=-Decimal(receipt.quantity), cost=Decimal(receipt.unit_cost_snapshot),
                reason=f"Annulation réception fournisseur {purchase.reference}: {reason}",
            )
            db.session.flush()
            receipt.reversal_movement_id = reversal.id
        receipt.reversed_by_id = actor.id
        receipt.reversed_at = utcnow()
    db.session.flush()
    record(actor, bar_id, "warehouse.purchase.main.reverse", "purchases", purchase.id,
           f"Annulation MAIN {purchase.reference}: {reason}")
