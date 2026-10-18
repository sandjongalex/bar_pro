"""Shared financial totals. Refunds reduce receipts, returns reduce sales and customer credit can settle an order."""
from decimal import Decimal
from sqlalchemy import select, func

from app.change_voucher_models import ChangeVoucherTransaction
from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import Payment, Refund, OrderReturn


def total(model, column, *, locking=False, **filters):
    if locking:
        # Locking reads see committed rows after a concurrent order lock even
        # under MySQL REPEATABLE READ (regular SUM reads may use an old snapshot).
        return sum((Decimal(value or 0) for value in db.session.scalars(
            select(column).select_from(model).filter_by(**filters).with_for_update()
        )), Decimal(0))
    return db.session.scalar(select(func.coalesce(func.sum(column), 0)).filter_by(**filters).select_from(model))


def order_balance(order, update=False, *, locking=False):
    keys = dict(bar_id=order.bar_id, order_id=order.id)
    paid = Decimal(total(Payment, Payment.amount_applied, locking=locking, **keys) or 0)
    refunded = Decimal(total(Refund, Refund.amount, locking=locking, **keys) or 0)
    credits = Decimal(total(OrderReturn, OrderReturn.total_amount, status="POSTED", locking=locking, **keys) or 0)
    credit_stmt = select(CustomerLedgerEntry.amount_delta).where(
        CustomerLedgerEntry.bar_id == order.bar_id,
        CustomerLedgerEntry.order_id == order.id,
    )
    customer_credit = (sum((Decimal(value or 0) for value in db.session.scalars(
        credit_stmt.with_for_update())), Decimal(0)) if locking else Decimal(
            db.session.scalar(select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
                CustomerLedgerEntry.bar_id == order.bar_id,
                CustomerLedgerEntry.order_id == order.id,
            )) or 0))
    voucher_stmt = select(ChangeVoucherTransaction.amount).where(
        ChangeVoucherTransaction.bar_id == order.bar_id,
        ChangeVoucherTransaction.target_order_id == order.id,
        ChangeVoucherTransaction.kind == "REDEEM",
    )
    voucher_credit = (sum((Decimal(value or 0) for value in db.session.scalars(
        voucher_stmt.with_for_update())), Decimal(0)) if locking else Decimal(
            db.session.scalar(select(func.coalesce(func.sum(ChangeVoucherTransaction.amount), 0)).where(
                ChangeVoucherTransaction.bar_id == order.bar_id,
                ChangeVoucherTransaction.target_order_id == order.id,
                ChangeVoucherTransaction.kind == "REDEEM",
            )) or 0))
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
