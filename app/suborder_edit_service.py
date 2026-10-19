"""Cashier correction of one sub-order without merging its historical ownership."""
from decimal import Decimal
from sqlalchemy import select

from app.audit import record
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Order, Product, StaffAssignment
from app.order_concurrency import revision_matches
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text
from app.order_suborder_service import order_suborder_service


def edit_cashier_suborder(actor, bar_id, order_id, suborder_id, lines,
                          *, expected_revision, reason=None):
    permissions.require(actor, "orders.edit", bar_id)
    if actor.category == "EMPLOYEE":
        assignment = order_suborder_service._active_assignment(actor, bar_id)
        if not assignment or assignment.role != "CASHIER":
            raise PermissionError("FORBIDDEN")

    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar_id, Order.id == order_id).with_for_update())
    if not order:
        raise LookupError("NOT_FOUND")
    if not expected_revision:
        raise ValueError("ORDER_REVISION_REQUIRED")
    if not revision_matches(order, expected_revision):
        raise ValueError("ORDER_CONFLICT")
    if order.payment_status == "PAID":
        raise ValueError("ORDER_PAID")
    if order.status not in {"DRAFT", "CONFIRMED", "SERVED"}:
        raise ValueError("ORDER_NOT_EDITABLE")

    sub = db.session.scalar(select(OrderSuborder).where(
        OrderSuborder.bar_id == bar_id, OrderSuborder.order_id == order_id,
        OrderSuborder.id == suborder_id).with_for_update())
    if not sub:
        raise LookupError("NOT_FOUND")
    if sub.status not in {"VALIDATED", "PENDING_VALIDATION"}:
        raise ValueError("SUBORDER_NOT_EDITABLE")

    targets = {}
    for item in lines or []:
        try:
            pid = int(item["product_id"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("INVALID_LINES") from None
        if pid in targets:
            raise ValueError("INVALID_LINES")
        targets[pid] = number(item.get("quantity"), 6, positive=True)
    if not targets:
        raise ValueError("INVALID_LINES")

    original = list(db.session.scalars(select(OrderSuborderLine).where(
        OrderSuborderLine.bar_id == bar_id, OrderSuborderLine.order_id == order_id,
        OrderSuborderLine.order_suborder_id == suborder_id
    ).order_by(OrderSuborderLine.line_no, OrderSuborderLine.id)))
    existing = {line.product_id: line for line in original}
    if len(existing) != len(original):
        raise ValueError("ORDER_LINES_AMBIGUOUS")
    def snapshot(rows):
        return [dict(line_id=r.id, product_id=r.product_id,
                     name=r.product_name_snapshot, quantity=str(r.quantity),
                     unit_price=str(r.unit_sale_price_snapshot),
                     amount=str(r.total_amount)) for r in rows]

    before = snapshot(original)
    old_subtotal = Decimal(sub.subtotal_amount)
    old_total = Decimal(sub.total_amount)
    delivered = sub.delivery_status == "DELIVERED"
    new_products = {}
    proposed_subtotal = Decimal(0)
    for pid, qty in targets.items():
        line = existing.get(pid)
        if line:
            price = Decimal(line.unit_sale_price_snapshot)
        else:
            product = db.session.scalar(select(Product).where(
                Product.id == pid, Product.bar_id == bar_id,
                Product.is_active.is_(True)))
            if not product:
                raise LookupError("NOT_FOUND")
            new_products[pid] = product
            price = Decimal(product.sale_price)
        proposed_subtotal += number(qty * price)

    proposed_total = number(
        proposed_subtotal - Decimal(sub.discount_amount) + Decimal(sub.tax_amount))
    if proposed_total < 0:
        raise ValueError("INVALID_LINES")
    delta_total = proposed_total - old_total
    if sub.status == "VALIDATED":
        balance = order_balance(order, locking=True)
        if Decimal(balance["net_sale"]) + delta_total < Decimal(balance["net_settled"]):
            raise ValueError("ORDER_TOTAL_BELOW_SETTLED")

    if not delivered:
        # Pending rounds have not touched stock. Reserve no stock here either.
        # Validate positive changes using the same row locks as delivery.
        increases = []
        for pid, qty in targets.items():
            previous_qty = Decimal(existing[pid].quantity) if pid in existing else Decimal(0)
            if qty > previous_qty:
                p = new_products.get(pid) or db.session.scalar(select(Product).where(
                    Product.bar_id == bar_id, Product.id == pid))
                increases.append((p, qty, None, None))
        order_suborder_service._require_stock_for_server_addition(bar_id, increases)

    next_line_no = max((line.line_no for line in original), default=0)
    for pid in sorted(set(existing) | set(targets)):
        line = existing.get(pid)
        old_qty = Decimal(line.quantity) if line else Decimal(0)
        qty = targets.get(pid, Decimal(0))
        diff = qty - old_qty
        if delivered and diff:
            product = new_products.get(pid)
            stock_service.move(
                actor, bar_id, pid, "SALE" if diff > 0 else "RETURN", -diff,
                f"Correction sous-commande {sub.sequence_no} de {order.reference}",
                unit_snapshot=line.unit_snapshot if line else product.base_unit,
                unit_cost_snapshot=line.unit_cost_snapshot if line else product.valuation_unit_cost,
            )
        if line and qty == 0:
            db.session.delete(line)
        elif line and diff:
            line.quantity = qty
            line.subtotal_amount = line.total_amount = number(qty * line.unit_sale_price_snapshot)
        elif not line:
            p = new_products[pid]
            next_line_no += 1
            amount = number(qty * p.sale_price)
            db.session.add(OrderSuborderLine(
                bar_id=bar_id, order_id=order_id, order_suborder_id=suborder_id,
                product_id=pid, line_no=next_line_no,
                product_name_snapshot=p.name, unit_snapshot=p.base_unit,
                unit_sale_price_snapshot=p.sale_price,
                unit_cost_snapshot=p.valuation_unit_cost,
                quantity=qty, note=None, subtotal_amount=amount,
                discount_amount=0, tax_amount=0, total_amount=amount,
            ))
    sub.subtotal_amount = number(proposed_subtotal)
    sub.total_amount = proposed_total
    if sub.status == "VALIDATED":
        order.subtotal_amount = number(Decimal(order.subtotal_amount) + proposed_subtotal - old_subtotal)
        order.total_amount = number(Decimal(order.total_amount) + delta_total)
        order_balance(order, update=True)
    db.session.flush()
    current = list(db.session.scalars(select(OrderSuborderLine).where(
        OrderSuborderLine.bar_id == bar_id, OrderSuborderLine.order_id == order_id,
        OrderSuborderLine.order_suborder_id == suborder_id
    ).order_by(OrderSuborderLine.line_no, OrderSuborderLine.id)))
    record(actor, bar_id, "orders.suborder.edit", "order_suborders", sub.id,
           required_text((reason or "").strip() or "Modification par la caisse", 500),
           changes=dict(order_id=order.id, sequence_no=sub.sequence_no,
                        status=sub.status, delivery_status=sub.delivery_status,
                        before=before, after=snapshot(current),
                        total_before=str(old_total), total_after=str(proposed_total)))
    return sub
