"""Order lifecycle and its only stock integration point."""
from decimal import Decimal
import uuid

from sqlalchemy import select, func

from app.audit import record
from app.extensions import db
from app.models import (
    Bar,
    BarTable,
    Customer,
    Order,
    OrderLine,
    OrderReturn,
    OrderReturnLine,
    Payment,
    Product,
    StaffAssignment,
    StockMovement,
    utcnow,
)
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text


class OrderService:
    def _active_assignment(self, actor, bar_id):
        if actor.category != "EMPLOYEE":
            return None
        return db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.user_id == actor.id,
                StaffAssignment.ended_at.is_(None),
            )
        )

    def create(
        self,
        actor,
        bar_id,
        reference,
        lines,
        table_id=None,
        customer_id=None,
        notes=None,
        invoice_name=None,
    ):
        """Create an order in the waiting state without touching stock.

        SERVER-created orders are linked to their active staff assignment so the
        cashier can identify the server and the server can retrieve only their
        own operational queue. ``invoice_name`` is the optional human-facing
        label used to recognize the bill quickly; when omitted, an attached
        customer's display name remains the snapshot fallback.
        """
        permissions.require(actor, "orders.create", bar_id)

        table = None
        customer = None
        if table_id is not None:
            table = db.session.get(BarTable, table_id)
            if not table or table.bar_id != bar_id or not table.is_active:
                raise LookupError("NOT_FOUND")
        if customer_id is not None:
            customer = db.session.get(Customer, customer_id)
            if not customer or customer.bar_id != bar_id or not customer.is_active:
                raise LookupError("NOT_FOUND")

        assignment = self._active_assignment(actor, bar_id)
        assigned_staff_id = assignment.id if assignment and assignment.role == "SERVER" else None
        human_invoice_name = required_text(invoice_name, 120) if invoice_name else None

        order = Order(
            bar_id=bar_id,
            reference=required_text(reference, 64),
            table_id=table_id,
            customer_id=customer_id,
            assigned_staff_id=assigned_staff_id,
            status="DRAFT",  # UI: En attente / A livrer
            payment_status="UNPAID",
            notes=(required_text(notes, 500) if notes else None),
            currency=db.session.get(Bar, bar_id).currency,
            customer_name_snapshot=(
                human_invoice_name or (customer.display_name if customer else None)
            ),
            table_label_snapshot=table.label if table else None,
            subtotal_amount=0,
            discount_amount=0,
            tax_amount=0,
            total_amount=0,
            created_by_id=actor.id,
        )
        db.session.add(order)
        db.session.flush()
        self.replace_lines(actor, order, lines)
        record(actor, bar_id, "orders.create", "orders", order.id, order.reference)
        return order

    def replace_lines(self, actor, order, lines):
        permissions.require(actor, "orders.edit", order.bar_id)
        if order.status != "DRAFT":
            raise ValueError("ORDER_IMMUTABLE")
        if not lines or len({x["product_id"] for x in lines}) != len(lines):
            raise ValueError("INVALID_LINES")

        assignment = self._active_assignment(actor, order.bar_id)
        if assignment and assignment.role == "SERVER" and order.assigned_staff_id != assignment.id:
            raise PermissionError("FORBIDDEN")

        validated = []
        for data in lines:
            product = db.session.scalar(
                select(Product).where(
                    Product.id == data["product_id"],
                    Product.bar_id == order.bar_id,
                    Product.is_active.is_(True),
                )
            )
            if not product:
                raise LookupError("NOT_FOUND")
            qty = number(data["quantity"], 6, positive=True)
            amount = number(qty * product.sale_price)
            validated.append((data, product, qty, amount))

        for old in db.session.scalars(
            select(OrderLine).where(OrderLine.order_id == order.id, OrderLine.bar_id == order.bar_id)
        ):
            db.session.delete(old)
        db.session.flush()

        total = Decimal("0")
        for n, (data, product, qty, amount) in enumerate(validated, 1):
            total += amount
            db.session.add(
                OrderLine(
                    bar_id=order.bar_id,
                    order_id=order.id,
                    product_id=product.id,
                    line_no=n,
                    product_name_snapshot=product.name,
                    unit_snapshot=product.base_unit,
                    quantity=qty,
                    note=(required_text(data.get("note"), 500) if data.get("note") else None),
                    unit_sale_price_snapshot=product.sale_price,
                    unit_cost_snapshot=product.valuation_unit_cost,
                    subtotal_amount=amount,
                    discount_amount=0,
                    tax_amount=0,
                    total_amount=amount,
                )
            )
        order.subtotal_amount = order.total_amount = number(total)

    def append_note(self, actor, bar_id, order_id, note):
        """Attach an operational note without changing stock or payment state."""
        permissions.require(actor, "orders.edit", bar_id)
        text = required_text(note, 220)
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        if not order or order.status not in {"DRAFT", "CONFIRMED"}:
            raise ValueError("ORDER_NOT_EDITABLE")

        assignment = self._active_assignment(actor, bar_id)
        if assignment and assignment.role == "SERVER" and order.assigned_staff_id != assignment.id:
            raise PermissionError("FORBIDDEN")

        prefix = actor.display_name or "Utilisateur"
        addition = f"{prefix}: {text}"
        combined = f"{order.notes}\n{addition}" if order.notes else addition
        if len(combined) > 500:
            raise ValueError("ORDER_NOTES_TOO_LONG")
        order.notes = combined
        record(actor, bar_id, "orders.note", "orders", order.id, text)
        return order

    def confirm(self, actor, bar_id, order_id):
        """Cashier confirms delivery; stock is deducted exactly here."""
        permissions.require(actor, "orders.deliver", bar_id)
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        if not order or order.status != "DRAFT":
            raise ValueError("ORDER_NOT_DRAFT")
        lines = list(db.session.scalars(select(OrderLine).where(OrderLine.order_id == order.id)))
        if not lines:
            raise ValueError("ORDER_EMPTY")
        for line in lines:
            stock_service.move(
                actor,
                bar_id,
                line.product_id,
                "SALE",
                -line.quantity,
                f"Livraison commande {order.reference}",
                order_line_id=line.id,
            )
        order.status = "CONFIRMED"  # UI: A payer
        order.posted_at = utcnow()
        record(actor, bar_id, "orders.deliver", "orders", order.id, order.reference)
        return order

    def adjust_confirmed(self, actor, bar_id, order_id, lines, reason):
        """Apply only the quantity delta to an unpaid delivered order."""
        permissions.require(actor, "orders.edit", bar_id)
        if not reason or not reason.strip():
            raise ValueError("REASON_REQUIRED")
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        if not order or order.status != "CONFIRMED" or order.payment_status != "UNPAID":
            raise ValueError("ORDER_NOT_EDITABLE")
        if not lines or len({x["order_line_id"] for x in lines}) != len(lines):
            raise ValueError("INVALID_LINES")
        indexed = {
            line.id: line
            for line in db.session.scalars(
                select(OrderLine).where(OrderLine.order_id == order.id, OrderLine.bar_id == bar_id)
            )
        }
        for item in lines:
            line = indexed.get(item["order_line_id"])
            if not line:
                raise LookupError("NOT_FOUND")
            target = number(item["quantity"], 6, positive=True)
            delta = target - line.quantity
            if not delta:
                continue
            stock_service.move(
                actor,
                bar_id,
                line.product_id,
                "SALE" if delta > 0 else "RETURN",
                -delta,
                f"Correction commande {order.reference}: {reason}",
                unit_snapshot=line.unit_snapshot,
                unit_cost_snapshot=line.unit_cost_snapshot,
            )
            line.quantity = target
            line.subtotal_amount = line.total_amount = number(target * line.unit_sale_price_snapshot)
        order.subtotal_amount = order.total_amount = number(
            sum((line.total_amount for line in indexed.values()), Decimal("0"))
        )
        record(actor, bar_id, "orders.adjust", "orders", order.id, reason)
        return order

    def serve(self, actor, bar_id, order_id):
        """Legacy terminal state kept for existing data/API compatibility."""
        permissions.require(actor, "orders.edit", bar_id)
        order = db.session.get(Order, order_id)
        if not order or order.bar_id != bar_id or order.status != "CONFIRMED":
            raise ValueError("ORDER_NOT_CONFIRMED")
        order.status = "SERVED"
        order.closed_at = utcnow()
        return order

    def cancel(self, actor, bar_id, order_id, reason):
        permissions.require(actor, "orders.edit", bar_id)
        if not reason or not reason.strip():
            raise ValueError("REASON_REQUIRED")
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        if not order or order.status not in {"DRAFT", "CONFIRMED"}:
            raise ValueError("ORDER_NOT_CANCELLABLE")

        assignment = self._active_assignment(actor, bar_id)
        if assignment and assignment.role == "SERVER":
            if order.assigned_staff_id != assignment.id or order.status != "DRAFT":
                raise PermissionError("FORBIDDEN")

        if order.status == "DRAFT":
            order.status = "CANCELLED"
            order.cancelled_at = utcnow()
            record(actor, bar_id, "orders.cancel", "orders", order.id, reason)
            return order

        from app.finance_totals import order_balance
        if order_balance(order)["net_settled"] != 0:
            raise ValueError("REFUND_REQUIRED")
        for line in db.session.scalars(select(OrderLine).where(OrderLine.order_id == order.id)):
            source = db.session.scalar(
                select(StockMovement).where(
                    StockMovement.bar_id == bar_id,
                    StockMovement.order_line_id == line.id,
                )
            )
            stock_service.move(
                actor,
                bar_id,
                line.product_id,
                "RETURN",
                line.quantity,
                f"Annulation {order.reference}: {reason}",
                reversal_of_id=source.id,
            )
        order.status = "CANCELLED"
        order.cancelled_at = utcnow()
        record(actor, bar_id, "orders.cancel", "orders", order.id, reason)
        return order

    def return_lines(self, actor, bar_id, order_id, lines, reason):
        permissions.require(actor, "orders.edit", bar_id)
        if not reason or not reason.strip():
            raise ValueError("REASON_REQUIRED")
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        # In the web cashier workflow, CONFIRMED already means physically delivered.
        # SERVED is retained for legacy/API data, so both states are returnable.
        if not order or order.status not in {"CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_RETURNABLE")
        if not lines or len({x["order_line_id"] for x in lines}) != len(lines):
            raise ValueError("INVALID_RETURN")
        originals = {
            x.id: x
            for x in db.session.scalars(
                select(OrderLine).where(OrderLine.order_id == order_id, OrderLine.bar_id == bar_id)
            )
        }
        returned = OrderReturn(
            bar_id=bar_id,
            order_id=order_id,
            reference="RET-" + uuid.uuid4().hex,
            status="POSTED",
            currency=order.currency,
            total_amount=0,
            reason=reason,
            posted_at=utcnow(),
            created_by_id=actor.id,
        )
        db.session.add(returned)
        db.session.flush()
        total = Decimal("0")
        for item in lines:
            line = originals.get(item["order_line_id"])
            qty = number(item["quantity"], 6, positive=True)
            disposition = item["disposition"]
            if not line or disposition not in {"RESTOCK", "LOSS"}:
                raise ValueError("INVALID_RETURN")
            prior = db.session.scalar(
                select(func.coalesce(func.sum(OrderReturnLine.quantity), 0))
                .join(OrderReturn, OrderReturn.id == OrderReturnLine.order_return_id)
                .where(
                    OrderReturnLine.bar_id == bar_id,
                    OrderReturnLine.order_line_id == line.id,
                    OrderReturn.status == "POSTED",
                )
            )
            if prior + qty > line.quantity:
                raise ValueError("RETURN_LIMIT_EXCEEDED")
            credit = number(qty * line.unit_sale_price_snapshot)
            total += credit
            detail = OrderReturnLine(
                bar_id=bar_id,
                order_return_id=returned.id,
                order_id=order_id,
                order_line_id=line.id,
                product_id=line.product_id,
                quantity=qty,
                restock_quantity=qty if disposition == "RESTOCK" else 0,
                credit_amount=credit,
            )
            db.session.add(detail)
            db.session.flush()
            if disposition == "RESTOCK":
                stock_service.move(
                    actor,
                    bar_id,
                    line.product_id,
                    "RETURN",
                    qty,
                    f"Retour {order.reference}: {reason}",
                    order_return_line_id=detail.id,
                )
        returned.total_amount = number(total)
        from app.finance_totals import order_balance
        order_balance(order, update=True)
        record(actor, bar_id, "orders.return", "order_returns", returned.id, reason)
        return returned


order_service = OrderService()
