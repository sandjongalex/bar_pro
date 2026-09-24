"""Shared live monitor for delivered orders that still need payment."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select

from app.extensions import db, limiter
from app.finance_totals import order_balance
from app.models import Bar, Order, StaffAssignment, User
from app.order_line_views import effective_lines_by_order
from app.permissions import permissions

bp = Blueprint("unpaid_orders_web", __name__, url_prefix="/bars/<int:bar_id>/unpaid-orders")


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


def _iso(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _personnel_context(bar_id: int, orders: list[Order]):
    """Resolve personnel referenced by the queue plus currently assigned staff."""
    assigned_staff_ids = {
        order.assigned_staff_id
        for order in orders
        if order.assigned_staff_id is not None
    }
    counter_creator_ids = {
        order.created_by_id
        for order in orders
        if order.assigned_staff_id is None
    }

    stmt = select(StaffAssignment).where(
        StaffAssignment.bar_id == bar_id,
        StaffAssignment.role.in_(["CASHIER", "SERVER"]),
    )
    clauses = [StaffAssignment.ended_at.is_(None)]
    if assigned_staff_ids:
        clauses.append(StaffAssignment.id.in_(assigned_staff_ids))
    if counter_creator_ids:
        clauses.append(StaffAssignment.user_id.in_(counter_creator_ids))

    staff_rows = list(db.session.scalars(stmt.where(or_(*clauses))))
    assignments = {item.id: item for item in staff_rows}
    user_ids = {item.user_id for item in staff_rows}
    users = {
        item.id: item
        for item in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}

    personnel_by_user = {}
    for item in staff_rows:
        user = users.get(item.user_id)
        if not user:
            continue
        previous = personnel_by_user.get(user.id)
        if previous is None or (previous["ended"] and item.ended_at is None):
            personnel_by_user[user.id] = {
                "user_id": user.id,
                "name": user.display_name,
                "role": item.role,
                "ended": item.ended_at is not None,
            }

    personnel = sorted(
        (
            {
                "user_id": item["user_id"],
                "name": item["name"],
                "role": item["role"],
            }
            for item in personnel_by_user.values()
        ),
        key=lambda item: (
            0 if item["role"] == "CASHIER" else 1,
            item["name"].casefold(),
            item["user_id"],
        ),
    )
    return assignments, users, personnel_by_user, personnel


def _payload(bar_id: int, staff_filter: str | None = None):
    permissions.require(current_user, "orders.create", bar_id)
    assignment = _assignment(bar_id)
    role = assignment.role if assignment else current_user.category

    query = select(Order).where(
        Order.bar_id == bar_id,
        Order.status.in_(["CONFIRMED", "SERVED"]),
        Order.payment_status.in_(["UNPAID", "PARTIAL"]),
    )
    if role == "SERVER":
        query = query.where(Order.assigned_staff_id == assignment.id)

    all_orders = list(
        db.session.scalars(
            query.order_by(Order.posted_at.asc(), Order.id.asc()).limit(200)
        )
    )

    assignments, users, personnel_by_user, personnel = _personnel_context(bar_id, all_orders)

    selected_filter = "all"
    selected_user_id = None
    if role != "SERVER":
        candidate = (staff_filter or "all").strip().lower()
        if candidate.startswith("person-"):
            try:
                candidate_user_id = int(candidate.split("-", 1)[1])
            except (TypeError, ValueError):
                candidate_user_id = None
            if candidate_user_id in personnel_by_user:
                selected_filter = candidate
                selected_user_id = candidate_user_id

    def order_person_id(order: Order):
        if order.assigned_staff_id is not None:
            staff = assignments.get(order.assigned_staff_id)
            return staff.user_id if staff else None
        return order.created_by_id if order.created_by_id in personnel_by_user else None

    orders = (
        [order for order in all_orders if order_person_id(order) == selected_user_id]
        if selected_user_id is not None
        else all_orders
    )

    lines_by_order = effective_lines_by_order(bar_id, [order.id for order in orders])
    can_collect = role != "SERVER" and permissions.evaluate(current_user, "payments.read", bar_id).allowed

    rows = []
    total_due = Decimal("0")
    partial = 0
    for order in orders:
        balance = order_balance(order)
        due = Decimal(balance["amount_due"] or 0)
        total_due += due
        if order.payment_status == "PARTIAL":
            partial += 1

        if order.assigned_staff_id is not None:
            staff = assignments.get(order.assigned_staff_id)
            user = users.get(staff.user_id) if staff else None
            person_name = user.display_name if user else "Serveuse"
        else:
            person = personnel_by_user.get(order.created_by_id)
            person_name = f"{person['name']} · Comptoir" if person else "Comptoir"

        if role == "CASHIER":
            action_url = url_for("cashier_workspace_web.workspace", bar_id=bar_id, order_id=order.id) + "#paymentPanel"
        elif can_collect:
            action_url = url_for("checkout_web.checkout", bar_id=bar_id, order_id=order.id)
        else:
            action_url = None

        rows.append(
            {
                "id": order.id,
                "reference": order.reference,
                "table": order.table_label_snapshot or "Sans table",
                "server_name": person_name,
                "payment_status": order.payment_status,
                "currency": order.currency,
                "amount_due": _decimal_text(due),
                "net_sale": _decimal_text(balance["net_sale"]),
                "posted_at": _iso(order.posted_at or order.created_at),
                "notes": order.notes or "",
                "action_url": action_url,
                "lines": [
                    {
                        "name": line["product_name_snapshot"],
                        "quantity": _decimal_text(line["quantity"]),
                        "total_amount": _decimal_text(line["total_amount"]),
                    }
                    for line in lines_by_order.get(order.id, [])
                ],
            }
        )

    return {
        "mode": role,
        "orders": rows,
        "personnel": personnel if role != "SERVER" else [],
        "staff_filter": selected_filter,
        "stats": {
            "count": len(rows),
            "partial": partial,
            "due": _decimal_text(total_due),
        },
    }


@bp.get("")
@login_required
def monitor(bar_id: int):
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    payload = _payload(bar_id, request.args.get("staff"))
    return render_template(
        "unpaid_orders.html",
        bar=bar,
        payload=payload,
        is_server=payload["mode"] == "SERVER",
        is_cashier=payload["mode"] == "CASHIER",
    )


@bp.get("/data")
@limiter.exempt
@login_required
def data(bar_id: int):
    if not db.session.get(Bar, bar_id):
        raise LookupError("NOT_FOUND")
    return jsonify(_payload(bar_id, request.args.get("staff")))
