"""Shared financial totals. Refunds reduce receipts, returns reduce sales."""
from decimal import Decimal
from sqlalchemy import select, func
from app.extensions import db
from app.models import Payment, Refund, OrderReturn


def total(model, column, **filters):
    return db.session.scalar(select(func.coalesce(func.sum(column), 0)).filter_by(**filters).select_from(model))


def order_balance(order, update=False):
    keys = dict(bar_id=order.bar_id, order_id=order.id)
    paid = total(Payment, Payment.amount_applied, **keys)
    refunded = total(Refund, Refund.amount, **keys)
    credits = total(OrderReturn, OrderReturn.total_amount, status="POSTED", **keys)
    sale = Decimal(0) if order.status == "CANCELLED" else order.total_amount - credits
    net = paid - refunded
    if update:
        order.payment_status = "PAID" if net >= sale else "PARTIAL" if net else "UNPAID"
    return dict(total_paid=paid, total_refunded=refunded, return_credit=credits,
                net_sale=sale, net_paid=net, amount_due=max(sale-net, Decimal(0)),
                refundable_overpayment=max(net-sale, Decimal(0)))
