"""Lightweight live order/status feed for cashier and server screens.

The UI polls this endpoint while the tab is visible. This keeps the WSGI deployment
simple (no WebSocket worker required) while letting both roles see changes without a
full page refresh.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from flask import Blueprint, jsonify
from flask_login import current_user, login_required
from sqlalchemy import select

from app.extensions import db, limiter
from app.finance_totals import order_balance
from app.models import Order, Product, StaffAssignment, StockBalance, User
from app.order_line_views import effective_lines_by_order
from app.permissions import permissions

bp = Blueprint("live_orders_web", __name__, url_prefix="/bars/<int:bar_id>/live")


def _assignment(bar_id: int):
    if current_user.category != "EMPLOYEE":
        return None
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == current_user.id,
            StaffAssignment.ended_at.is_(None),
        )
    )


def _decimal_text(value) -> str:
    number = Decimal(value or 0)
    if not number.is_finite():
        return str(number)
    return format(number.normalize(), "f")


def _order_state(order: Order) -> str:
    if order.payment_status == "PAID":
        return "paid"
    if order.status == "DRAFT":
        return "waiting"
    if order.status in {"CONFIRMED", "SERVED"}:
        return "to_pay"
    if order.status == "CANCELLED":
        return "cancelled"
    return "other"


def _stock_payload(bar_id: int) -> dict[str, str]:
    active_ids = set(
        db.session.scalars(
            select(Product.id).where(Product.bar_id == bar_id, Product.is_active.is_(True))
        )
    )
    return {
        str(balance.product_id): _decimal_text(balance.quantity)
        for balance in db.session.scalars(select(StockBalance).where(StockBalance.bar_id == bar_id))
        if balance.product_id in active_ids
    }


def _server_names(orders: list[Order]) -> dict[int, str]:
    assignment_ids = {order.assigned_staff_id for order in orders if order.assigned_staff_id is not None}
    if not assignment_ids:
        return {order.id: "Comptoir" for order in orders}

    assignments = {
        item.id: item
        for item in db.session.scalars(select(StaffAssignment).where(StaffAssignment.id.in_(assignment_ids)))
    }
    user_ids = {item.user_id for item in assignments.values()}
    users = {
        item.id: item
        for item in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}

    result = {}
    for order in orders:
        staff = assignments.get(order.assigned_staff_id)
        user = users.get(staff.user_id) if staff else None
        result[order.id] = user.display_name if user else "Comptoir"
    return result


def _line_payload(lines: list[dict]) -> list[dict]:
    return [
        {
            "line_id": line["id"],
            "product_id": line["product_id"],
            "name": line["product_name_snapshot"],
            "quantity": _decimal_text(line["quantity"]),
        }
        for line in lines
    ]


@bp.get("/orders")
@limiter.exempt
@login_required
def orders(bar_id: int):
    permissions.require(current_user, "orders.create", bar_id)
    assignment = _assignment(bar_id)
    role = assignment.role if assignment else current_user.category

    stock = _stock_payload(bar_id)

    if role == "SERVER":
        recent_orders = list(
            db.session.scalars(
                select(Order)
                .where(
                    Order.bar_id == bar_id,
                    Order.assigned_staff_id == assignment.id,
                    Order.status.in_(["DRAFT", "CONFIRMED", "SERVED", "CANCELLED"]),
                )
                .order_by(Order.id.desc())
                .limit(30)
            )
        )
        lines_by_order = effective_lines_by_order(bar_id, [order.id for order in recent_orders])
        payload_orders = []
        for order in recent_orders:
            balance = order_balance(order)
            payload_orders.append(
                {
                    "id": order.id,
                    "reference": order.reference,
                    "display_name": order.customer_name_snapshot or order.table_label_snapshot or "COMPTOIR",
                    "status": order.status,
                    "payment_status": order.payment_status,
                    "state": _order_state(order),
                    "table": order.table_label_snapshot or "Sans table",
                    "notes": order.notes or "",
                    "total_amount": _decimal_text(balance["net_sale"]),
                    "currency": order.currency,
                    "created_at": str(order.created_at),
                    "lines": _line_payload(lines_by_order.get(order.id, [])),
                }
            )

        stats = {
            "waiting": sum(1 for order in recent_orders if order.status == "DRAFT"),
            "to_pay": sum(
                1
                for order in recent_orders
                if order.status in {"CONFIRMED", "SERVED"} and order.payment_status != "PAID"
            ),
            "paid": sum(1 for order in recent_orders if order.payment_status == "PAID"),
        }
        return jsonify(
            {
                "mode": "SERVER",
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "orders": payload_orders,
                "stats": stats,
                "stock": stock,
            }
        )

    active_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.status.in_(["DRAFT", "CONFIRMED", "SERVED"]),
                Order.payment_status.in_(["UNPAID", "PARTIAL"]),
            )
            .order_by(Order.id.asc())
            .limit(120)
        )
    )
    lines_by_order = effective_lines_by_order(bar_id, [order.id for order in active_orders])
    server_names = _server_names(active_orders)

    payload_orders = []
    total_due = Decimal("0")
    waiting = 0
    payable = 0
    for order in active_orders:
        balance = order_balance(order)
        if order.status == "DRAFT":
            waiting += 1
        else:
            payable += 1
            total_due += Decimal(balance["amount_due"] or 0)
        lines = lines_by_order.get(order.id, [])
        payload_orders.append(
            {
                "id": order.id,
                "reference": order.reference,
                "display_name": order.customer_name_snapshot or order.table_label_snapshot or "COMPTOIR",
                "status": order.status,
                "payment_status": order.payment_status,
                "state": _order_state(order),
                "table": order.table_label_snapshot or "Sans table",
                "server_name": server_names.get(order.id, "Comptoir"),
                "currency": order.currency,
                "amount_due": _decimal_text(balance["amount_due"]),
                "net_sale": _decimal_text(balance["net_sale"]),
                "first_product": lines[0]["product_name_snapshot"] if lines else "",
                "extra_lines": max(len(lines) - 1, 0),
                "lines": _line_payload(lines),
            }
        )

    return jsonify(
        {
            "mode": "CASHIER" if role == "CASHIER" else "ADMIN",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "orders": payload_orders,
            "stats": {
                "waiting": waiting,
                "to_pay": payable,
                "due": _decimal_text(total_due),
            },
            "stock": stock,
        }
    )
