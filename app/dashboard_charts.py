"""Chart projections for the owner/admin dashboard.

The chart layer deliberately reuses the same definition of a "sale today" as the
main dashboard KPIs, so the visual totals never contradict the cards above them.
"""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select

from app.customer_models import CustomerLedgerEntry
from app.dashboard_service import _day_bounds, _settled_orders_today
from app.extensions import db
from app.models import Bar, Order, OrderLine, OrderReturn, OrderReturnLine, StaffAssignment, User
from app.permissions import permissions

ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _number(value) -> float:
    """Convert Decimal business values to JSON-safe numbers for Chart.js."""
    return float(_decimal(value))


def _posted_return_quantities(bar_id: int, order_ids: list[int]):
    if not order_ids:
        return {}
    return dict(
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


def _posted_return_amounts(bar_id: int, order_ids: list[int]):
    if not order_ids:
        return {}
    return dict(
        db.session.execute(
            select(
                OrderReturn.order_id,
                func.coalesce(func.sum(OrderReturn.total_amount), 0),
            )
            .where(
                OrderReturn.bar_id == bar_id,
                OrderReturn.order_id.in_(order_ids),
                OrderReturn.status == "POSTED",
            )
            .group_by(OrderReturn.order_id)
        ).all()
    )


def _credit_by_order(bar_id: int, order_ids: list[int]):
    if not order_ids:
        return {}
    return dict(
        db.session.execute(
            select(
                CustomerLedgerEntry.order_id,
                func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0),
            )
            .where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.order_id.in_(order_ids),
                CustomerLedgerEntry.entry_kind.in_(["CREDIT_SALE", "REVERSAL"]),
            )
            .group_by(CustomerLedgerEntry.order_id)
        ).all()
    )


def _top_products(bar_id: int, order_ids: list[int]):
    if not order_ids:
        return {"labels": [], "quantities": [], "rows": []}

    lines = list(
        db.session.execute(
            select(
                OrderLine.id,
                OrderLine.product_id,
                OrderLine.product_name_snapshot,
                OrderLine.quantity,
            ).where(
                OrderLine.bar_id == bar_id,
                OrderLine.order_id.in_(order_ids),
            )
        ).all()
    )
    returned = _posted_return_quantities(bar_id, order_ids)
    totals = defaultdict(Decimal)
    names = {}
    for line in lines:
        net_quantity = _decimal(line.quantity) - _decimal(returned.get(line.id, 0))
        if net_quantity <= 0:
            continue
        totals[line.product_id] += net_quantity
        names[line.product_id] = line.product_name_snapshot

    ranked = sorted(
        ((product_id, quantity) for product_id, quantity in totals.items()),
        key=lambda item: (-item[1], names.get(item[0], "").lower(), item[0]),
    )[:5]
    rows = [
        {
            "product_id": product_id,
            "name": names.get(product_id, f"Produit {product_id}"),
            "quantity": _number(quantity),
        }
        for product_id, quantity in ranked
    ]
    return {
        "labels": [row["name"] for row in rows],
        "quantities": [row["quantity"] for row in rows],
        "rows": rows,
    }


def _server_performance(bar_id: int, order_ids: list[int]):
    if not order_ids:
        return {
            "labels": [],
            "sales": [],
            "non_credit_sales": [],
            "credit_sales": [],
            "orders": [],
            "rows": [],
        }

    orders = list(
        db.session.execute(
            select(
                Order.id,
                Order.assigned_staff_id,
                Order.total_amount,
            ).where(
                Order.bar_id == bar_id,
                Order.id.in_(order_ids),
            )
        ).all()
    )
    return_amounts = _posted_return_amounts(bar_id, order_ids)
    credits = _credit_by_order(bar_id, order_ids)

    assignment_ids = {row.assigned_staff_id for row in orders if row.assigned_staff_id is not None}
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

    grouped = defaultdict(lambda: {"sales": ZERO, "credit": ZERO, "orders": 0})
    for order in orders:
        name = staff_names.get(order.assigned_staff_id, "Non attribuée")
        net_sale = max(_decimal(order.total_amount) - _decimal(return_amounts.get(order.id, 0)), ZERO)
        credit = max(_decimal(credits.get(order.id, 0)), ZERO)
        credit = min(credit, net_sale)
        grouped[name]["sales"] += net_sale
        grouped[name]["credit"] += credit
        grouped[name]["orders"] += 1

    ranked = sorted(
        grouped.items(),
        key=lambda item: (-item[1]["sales"], -item[1]["orders"], item[0].lower()),
    )
    rows = []
    for name, values in ranked:
        sale = values["sales"]
        credit = values["credit"]
        rows.append(
            {
                "name": name,
                "sales": _number(sale),
                "non_credit_sales": _number(max(sale - credit, ZERO)),
                "credit_sales": _number(credit),
                "orders": int(values["orders"]),
            }
        )

    return {
        "labels": [row["name"] for row in rows],
        "sales": [row["sales"] for row in rows],
        "non_credit_sales": [row["non_credit_sales"] for row in rows],
        "credit_sales": [row["credit_sales"] for row in rows],
        "orders": [row["orders"] for row in rows],
        "rows": rows,
    }


def dashboard_charts(actor, bar_id: int):
    """Return Chart.js-ready data for the selected bar and local business day."""
    permissions.require(actor, "reports.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    local_date, start_at, end_at = _day_bounds(bar)
    settled = _settled_orders_today(bar_id, start_at, end_at)
    order_ids = [row.id for row in settled]

    return {
        "currency": bar.currency,
        "local_date": local_date.isoformat(),
        "top_products": _top_products(bar_id, order_ids),
        "servers": _server_performance(bar_id, order_ids),
    }
