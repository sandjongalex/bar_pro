"""Shared financial totals. Refunds reduce receipts, returns reduce sales and customer credit can settle an order."""
from decimal import Decimal
from sqlalchemy import select, func

from app.change_voucher_models import ChangeVoucherTransaction
from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import Payment, Refund, OrderReturn


def total(model, column, **filters):
    return db.session.scalar(select(func.coalesce(func.sum(column), 0)).filter_by(**filters).select_from(model))


def order_balance(order, update=False):
    keys = dict(bar_id=order.bar_id, order_id=order.id)
    paid = Decimal(total(Payment, Payment.amount_applied, **keys) or 0)
    refunded = Decimal(total(Refund, Refund.amount, **keys) or 0)
    credits = Decimal(total(OrderReturn, OrderReturn.total_amount, status="POSTED", **keys) or 0)
    customer_credit = Decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
                CustomerLedgerEntry.bar_id == order.bar_id,
                CustomerLedgerEntry.order_id == order.id,
            )
        )
        or 0
    )
    voucher_credit = Decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(ChangeVoucherTransaction.amount), 0)).where(
                ChangeVoucherTransaction.bar_id == order.bar_id,
                ChangeVoucherTransaction.target_order_id == order.id,
                ChangeVoucherTransaction.kind == "REDEEM",
            )
        )
        or 0
    )
    sale = Decimal(0) if order.status == "CANCELLED" else Decimal(order.total_amount) - credits
    net = paid - refunded
    financed = max(customer_credit, Decimal(0))
    settled = net + financed + voucher_credit
    if update:
        order.payment_status = "PAID" if settled >= sale else "PARTIAL" if settled else "UNPAID"
    return dict(
        total_paid=paid,
        total_refunded=refunded,
        return_credit=credits,
        customer_credit=financed,
        change_voucher_credit=voucher_credit,
        net_sale=sale,
        net_paid=net,
        net_settled=settled,
        amount_due=max(sale - settled, Decimal(0)),
        refundable_overpayment=max(net - sale, Decimal(0)),
    )
