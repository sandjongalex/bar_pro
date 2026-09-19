"""Role-aware editing of active orders.

Servers may edit only their own DRAFT orders. Cashiers/admin roles may edit
DRAFT orders and delivered (CONFIRMED/SERVED) orders until they are fully paid.
Delivered reductions are persisted as posted returns so stock/history stay
consistent without deleting historical order lines.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.audit import record
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Order, OrderLine, OrderReturn, OrderReturnLine, Product, StaffAssignment
from app.order_services import order_service
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text


class OrderEditService:
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

    def _targets(self, lines) -> dict[int, Decimal]:
        if not lines:
            raise ValueError("INVALID_LINES")
        targets: dict[int, Decimal] = {}
        for item in lines:
            try:
                product_id = int(item["product_id"])
            except (KeyError, TypeError, ValueError):
                raise ValueError("INVALID_LINES") from None
            if product_id in targets:
                raise ValueError("INVALID_LINES")
            targets[product_id] = number(item.get("quantity"), 6, positive=True)
        return targets

    def _returned_by_line(self, bar_id: int, order_id: int) -> dict[int, Decimal]:
        rows = db.session.execute(
            select(
                OrderReturnLine.order_line_id,
                func.coalesce(func.sum(OrderReturnLine.quantity), 0),
            )
            .join(OrderReturn, OrderReturn.id == OrderReturnLine.order_return_id)
            .where(
                OrderReturnLine.bar_id == bar_id,
                OrderReturnLine.order_id == order_id,
                OrderReturn.status == "POSTED",
            )
            .group_by(OrderReturnLine.order_line_id)
        ).all()
        return {line_id: Decimal(quantity or 0) for line_id, quantity in rows}

    def edit(self, actor, bar_id: int, order_id: int, lines, reason: str | None = None):
        """Replace the *effective* product list of an editable order.

        Omit a product from ``lines`` to remove it. Quantities must stay positive
        for products that remain on the order.
        """
        permissions.require(actor, "orders.edit", bar_id)
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        if not order:
            raise LookupError("NOT_FOUND")
        if order.payment_status == "PAID":
            raise ValueError("ORDER_PAID")
        if order.status not in {"DRAFT", "CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_EDITABLE")

        assignment = self._active_assignment(actor, bar_id)
        if assignment and assignment.role == "SERVER":
            if order.assigned_staff_id != assignment.id or order.status != "DRAFT":
                raise PermissionError("FORBIDDEN")

        targets = self._targets(lines)

        # Before delivery there is no stock movement yet: replacing lines is safe.
        if order.status == "DRAFT":
            normalized = [
                {"product_id": product_id, "quantity": quantity}
                for product_id, quantity in targets.items()
            ]
            order_service.replace_lines(actor, order, normalized)
            record(actor, bar_id, "orders.edit", "orders", order.id, "Modification avant livraison")
            return order

        # A server is never allowed to change a delivered order. Cashier/admin only.
        if assignment and assignment.role == "SERVER":
            raise PermissionError("FORBIDDEN")

        reason_text = (reason or "").strip() or "Modification de la commande par la caisse"
        reason_text = required_text(reason_text, 500)

        existing_lines = list(
            db.session.scalars(
                select(OrderLine)
                .where(OrderLine.bar_id == bar_id, OrderLine.order_id == order.id)
                .order_by(OrderLine.line_no, OrderLine.id)
            )
        )
        existing_by_product: dict[int, OrderLine] = {}
        for line in existing_lines:
            if line.product_id in existing_by_product:
                raise ValueError("ORDER_LINES_AMBIGUOUS")
            existing_by_product[line.product_id] = line

        returned_by_line = self._returned_by_line(bar_id, order.id)
        effective_qty = {
            line.product_id: Decimal(line.quantity) - returned_by_line.get(line.id, Decimal("0"))
            for line in existing_lines
        }

        products: dict[int, Product] = {}
        target_subtotal = Decimal("0")
        for product_id, target_qty in targets.items():
            line = existing_by_product.get(product_id)
            current_qty = effective_qty.get(product_id, Decimal("0"))
            if line is not None:
                price = Decimal(line.unit_sale_price_snapshot)
                if target_qty > current_qty:
                    product = db.session.scalar(
                        select(Product).where(
                            Product.id == product_id,
                            Product.bar_id == bar_id,
                            Product.is_active.is_(True),
                        )
                    )
                    if not product:
                        raise LookupError("NOT_FOUND")
                    products[product_id] = product
            else:
                product = db.session.scalar(
                    select(Product).where(
                        Product.id == product_id,
                        Product.bar_id == bar_id,
                        Product.is_active.is_(True),
                    )
                )
                if not product:
                    raise LookupError("NOT_FOUND")
                products[product_id] = product
                price = Decimal(product.sale_price)
            target_subtotal += number(target_qty * price)

        discount = Decimal(order.discount_amount or 0)
        tax = Decimal(order.tax_amount or 0)
        if discount > target_subtotal:
            raise ValueError("ORDER_NOT_EDITABLE")
        target_total = target_subtotal - discount + tax
        current_balance = order_balance(order)
        if target_total < Decimal(current_balance["net_settled"] or 0):
            raise ValueError("ORDER_TOTAL_BELOW_SETTLED")

        # First apply additions/increases so a mixed edit never creates a temporary
        # under-paid/over-paid state before reductions are processed.
        next_line_no = max((line.line_no for line in existing_lines), default=0) + 1
        for product_id, target_qty in targets.items():
            line = existing_by_product.get(product_id)
            current_qty = effective_qty.get(product_id, Decimal("0"))
            delta = target_qty - current_qty
            if delta <= 0:
                continue

            if line is None:
                product = products[product_id]
                amount = number(target_qty * product.sale_price)
                line = OrderLine(
                    bar_id=bar_id,
                    order_id=order.id,
                    product_id=product.id,
                    line_no=next_line_no,
                    product_name_snapshot=product.name,
                    unit_snapshot=product.base_unit,
                    quantity=target_qty,
                    note=None,
                    unit_sale_price_snapshot=product.sale_price,
                    unit_cost_snapshot=product.valuation_unit_cost,
                    subtotal_amount=amount,
                    discount_amount=0,
                    tax_amount=0,
                    total_amount=amount,
                )
                next_line_no += 1
                db.session.add(line)
                db.session.flush()
                existing_lines.append(line)
                existing_by_product[product_id] = line
                stock_service.move(
                    actor,
                    bar_id,
                    product_id,
                    "SALE",
                    -target_qty,
                    f"Ajout à la commande {order.reference}: {reason_text}",
                    order_line_id=line.id,
                )
            else:
                stock_service.move(
                    actor,
                    bar_id,
                    product_id,
                    "SALE",
                    -delta,
                    f"Ajout à la commande {order.reference}: {reason_text}",
                    unit_snapshot=line.unit_snapshot,
                    unit_cost_snapshot=line.unit_cost_snapshot,
                )
                line.quantity = number(Decimal(line.quantity) + delta, 6, positive=True)
                line.subtotal_amount = line.total_amount = number(
                    Decimal(line.quantity) * Decimal(line.unit_sale_price_snapshot)
                )

        order.subtotal_amount = order.total_amount = number(
            sum((Decimal(line.total_amount) for line in existing_lines), Decimal("0"))
        )

        reductions = []
        for line in existing_lines:
            current_qty = effective_qty.get(line.product_id, Decimal("0"))
            target_qty = targets.get(line.product_id, Decimal("0"))
            if target_qty >= current_qty:
                continue
            reductions.append(
                {
                    "order_line_id": line.id,
                    "quantity": current_qty - target_qty,
                    "disposition": "RESTOCK",
                }
            )

        if reductions:
            order_service.return_lines(actor, bar_id, order.id, reductions, reason_text)

        order_balance(order, update=True)
        record(actor, bar_id, "orders.adjust", "orders", order.id, reason_text)
        return order


order_edit_service = OrderEditService()
