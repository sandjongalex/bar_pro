"""Operational dashboard projections for one bar.

The dashboard intentionally separates sales from real receipts: a customer-credit
sale contributes to turnover once the order is settled, but it does not increase
cash received until the customer actually pays later.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select

from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import (
    Bar,
    Expense,
    Order,
    OrderLine,
    OrderReturn,
    OrderReturnLine,
    Payment,
    Product,
    Purchase,
    Refund,
    StockBalance,
    SupplierPayment,
)
from app.permissions import permissions

ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _day_bounds(bar: Bar):
    """Return the current commercial calendar day as naive UTC DB bounds."""
    local_tz = ZoneInfo(bar.timezone)
    now_local = datetime.now(local_tz)
    start_local = datetime.combine(now_local.date(), time.min, tzinfo=local_tz)
    end_local = datetime.combine(now_local.date(), time.max, tzinfo=local_tz)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return now_local.date(), start_utc, end_utc


def _between(column, start_at, end_at):
    return column >= start_at, column <= end_at


def _naive_datetime(value):
    if value is None:
        return None
    return value.replace(tzinfo=None) if getattr(value, "tzinfo", None) is not None else value


def _settled_orders_today(bar_id: int, start_at, end_at):
    """Return orders whose final settlement event falls inside the given bounds.

    A multi-part payment can span several days.  Looking only for "any payment in
    the day" would therefore count the same order on more than one historical day
    once it eventually becomes PAID.  We first collect orders touched in the
    requested interval, then keep only those whose latest settlement event
    (payment or CREDIT_SALE) is actually inside that interval.
    """
    payment_ids = set(
        db.session.scalars(
            select(Payment.order_id).where(
                Payment.bar_id == bar_id,
                *_between(Payment.received_at, start_at, end_at),
            )
        )
    )
    credit_ids = set(
        db.session.scalars(
            select(CustomerLedgerEntry.order_id).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.entry_kind == "CREDIT_SALE",
                CustomerLedgerEntry.order_id.is_not(None),
                *_between(CustomerLedgerEntry.occurred_at, start_at, end_at),
            )
        )
    )
    candidate_ids = payment_ids | credit_ids
    if not candidate_ids:
        return []

    last_payment = dict(
        db.session.execute(
            select(Payment.order_id, func.max(Payment.received_at))
            .where(Payment.bar_id == bar_id, Payment.order_id.in_(candidate_ids))
            .group_by(Payment.order_id)
        ).all()
    )
    last_credit = dict(
        db.session.execute(
            select(CustomerLedgerEntry.order_id, func.max(CustomerLedgerEntry.occurred_at))
            .where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.order_id.in_(candidate_ids),
                CustomerLedgerEntry.entry_kind == "CREDIT_SALE",
            )
            .group_by(CustomerLedgerEntry.order_id)
        ).all()
    )

    settled_ids = []
    for order_id in candidate_ids:
        events = [
            _naive_datetime(last_payment.get(order_id)),
            _naive_datetime(last_credit.get(order_id)),
        ]
        events = [event for event in events if event is not None]
        if not events:
            continue
        settled_at = max(events)
        if start_at <= settled_at <= end_at:
            settled_ids.append(order_id)

    if not settled_ids:
        return []

    return list(
        db.session.execute(
            select(Order.id, Order.total_amount).where(
                Order.bar_id == bar_id,
                Order.id.in_(settled_ids),
                Order.status.in_(["CONFIRMED", "SERVED"]),
                Order.payment_status == "PAID",
            )
        ).all()
    )


def _sales_and_margin(bar_id: int, start_at, end_at):
    settled = _settled_orders_today(bar_id, start_at, end_at)
    if not settled:
        return ZERO, ZERO

    order_ids = [row.id for row in settled]
    gross_sales = sum((_decimal(row.total_amount) for row in settled), ZERO)
    return_total = _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(OrderReturn.total_amount), 0)).where(
                OrderReturn.bar_id == bar_id,
                OrderReturn.order_id.in_(order_ids),
                OrderReturn.status == "POSTED",
            )
        )
    )

    lines = list(
        db.session.execute(
            select(
                OrderLine.id,
                OrderLine.quantity,
                OrderLine.unit_sale_price_snapshot,
                OrderLine.unit_cost_snapshot,
            ).where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(order_ids))
        ).all()
    )
    returned_by_line = dict(
        db.session.execute(
            select(
                OrderReturnLine.order_line_id,
                func.coalesce(func.sum(OrderReturnLine.quantity), 0),
            )
            .join(
                OrderReturn,
                (OrderReturn.id == OrderReturnLine.order_return_id)
                & (OrderReturn.bar_id == OrderReturnLine.bar_id),
            )
            .where(
                OrderReturnLine.bar_id == bar_id,
                OrderReturnLine.order_id.in_(order_ids),
                OrderReturn.status == "POSTED",
            )
            .group_by(OrderReturnLine.order_line_id)
        ).all()
    )

    margin = ZERO
    for line in lines:
        net_quantity = _decimal(line.quantity) - _decimal(returned_by_line.get(line.id, 0))
        unit_margin = _decimal(line.unit_sale_price_snapshot) - _decimal(line.unit_cost_snapshot)
        margin += net_quantity * unit_margin

    return max(gross_sales - return_total, ZERO), margin


def _today_receipts(bar_id: int, start_at, end_at):
    order_payments = _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(Payment.amount_applied), 0)).where(
                Payment.bar_id == bar_id,
                *_between(Payment.received_at, start_at, end_at),
            )
        )
    )
    refunds = _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(Refund.amount), 0)).where(
                Refund.bar_id == bar_id,
                *_between(Refund.refunded_at, start_at, end_at),
            )
        )
    )
    customer_debt_payments = -_decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.entry_kind == "PAYMENT",
                *_between(CustomerLedgerEntry.occurred_at, start_at, end_at),
            )
        )
    )
    return order_payments + customer_debt_payments - refunds


def _today_expenses(bar_id: int, start_at, end_at):
    signed = case(
        (Expense.entry_kind == "EXPENSE", Expense.amount),
        else_=-Expense.amount,
    )
    return max(
        _decimal(
            db.session.scalar(
                select(func.coalesce(func.sum(signed), 0)).where(
                    Expense.bar_id == bar_id,
                    *_between(Expense.incurred_at, start_at, end_at),
                )
            )
        ),
        ZERO,
    )


def _supplier_debt(bar_id: int):
    purchases = _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(Purchase.total_amount), 0)).where(
                Purchase.bar_id == bar_id,
                Purchase.status == "POSTED",
            )
        )
    )
    signed_payment = case(
        (SupplierPayment.entry_kind == "PAYMENT", SupplierPayment.amount),
        else_=-SupplierPayment.amount,
    )
    paid = _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(signed_payment), 0)).where(
                SupplierPayment.bar_id == bar_id,
            )
        )
    )
    return max(purchases - paid, ZERO)


def _stock_snapshot(bar_id: int):
    row = db.session.execute(
        select(
            func.coalesce(func.sum(StockBalance.quantity), 0),
            func.coalesce(func.sum(StockBalance.quantity * Product.valuation_unit_cost), 0),
            func.coalesce(func.sum(StockBalance.quantity * Product.sale_price), 0),
        )
        .join(
            Product,
            (Product.id == StockBalance.product_id) & (Product.bar_id == StockBalance.bar_id),
        )
        .where(StockBalance.bar_id == bar_id, Product.is_active.is_(True))
    ).one()
    return _decimal(row[0]), _decimal(row[1]), _decimal(row[2])


def dashboard_summary(actor, bar_id: int):
    """Build the ten owner/admin dashboard indicators for one accessible bar."""
    permissions.require(actor, "reports.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    local_date, start_at, end_at = _day_bounds(bar)
    sales, gross_margin = _sales_and_margin(bar_id, start_at, end_at)
    receipts = _today_receipts(bar_id, start_at, end_at)
    expenses = _today_expenses(bar_id, start_at, end_at)
    supplier_debt = _supplier_debt(bar_id)
    stock_quantity, stock_purchase_value, stock_sale_value = _stock_snapshot(bar_id)

    product_count = int(
        db.session.scalar(
            select(func.count(Product.id)).where(Product.bar_id == bar_id, Product.is_active.is_(True))
        )
        or 0
    )
    pending_orders = int(
        db.session.scalar(
            select(func.count(Order.id)).where(
                Order.bar_id == bar_id,
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
                Order.payment_status != "PAID",
            )
        )
        or 0
    )

    return {
        "bar": bar,
        "local_date": local_date,
        "currency": bar.currency,
        "values": {
            "today_sales": sales,
            "today_receipts": receipts,
            "product_count": product_count,
            "pending_orders": pending_orders,
            "today_expenses": expenses,
            "supplier_debt": supplier_debt,
            "estimated_profit": gross_margin - expenses,
            "stock_quantity": stock_quantity,
            "stock_purchase_value": stock_purchase_value,
            "stock_sale_value": stock_sale_value,
        },
        "cards": [
            ("Ventes du jour", sales, "money", "green", "Commandes soldées aujourd'hui"),
            ("Total encaissé", receipts, "money", "blue", "Paiements réels nets reçus"),
            ("Produits actifs", product_count, "number", "slate", "Catalogue disponible"),
            ("Commandes en attente", pending_orders, "number", "orange", "À livrer ou à encaisser"),
            ("Dépenses du jour", expenses, "money", "red", "Sorties validées nettes"),
            ("Dettes fournisseurs", supplier_debt, "money", "amber", "Reste à régler"),
            ("Bénéfice estimé", gross_margin - expenses, "money", "purple", "Marge brute moins dépenses"),
            ("Stock total", stock_quantity, "quantity", "teal", "Unités actuellement disponibles"),
            ("Valeur stock achat", stock_purchase_value, "money", "indigo", "Valorisation au coût unitaire"),
            ("Valeur stock vente", stock_sale_value, "money", "emerald", "Potentiel au prix de vente"),
        ],
    }
