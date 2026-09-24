"""Read-only, tenant-scoped operational reporting projections."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import BeverageExchange
from sqlalchemy.orm import aliased
from app.models import (
    Bar,
    CashSession,
    Customer,
    Expense,
    Inventory,
    InventoryLine,
    Order,
    OrderLine,
    OrderReturn,
    OrderReturnLine,
    Payment,
    Product,
    ProductCategory,
    Purchase,
    Refund,
    StaffAssignment,
    StockBalance,
    StockMovement,
    SupplierPayment,
    User,
)
from app.permissions import permissions

ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _parse_bound(value, bar: Bar, end: bool = False):
    """Parse a report bound using the establishment timezone.

    Date-only end values are inclusive for the user and converted to the next
    local midnight because all SQL period filters use an exclusive upper bound.
    """
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    tz = ZoneInfo(bar.timezone)
    try:
        if len(raw) == 10:
            local_day = date.fromisoformat(raw)
            local_dt = datetime.combine(local_day, time.min, tzinfo=tz)
            if end:
                local_dt += timedelta(days=1)
            return local_dt.astimezone(timezone.utc).replace(tzinfo=None)

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (ValueError, TypeError):
        raise ValueError("INVALID_DATE") from None


def _period_filters(column, start, end):
    filters = []
    if start is not None:
        filters.append(column >= start)
    if end is not None:
        filters.append(column < end)
    return filters


def _within(value, start, end):
    if value is None:
        return False
    candidate = value.replace(tzinfo=None) if getattr(value, "tzinfo", None) else value
    if start is not None and candidate < start:
        return False
    if end is not None and candidate >= end:
        return False
    return True


def _settled_orders(bar_id: int, start, end):
    """Orders are attributed to the period where their final settlement occurred."""
    base = [
        Order.bar_id == bar_id,
        Order.status.in_(["CONFIRMED", "SERVED"]),
        Order.payment_status == "PAID",
    ]
    if start is None and end is None:
        return list(db.session.scalars(select(Order).where(*base)))

    candidate_ids = set(
        db.session.scalars(
            select(Payment.order_id).where(
                Payment.bar_id == bar_id,
                *_period_filters(Payment.received_at, start, end),
            )
        )
    )
    candidate_ids.update(
        order_id
        for order_id in db.session.scalars(
            select(CustomerLedgerEntry.order_id).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.order_id.is_not(None),
                CustomerLedgerEntry.entry_kind == "CREDIT_SALE",
                *_period_filters(CustomerLedgerEntry.occurred_at, start, end),
            )
        )
        if order_id is not None
    )
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
        events = [event for event in (last_payment.get(order_id), last_credit.get(order_id)) if event is not None]
        if events and _within(max(events), start, end):
            settled_ids.append(order_id)
    if not settled_ids:
        return []
    return list(db.session.scalars(select(Order).where(*base, Order.id.in_(settled_ids))))


def _posted_return_amounts(bar_id: int, order_ids):
    if not order_ids:
        return {}
    return dict(
        db.session.execute(
            select(OrderReturn.order_id, func.coalesce(func.sum(OrderReturn.total_amount), 0))
            .where(
                OrderReturn.bar_id == bar_id,
                OrderReturn.order_id.in_(order_ids),
                OrderReturn.status == "POSTED",
            )
            .group_by(OrderReturn.order_id)
        ).all()
    )


def _posted_return_quantities(bar_id: int, order_ids):
    if not order_ids:
        return {}
    return dict(
        db.session.execute(
            select(OrderReturnLine.order_line_id, func.coalesce(func.sum(OrderReturnLine.quantity), 0))
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


def _credit_by_order(bar_id: int, order_ids):
    if not order_ids:
        return {}
    return dict(
        db.session.execute(
            select(CustomerLedgerEntry.order_id, func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0))
            .where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.order_id.in_(order_ids),
                CustomerLedgerEntry.entry_kind.in_(["CREDIT_SALE", "REVERSAL"]),
            )
            .group_by(CustomerLedgerEntry.order_id)
        ).all()
    )


def summary(actor, bar_id, start=None, end=None):
    permissions.require(actor, "reports.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    start_input = str(start).strip() if start else None
    end_input = str(end).strip() if end else None
    start_at = _parse_bound(start_input, bar, False)
    end_at = _parse_bound(end_input, bar, True)
    if start_at is not None and end_at is not None and end_at <= start_at:
        raise ValueError("INVALID_PERIOD")

    sales = _settled_orders(bar_id, start_at, end_at)
    sale_ids = [order.id for order in sales]
    sales_lines = list(
        db.session.scalars(
            select(OrderLine).where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(sale_ids))
        )
    ) if sale_ids else []
    return_amounts = _posted_return_amounts(bar_id, sale_ids)
    return_quantities = _posted_return_quantities(bar_id, sale_ids)

    gross_revenue = sum((_decimal(order.total_amount) for order in sales), ZERO)
    returned_amount = sum((_decimal(return_amounts.get(order.id, 0)) for order in sales), ZERO)
    revenue = max(gross_revenue - returned_amount, ZERO)

    gross_margin = ZERO
    top = defaultdict(Decimal)
    for line in sales_lines:
        returned_qty = _decimal(return_quantities.get(line.id, 0))
        net_qty = max(_decimal(line.quantity) - returned_qty, ZERO)
        gross_margin += net_qty * (_decimal(line.unit_sale_price_snapshot) - _decimal(line.unit_cost_snapshot))
        if net_qty > 0:
            top[line.product_name_snapshot] += net_qty

    incoming, outgoing = aliased(StockMovement), aliased(StockMovement)
    exchanges = db.session.execute(
        select(BeverageExchange, incoming.unit_cost_snapshot, outgoing.unit_cost_snapshot)
        .join(incoming, incoming.id == BeverageExchange.return_movement_id)
        .join(outgoing, outgoing.id == BeverageExchange.replacement_movement_id)
        .where(BeverageExchange.bar_id == bar_id, BeverageExchange.status == "POSTED",
               *_period_filters(BeverageExchange.decided_at, start_at, end_at))
    ).all()
    exchange_supplements = sum((item.supplement for item, _, _ in exchanges), ZERO)
    revenue += exchange_supplements
    gross_revenue += exchange_supplements
    for item, incoming_cost, outgoing_cost in exchanges:
        gross_margin += (item.supplement + item.returned_quantity * incoming_cost
                         - item.replacement_quantity * outgoing_cost)

    direct_payments = list(
        db.session.scalars(
            select(Payment).where(
                Payment.bar_id == bar_id,
                *_period_filters(Payment.received_at, start_at, end_at),
            )
        )
    )
    refunds = list(
        db.session.scalars(
            select(Refund).where(
                Refund.bar_id == bar_id,
                *_period_filters(Refund.refunded_at, start_at, end_at),
            )
        )
    )
    customer_collections = list(
        db.session.scalars(
            select(CustomerLedgerEntry).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.entry_kind == "PAYMENT",
                *_period_filters(CustomerLedgerEntry.occurred_at, start_at, end_at),
            )
        )
    )

    by_method_gross = defaultdict(Decimal)
    by_method_refunds = defaultdict(Decimal)
    if exchange_supplements:
        by_method_gross["CASH"] += exchange_supplements
    for payment in direct_payments:
        by_method_gross[payment.method] += _decimal(payment.amount_applied)
    for entry in customer_collections:
        by_method_gross[entry.method or "OTHER"] += abs(_decimal(entry.amount_delta))
    for refund in refunds:
        by_method_refunds[refund.method] += _decimal(refund.amount)
    methods = sorted(set(by_method_gross) | set(by_method_refunds))
    by_method = {
        method: str(by_method_gross[method] - by_method_refunds[method])
        for method in methods
    }
    direct_received = sum((_decimal(p.amount_applied) for p in direct_payments), ZERO)
    credit_collections = sum((abs(_decimal(x.amount_delta)) for x in customer_collections), ZERO)
    refunded = sum((_decimal(r.amount) for r in refunds), ZERO)
    total_received = direct_received + credit_collections + exchange_supplements

    expenses = list(
        db.session.scalars(
            select(Expense).where(
                Expense.bar_id == bar_id,
                *_period_filters(Expense.incurred_at, start_at, end_at),
            )
        )
    )
    expense_gross = sum((_decimal(e.amount) for e in expenses if e.entry_kind == "EXPENSE"), ZERO)
    expense_reversed = sum((_decimal(e.amount) for e in expenses if e.entry_kind == "REVERSAL"), ZERO)
    expense_net = max(expense_gross - expense_reversed, ZERO)

    from app.finance_totals import order_balance

    delivered_unsettled = list(
        db.session.scalars(
            select(Order).where(
                Order.bar_id == bar_id,
                Order.status.in_(["CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
        )
    )
    unpaid = []
    for order in delivered_unsettled:
        balance = order_balance(order)
        if balance["amount_due"] > 0:
            unpaid.append(
                {
                    "order_id": order.id,
                    "reference": order.reference,
                    "amount_due": str(balance["amount_due"]),
                }
            )

    customers = {c.id: c for c in db.session.scalars(select(Customer).where(Customer.bar_id == bar_id))}
    customer_debts = defaultdict(Decimal)
    for entry in db.session.scalars(select(CustomerLedgerEntry).where(CustomerLedgerEntry.bar_id == bar_id)):
        customer_debts[entry.customer_id] += _decimal(entry.amount_delta)
    customer_accounts = [
        {
            "customer_id": customer_id,
            "customer": customers[customer_id].display_name if customer_id in customers else str(customer_id),
            "amount_due": str(max(amount, ZERO)),
        }
        for customer_id, amount in customer_debts.items()
        if amount > 0
    ]
    customer_accounts.sort(key=lambda item: (-Decimal(item["amount_due"]), item["customer"].casefold()))
    customer_credit_total = sum((Decimal(item["amount_due"]) for item in customer_accounts), ZERO)

    products = {p.id: p for p in db.session.scalars(select(Product).where(Product.bar_id == bar_id))}
    categories = {
        c.id: c.name
        for c in db.session.scalars(select(ProductCategory).where(ProductCategory.bar_id == bar_id))
    }
    balances = list(db.session.scalars(select(StockBalance).where(StockBalance.bar_id == bar_id)))
    low = []
    stock_quantity = ZERO
    stock_purchase_value = ZERO
    stock_sale_value = ZERO
    for balance in balances:
        product = products.get(balance.product_id)
        if product is None or not product.is_active:
            continue
        quantity = _decimal(balance.quantity)
        stock_quantity += quantity
        stock_purchase_value += quantity * _decimal(product.valuation_unit_cost)
        stock_sale_value += quantity * _decimal(product.sale_price)
        threshold = _decimal(product.stock_alert_threshold)
        if threshold <= 0:
            threshold = _decimal(bar.stock_alert_threshold)
        if quantity <= threshold:
            low.append(
                {
                    "product_id": product.id,
                    "product": product.name,
                    "category": categories.get(product.category_id),
                    "quantity": str(quantity),
                    "threshold": str(threshold),
                    "difference": str(quantity - threshold),
                }
            )
    low.sort(key=lambda item: (Decimal(item["difference"]), item["product"].casefold()))

    losses = sum(
        (
            -_decimal(m.quantity_delta)
            for m in db.session.scalars(
                select(StockMovement).where(
                    StockMovement.bar_id == bar_id,
                    StockMovement.movement_type == "LOSS",
                    *_period_filters(StockMovement.occurred_at, start_at, end_at),
                )
            )
            if m.quantity_delta < 0
        ),
        ZERO,
    )

    cash_differences = [
        _decimal(value)
        for value in db.session.scalars(
            select(CashSession.closing_difference).where(
                CashSession.bar_id == bar_id,
                CashSession.closing_difference.is_not(None),
                *_period_filters(CashSession.closed_at, start_at, end_at),
            )
        )
    ]
    inventory_differences = [
        _decimal(value)
        for value in db.session.scalars(
            select(InventoryLine.difference_quantity)
            .join(Inventory, Inventory.id == InventoryLine.inventory_id)
            .where(
                Inventory.bar_id == bar_id,
                InventoryLine.bar_id == bar_id,
                Inventory.status == "POSTED",
                *_period_filters(Inventory.posted_at, start_at, end_at),
            )
        )
        if value is not None
    ]

    purchases = list(
        db.session.scalars(
            select(Purchase).where(Purchase.bar_id == bar_id, Purchase.status == "POSTED")
        )
    )
    purchase_ids = [p.id for p in purchases]
    supplier_paid = defaultdict(Decimal)
    if purchase_ids:
        for payment in db.session.scalars(
            select(SupplierPayment).where(
                SupplierPayment.bar_id == bar_id,
                SupplierPayment.purchase_id.in_(purchase_ids),
            )
        ):
            supplier_paid[payment.purchase_id] += (
                _decimal(payment.amount) if payment.entry_kind == "PAYMENT" else -_decimal(payment.amount)
            )
    supplier_debt = sum(
        (max(_decimal(p.total_amount) - supplier_paid[p.id], ZERO) for p in purchases),
        ZERO,
    )

    credits = _credit_by_order(bar_id, sale_ids)
    assignment_ids = {order.assigned_staff_id for order in sales if order.assigned_staff_id is not None}
    staff_names = {}
    if assignment_ids:
        for assignment_id, display_name, role in db.session.execute(
            select(StaffAssignment.id, User.display_name, StaffAssignment.role)
            .join(User, User.id == StaffAssignment.user_id)
            .where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.id.in_(assignment_ids),
            )
        ).all():
            staff_names[assignment_id] = display_name if role == "SERVER" else f"{display_name} ({role})"

    server_group = defaultdict(lambda: {"sales": ZERO, "credit": ZERO, "orders": 0})
    for order in sales:
        name = staff_names.get(order.assigned_staff_id, "Non attribuée")
        net_sale = max(_decimal(order.total_amount) - _decimal(return_amounts.get(order.id, 0)), ZERO)
        credit = min(max(_decimal(credits.get(order.id, 0)), ZERO), net_sale)
        server_group[name]["sales"] += net_sale
        server_group[name]["credit"] += credit
        server_group[name]["orders"] += 1
    servers = [
        {
            "name": name,
            "orders": values["orders"],
            "sales": str(values["sales"]),
            "credit_sales": str(values["credit"]),
            "non_credit_sales": str(max(values["sales"] - values["credit"], ZERO)),
        }
        for name, values in sorted(
            server_group.items(),
            key=lambda item: (-item[1]["sales"], -item[1]["orders"], item[0].casefold()),
        )
    ]

    return {
        "currency": bar.currency,
        "period": {
            "start": start_input,
            "end": end_input,
            "start_utc": start_at.isoformat() if start_at else None,
            "end_exclusive_utc": end_at.isoformat() if end_at else None,
            "timezone": bar.timezone,
        },
        "sales": {
            "revenue": str(revenue),
            "gross_revenue": str(gross_revenue),
            "exchange_supplements": str(exchange_supplements),
            "returns": str(returned_amount),
            "orders": len(sales),
            "gross_margin_estimate": str(gross_margin),
            "estimated_profit_after_expenses": str(gross_margin - expense_net),
            "margin_note": "Ventes livrées et soldées uniquement. Marge basée sur les coûts historiques enregistrés, pas une comptabilité légale.",
        },
        "payments": {
            "received": str(total_received),
            "direct_received": str(direct_received),
            "exchange_supplements": str(exchange_supplements),
            "customer_credit_collections": str(credit_collections),
            "refunded": str(refunded),
            "net_received": str(total_received - refunded),
            "by_method": by_method,
        },
        "receivables": {
            "orders_unpaid": unpaid,
            "orders_total_due": str(sum((Decimal(item["amount_due"]) for item in unpaid), ZERO)),
            "customer_credit_total": str(customer_credit_total),
            "customer_accounts": customer_accounts,
        },
        "top_products": [
            {"name": name, "quantity": str(quantity)}
            for name, quantity in sorted(top.items(), key=lambda item: item[1], reverse=True)[:10]
        ],
        "servers": servers,
        "expenses": {
            "total": str(expense_net),
            "gross": str(expense_gross),
            "reversed": str(expense_reversed),
        },
        "stock": {
            "low": low,
            "losses": str(losses),
            "quantity": str(stock_quantity),
            "purchase_value": str(stock_purchase_value),
            "sale_value": str(stock_sale_value),
        },
        "cash": {
            "closing_differences": [str(value) for value in cash_differences],
            "closing_difference_total": str(sum(cash_differences, ZERO)),
        },
        "inventories": {
            "differences": [str(value) for value in inventory_differences],
            "difference_total": str(sum(inventory_differences, ZERO)),
        },
        "supplier_payables": {"total_due": str(supplier_debt)},
    }


def consolidated(actor, start=None, end=None):
    if actor.category != "OWNER":
        raise PermissionError("FORBIDDEN")
    bars = list(db.session.scalars(select(Bar).where(Bar.owner_id == actor.id)))
    reports = [summary(actor, bar.id, start, end) for bar in bars]
    return {
        "bars": [
            {"bar_id": bar.id, "name": bar.name, "report": report}
            for bar, report in zip(bars, reports)
        ],
        "bar_count": len(bars),
    }
