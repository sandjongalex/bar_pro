"""Business rules for persistent additions attached to one customer order."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.audit import record
from app.extensions import db
from app.models import Order, Product, StaffAssignment, utcnow
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text


class OrderSuborderService:
    def _active_assignment(self, actor, bar_id: int):
        if actor.category != "EMPLOYEE":
            return None
        return db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.user_id == actor.id,
                StaffAssignment.ended_at.is_(None),
            )
        )

    def create_cashier_addition(
        self,
        actor,
        bar_id: int,
        order_id: int,
        lines,
        note: str | None = None,
    ) -> OrderSuborder:
        """Create one delivered sub-order that awaits validation by the server.

        Products stay in their own sub-order lines and are never merged into the
        original order lines. Stock is deducted immediately because the cashier
        has physically served this additional round. The parent invoice totals
        stay unchanged until the assigned server validates the sub-order.
        """
        permissions.require(actor, "orders.deliver", bar_id)

        assignment = self._active_assignment(actor, bar_id)
        if assignment and assignment.role != "CASHIER":
            raise PermissionError("FORBIDDEN")

        order = db.session.scalar(
            select(Order)
            .where(Order.id == order_id, Order.bar_id == bar_id)
            .with_for_update()
        )
        if not order:
            raise LookupError("NOT_FOUND")
        if order.status not in {"DRAFT", "CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_EDITABLE")
        if order.payment_status == "PAID":
            raise ValueError("ORDER_PAID")
        if order.assigned_staff_id is None:
            raise ValueError("ORDER_SERVER_REQUIRED")

        server_assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.id == order.assigned_staff_id,
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.role == "SERVER",
                StaffAssignment.ended_at.is_(None),
            )
        )
        if not server_assignment:
            raise ValueError("ORDER_SERVER_REQUIRED")

        if not lines:
            raise ValueError("INVALID_LINES")

        product_ids: set[int] = set()
        validated = []
        total = Decimal("0")
        for item in lines:
            try:
                product_id = int(item["product_id"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("INVALID_LINES") from None
            if product_id in product_ids:
                raise ValueError("INVALID_LINES")
            product_ids.add(product_id)

            product = db.session.scalar(
                select(Product).where(
                    Product.id == product_id,
                    Product.bar_id == bar_id,
                    Product.is_active.is_(True),
                )
            )
            if not product:
                raise LookupError("NOT_FOUND")

            quantity = number(item.get("quantity"), 6, positive=True)
            amount = number(quantity * product.sale_price)
            line_note = item.get("note")
            if line_note:
                line_note = required_text(line_note, 500)
            validated.append((product, quantity, amount, line_note))
            total += amount

        sequence_no = int(
            db.session.scalar(
                select(func.coalesce(func.max(OrderSuborder.sequence_no), 0)).where(
                    OrderSuborder.bar_id == bar_id,
                    OrderSuborder.order_id == order.id,
                )
            )
            or 0
        ) + 1

        now = utcnow()
        suborder = OrderSuborder(
            bar_id=bar_id,
            order_id=order.id,
            sequence_no=sequence_no,
            assigned_staff_id=server_assignment.id,
            status="PENDING_VALIDATION",
            delivery_status="DELIVERED",
            currency=order.currency,
            subtotal_amount=number(total),
            discount_amount=0,
            tax_amount=0,
            total_amount=number(total),
            note=(required_text(note, 500) if note else None),
            created_by_id=actor.id,
            delivered_by_id=actor.id,
            delivered_at=now,
        )
        db.session.add(suborder)
        db.session.flush()

        for line_no, (product, quantity, amount, line_note) in enumerate(validated, 1):
            line = OrderSuborderLine(
                bar_id=bar_id,
                order_suborder_id=suborder.id,
                order_id=order.id,
                product_id=product.id,
                line_no=line_no,
                product_name_snapshot=product.name,
                unit_snapshot=product.base_unit,
                quantity=quantity,
                note=line_note,
                unit_sale_price_snapshot=product.sale_price,
                unit_cost_snapshot=product.valuation_unit_cost,
                subtotal_amount=amount,
                discount_amount=0,
                tax_amount=0,
                total_amount=amount,
            )
            db.session.add(line)
            db.session.flush()
            stock_service.move(
                actor,
                bar_id,
                product.id,
                "SALE",
                -quantity,
                f"Sous-commande {sequence_no} de {order.reference}",
                unit_snapshot=line.unit_snapshot,
                unit_cost_snapshot=line.unit_cost_snapshot,
            )

        record(
            actor,
            bar_id,
            "orders.suborder.create",
            "order_suborders",
            suborder.id,
            f"Sous-commande {sequence_no} de {order.reference}",
        )
        return suborder


order_suborder_service = OrderSuborderService()
