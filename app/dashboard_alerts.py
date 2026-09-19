"""Operational alerts shown on the owner/admin dashboard for one bar."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, case, func, select

from app.customer_models import UserNotification
from app.extensions import db
from app.models import (
    CashSession,
    Inventory,
    Order,
    Product,
    Purchase,
    StockBalance,
    SupplierPayment,
)
from app.permissions import permissions

ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _low_stock(bar_id: int):
    quantity = func.coalesce(StockBalance.quantity, 0)
    base = (
        select(
            Product.id,
            Product.name,
            Product.base_unit,
            quantity.label("quantity"),
            Product.stock_alert_threshold.label("threshold"),
        )
        .outerjoin(
            StockBalance,
            and_(
                StockBalance.bar_id == Product.bar_id,
                StockBalance.product_id == Product.id,
            ),
        )
        .where(
            Product.bar_id == bar_id,
            Product.is_active.is_(True),
            quantity <= Product.stock_alert_threshold,
        )
    )
    count = int(db.session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    rows = db.session.execute(
        base.order_by(quantity.asc(), Product.name.asc(), Product.id.asc()).limit(8)
    ).all()
    return count, [
        {
            "product_id": row.id,
            "name": row.name,
            "unit": row.base_unit,
            "quantity": _decimal(row.quantity),
            "threshold": _decimal(row.threshold),
        }
        for row in rows
    ]


def _pending_orders(bar_id: int):
    criteria = (
        Order.bar_id == bar_id,
        Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
        Order.payment_status != "PAID",
    )
    count = int(db.session.scalar(select(func.count(Order.id)).where(*criteria)) or 0)
    rows = db.session.execute(
        select(
            Order.id,
            Order.reference,
            Order.status,
            Order.payment_status,
            Order.total_amount,
            Order.table_label_snapshot,
            Order.customer_name_snapshot,
            Order.created_at,
        )
        .where(*criteria)
        .order_by(Order.created_at.asc(), Order.id.asc())
        .limit(8)
    ).all()
    return count, [
        {
            "order_id": row.id,
            "reference": row.reference,
            "status": row.status,
            "payment_status": row.payment_status,
            "total": _decimal(row.total_amount),
            "label": row.table_label_snapshot or row.customer_name_snapshot or "Sans table",
            "action": "À livrer" if row.status == "DRAFT" else "À payer",
        }
        for row in rows
    ]


def _supplier_debts(bar_id: int):
    signed_payment = case(
        (SupplierPayment.entry_kind == "PAYMENT", SupplierPayment.amount),
        else_=-SupplierPayment.amount,
    )
    payments = (
        select(
            SupplierPayment.purchase_id.label("purchase_id"),
            func.coalesce(func.sum(signed_payment), 0).label("net_paid"),
        )
        .where(SupplierPayment.bar_id == bar_id)
        .group_by(SupplierPayment.purchase_id)
        .subquery()
    )
    due = Purchase.total_amount - func.coalesce(payments.c.net_paid, 0)
    criteria = (
        Purchase.bar_id == bar_id,
        Purchase.status == "POSTED",
        due > 0,
    )
    count = int(
        db.session.scalar(
            select(func.count(Purchase.id)).outerjoin(
                payments, payments.c.purchase_id == Purchase.id
            ).where(*criteria)
        )
        or 0
    )
    rows = db.session.execute(
        select(
            Purchase.id,
            Purchase.reference,
            Purchase.supplier_name_snapshot,
            due.label("due"),
        )
        .outerjoin(payments, payments.c.purchase_id == Purchase.id)
        .where(*criteria)
        .order_by(due.desc(), Purchase.id.desc())
        .limit(5)
    ).all()
    return count, [
        {
            "purchase_id": row.id,
            "reference": row.reference,
            "supplier": row.supplier_name_snapshot,
            "due": _decimal(row.due),
        }
        for row in rows
    ]


def _draft_inventories(bar_id: int):
    criteria = (Inventory.bar_id == bar_id, Inventory.status == "DRAFT")
    count = int(db.session.scalar(select(func.count(Inventory.id)).where(*criteria)) or 0)
    rows = db.session.execute(
        select(Inventory.id, Inventory.reference, Inventory.counted_at, Inventory.reason)
        .where(*criteria)
        .order_by(Inventory.created_at.desc(), Inventory.id.desc())
        .limit(5)
    ).all()
    return count, [
        {
            "inventory_id": row.id,
            "reference": row.reference,
            "counted_at": row.counted_at,
            "reason": row.reason,
        }
        for row in rows
    ]


def _cash_differences(bar_id: int):
    criteria = (
        CashSession.bar_id == bar_id,
        CashSession.status == "CLOSED",
        CashSession.closing_difference.is_not(None),
        CashSession.closing_difference != 0,
    )
    count = int(db.session.scalar(select(func.count(CashSession.id)).where(*criteria)) or 0)
    rows = db.session.execute(
        select(
            CashSession.id,
            CashSession.reference,
            CashSession.expected_closing_amount,
            CashSession.counted_closing_amount,
            CashSession.closing_difference,
            CashSession.closed_at,
        )
        .where(*criteria)
        .order_by(CashSession.closed_at.desc(), CashSession.id.desc())
        .limit(5)
    ).all()
    return count, [
        {
            "session_id": row.id,
            "reference": row.reference,
            "expected": _decimal(row.expected_closing_amount),
            "counted": _decimal(row.counted_closing_amount),
            "difference": _decimal(row.closing_difference),
            "closed_at": row.closed_at,
        }
        for row in rows
    ]


def dashboard_alerts(actor, bar_id: int):
    """Return bounded alert lists plus exact counters for the selected bar."""
    permissions.require(actor, "reports.read", bar_id)

    low_count, low_items = _low_stock(bar_id)
    order_count, order_items = _pending_orders(bar_id)
    debt_count, debt_items = _supplier_debts(bar_id)
    inventory_count, inventory_items = _draft_inventories(bar_id)
    cash_count, cash_items = _cash_differences(bar_id)
    unread_count = int(
        db.session.scalar(
            select(func.count(UserNotification.id)).where(
                UserNotification.bar_id == bar_id,
                UserNotification.user_id == actor.id,
                UserNotification.read_at.is_(None),
            )
        )
        or 0
    )

    return {
        "counts": {
            "low_stock": low_count,
            "pending_orders": order_count,
            "supplier_debts": debt_count,
            "inventory_drafts": inventory_count,
            "cash_differences": cash_count,
            "unread_notifications": unread_count,
        },
        "low_stock": low_items,
        "pending_orders": order_items,
        "supplier_debts": debt_items,
        "inventory_drafts": inventory_items,
        "cash_differences": cash_items,
    }
