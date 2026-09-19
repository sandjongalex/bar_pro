"""The sole stock mutation gateway."""
from sqlalchemy import select

from app.extensions import db
from app.models import (
    InventoryLine,
    OrderLine,
    OrderReturnLine,
    Product,
    PurchaseLine,
    StockBalance,
    StockMovement,
    utcnow,
)
from app.permissions import permissions
from app.stock_valuation import moving_average_cost, remove_receipt_cost
from app.validation import number

TYPES = {"INITIAL", "PURCHASE", "SALE", "RETURN", "LOSS", "ADJUSTMENT", "INVENTORY_ADJUSTMENT"}


class StockError(ValueError):
    pass


class StockService:
    def move(self, actor, bar_id, product_id, movement_type, quantity_delta, reason, **source):
        if movement_type not in TYPES:
            raise StockError("INVALID_MOVEMENT_TYPE")
        if movement_type in {"INITIAL", "ADJUSTMENT", "LOSS", "INVENTORY_ADJUSTMENT"}:
            permissions.require(actor, "inventory.adjust", bar_id)
        elif movement_type in {"SALE", "RETURN"}:
            permissions.require(actor, "orders.edit", bar_id)
        else:
            permissions.require(actor, "purchases.manage", bar_id)
        if not reason or not reason.strip():
            raise StockError("REASON_REQUIRED")
        delta = number(quantity_delta, 6)
        if movement_type in {"INITIAL", "PURCHASE", "RETURN"} and delta <= 0:
            raise StockError("INVALID_DIRECTION")
        if movement_type in {"SALE", "LOSS"} and delta >= 0:
            raise StockError("INVALID_DIRECTION")
        if not delta:
            raise StockError("ZERO_QUANTITY")

        product = db.session.scalar(
            select(Product).where(Product.bar_id == bar_id, Product.id == product_id).with_for_update()
        )
        if not product:
            raise LookupError("NOT_FOUND")
        balance = db.session.scalar(
            select(StockBalance)
            .where(StockBalance.bar_id == bar_id, StockBalance.product_id == product_id)
            .with_for_update()
        )
        if not balance:
            balance = StockBalance(bar_id=bar_id, product_id=product_id, quantity=0, version=0)
            db.session.add(balance)
            db.session.flush()
        if movement_type == "INITIAL" and db.session.scalar(
            select(StockMovement.id)
            .where(StockMovement.bar_id == bar_id, StockMovement.product_id == product_id)
            .limit(1)
        ):
            raise StockError("INITIAL_ALREADY_RECORDED")

        has_unit_override = "unit_snapshot" in source
        has_cost_override = "unit_cost_snapshot" in source
        unit = source.pop("unit_snapshot", product.base_unit)
        cost = source.pop("unit_cost_snapshot", product.valuation_unit_cost)
        for field, model in (
            ("purchase_line_id", PurchaseLine),
            ("order_line_id", OrderLine),
            ("order_return_line_id", OrderReturnLine),
            ("inventory_line_id", InventoryLine),
            ("reversal_of_id", StockMovement),
        ):
            if source.get(field) is not None:
                linked = db.session.get(model, source[field])
                if not linked or linked.bar_id != bar_id or linked.product_id != product_id:
                    raise LookupError("NOT_FOUND")
                if field == "order_return_line_id":
                    linked = db.session.get(OrderLine, linked.order_line_id)
                if not has_cost_override and hasattr(linked, "unit_cost_snapshot"):
                    cost = linked.unit_cost_snapshot
                if not has_unit_override and hasattr(linked, "unit_snapshot"):
                    unit = linked.unit_snapshot

        new = balance.quantity + delta
        if new < 0:
            raise StockError("INSUFFICIENT_STOCK")
        try:
            new_valuation = moving_average_cost(
                balance.quantity,
                product.valuation_unit_cost,
                delta,
                cost,
            )
        except ValueError as exc:
            raise StockError(str(exc)) from exc

        movement = StockMovement(
            bar_id=bar_id,
            product_id=product_id,
            movement_type=movement_type,
            quantity_delta=delta,
            unit_snapshot=unit,
            unit_cost_snapshot=cost,
            reason=reason.strip(),
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
            **source,
            manual_kind=movement_type if movement_type in {"INITIAL", "ADJUSTMENT"} else None,
        )
        db.session.add(movement)
        balance.quantity = new
        balance.version += 1
        product.valuation_unit_cost = new_valuation
        return movement

    def reverse_purchase_receipt(self, actor, bar_id, purchase_line_id, reason):
        """Reverse one unconsumed posted purchase receipt without deleting history.

        If stock has already left the product after the receipt (sale, loss or
        negative adjustment), cancelling that historical receipt would require a
        COGS restatement. The service blocks that ambiguous case rather than
        silently corrupting the current moving-average valuation.
        """
        permissions.require(actor, "purchases.manage", bar_id)
        if not reason or not reason.strip():
            raise StockError("REASON_REQUIRED")

        source = db.session.scalar(
            select(StockMovement)
            .where(
                StockMovement.bar_id == bar_id,
                StockMovement.purchase_line_id == purchase_line_id,
                StockMovement.movement_type == "PURCHASE",
            )
            .with_for_update()
        )
        if not source:
            raise LookupError("NOT_FOUND")
        if db.session.scalar(
            select(StockMovement.id).where(
                StockMovement.bar_id == bar_id,
                StockMovement.reversal_of_id == source.id,
            )
        ):
            raise StockError("ALREADY_REVERSED")

        later_real_outflow = db.session.scalar(
            select(StockMovement.id)
            .where(
                StockMovement.bar_id == bar_id,
                StockMovement.product_id == source.product_id,
                StockMovement.id > source.id,
                StockMovement.quantity_delta < 0,
                StockMovement.reversal_of_id.is_(None),
            )
            .limit(1)
        )
        if later_real_outflow:
            raise StockError("PURCHASE_REVERSAL_AFTER_OUTFLOW")

        product = db.session.scalar(
            select(Product)
            .where(Product.bar_id == bar_id, Product.id == source.product_id)
            .with_for_update()
        )
        balance = db.session.scalar(
            select(StockBalance)
            .where(StockBalance.bar_id == bar_id, StockBalance.product_id == source.product_id)
            .with_for_update()
        )
        if not product or not balance:
            raise LookupError("NOT_FOUND")

        new_quantity = balance.quantity - source.quantity_delta
        if new_quantity < 0:
            raise StockError("INSUFFICIENT_STOCK")
        try:
            new_valuation = remove_receipt_cost(
                balance.quantity,
                product.valuation_unit_cost,
                source.quantity_delta,
                source.unit_cost_snapshot,
            )
        except ValueError as exc:
            raise StockError(str(exc)) from exc

        reversal = StockMovement(
            bar_id=bar_id,
            product_id=source.product_id,
            movement_type="ADJUSTMENT",
            quantity_delta=-source.quantity_delta,
            unit_snapshot=source.unit_snapshot,
            unit_cost_snapshot=source.unit_cost_snapshot,
            reversal_of_id=source.id,
            manual_kind=None,
            reason=reason.strip(),
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(reversal)
        balance.quantity = new_quantity
        balance.version += 1
        product.valuation_unit_cost = new_valuation
        return reversal


stock_service = StockService()
