"""Performance indicators for the cashier workspace.

All figures are scoped to one bar and to the employee's currently open shift.
The cashier gets service-wide sales plus metrics attributable to their own
counter sales, payments and expense entries. Active servers get their own
sales metrics from orders assigned to their staff assignment.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models import Expense, Order, Payment, Refund, StaffAssignment, User
from app.shift_models import EmployeeShift
from app.shift_service import local_time


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _sum(model, column, *conditions) -> Decimal:
    return _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(column), 0)).select_from(model).where(*conditions)
        )
    )


def _count(model, *conditions) -> int:
    return int(
        db.session.scalar(
            select(func.count()).select_from(model).where(*conditions)
        )
        or 0
    )


def build_cashier_performance(
    bar_id: int,
    cashier_user_id: int,
    cashier_assignment_id: int,
    timezone_name: str,
    active_orders,
    balances,
):
    """Return current-shift KPIs for the cashier and active servers.

    ``active_orders`` and ``balances`` are reused from the workspace so unpaid
    totals do not introduce another per-order balance query.
    """
    cashier_shift = db.session.scalar(
        select(EmployeeShift)
        .where(
            EmployeeShift.bar_id == bar_id,
            EmployeeShift.staff_assignment_id == cashier_assignment_id,
            EmployeeShift.role_snapshot == "CASHIER",
            EmployeeShift.status == "OPEN",
        )
        .order_by(EmployeeShift.started_at.desc(), EmployeeShift.id.desc())
    )
    if cashier_shift is None:
        return {
            "shift_started": "—",
            "service_sales": Decimal("0"),
            "counter_sales": Decimal("0"),
            "collected": Decimal("0"),
            "refunds": Decimal("0"),
            "net_collected": Decimal("0"),
            "order_count": 0,
            "average_ticket": Decimal("0"),
            "outstanding": Decimal("0"),
            "expense_total": Decimal("0"),
            "expense_count": 0,
            "servers": [],
        }

    server_shifts = list(
        db.session.scalars(
            select(EmployeeShift)
            .where(
                EmployeeShift.bar_id == bar_id,
                EmployeeShift.role_snapshot == "SERVER",
                EmployeeShift.status == "OPEN",
            )
            .order_by(EmployeeShift.started_at, EmployeeShift.id)
        )
    )

    starts = [cashier_shift.started_at, *(item.started_at for item in server_shifts)]
    earliest_start = min(starts)
    posted_orders = list(
        db.session.scalars(
            select(Order)
            .where(
                Order.bar_id == bar_id,
                Order.posted_at.is_not(None),
                Order.posted_at >= earliest_start,
                Order.status.in_(["CONFIRMED", "SERVED"]),
            )
            .order_by(Order.posted_at, Order.id)
        )
    )

    service_orders = [item for item in posted_orders if item.posted_at >= cashier_shift.started_at]
    service_sales = sum((_decimal(item.total_amount) for item in service_orders), Decimal("0"))
    counter_orders = [
        item
        for item in service_orders
        if item.created_by_id == cashier_user_id and item.assigned_staff_id is None
    ]
    counter_sales = sum((_decimal(item.total_amount) for item in counter_orders), Decimal("0"))
    order_count = len(service_orders)
    average_ticket = service_sales / order_count if order_count else Decimal("0")

    outstanding = sum(
        (
            _decimal(balances[item.id]["amount_due"])
            for item in active_orders
            if item.status in {"CONFIRMED", "SERVED"}
            and item.posted_at is not None
            and item.posted_at >= cashier_shift.started_at
            and item.id in balances
        ),
        Decimal("0"),
    )

    collected = _sum(
        Payment,
        Payment.amount_applied,
        Payment.bar_id == bar_id,
        Payment.recorded_by_id == cashier_user_id,
        Payment.received_at >= cashier_shift.started_at,
    )
    refunds = _sum(
        Refund,
        Refund.amount,
        Refund.bar_id == bar_id,
        Refund.recorded_by_id == cashier_user_id,
        Refund.refunded_at >= cashier_shift.started_at,
    )
    expense_total = _sum(
        Expense,
        Expense.amount,
        Expense.bar_id == bar_id,
        Expense.recorded_by_id == cashier_user_id,
        Expense.entry_kind == "EXPENSE",
        Expense.incurred_at >= cashier_shift.started_at,
    )
    expense_count = _count(
        Expense,
        Expense.bar_id == bar_id,
        Expense.recorded_by_id == cashier_user_id,
        Expense.entry_kind == "EXPENSE",
        Expense.incurred_at >= cashier_shift.started_at,
    )

    server_assignment_ids = {item.staff_assignment_id for item in server_shifts}
    assignments = {
        item.id: item
        for item in db.session.scalars(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.id.in_(server_assignment_ids),
            )
        )
    } if server_assignment_ids else {}
    user_ids = {item.user_id for item in assignments.values()}
    users = {
        item.id: item
        for item in db.session.scalars(select(User).where(User.id.in_(user_ids)))
    } if user_ids else {}

    server_rows = []
    for shift in server_shifts:
        assignment = assignments.get(shift.staff_assignment_id)
        user = users.get(assignment.user_id) if assignment else None
        server_orders = [
            item
            for item in posted_orders
            if item.assigned_staff_id == shift.staff_assignment_id
            and item.posted_at >= shift.started_at
        ]
        sales = sum((_decimal(item.total_amount) for item in server_orders), Decimal("0"))
        count = len(server_orders)
        server_due = sum(
            (
                _decimal(balances[item.id]["amount_due"])
                for item in active_orders
                if item.assigned_staff_id == shift.staff_assignment_id
                and item.status in {"CONFIRMED", "SERVED"}
                and item.posted_at is not None
                and item.posted_at >= shift.started_at
                and item.id in balances
            ),
            Decimal("0"),
        )
        server_rows.append(
            {
                "assignment_id": shift.staff_assignment_id,
                "name": user.display_name if user else f"Serveuse #{shift.staff_assignment_id}",
                "started": local_time(shift.started_at, timezone_name, "%H:%M"),
                "sales": sales,
                "order_count": count,
                "average_ticket": sales / count if count else Decimal("0"),
                "outstanding": server_due,
            }
        )

    return {
        "shift_started": local_time(cashier_shift.started_at, timezone_name, "%H:%M"),
        "service_sales": service_sales,
        "counter_sales": counter_sales,
        "collected": collected,
        "refunds": refunds,
        "net_collected": collected - refunds,
        "order_count": order_count,
        "average_ticket": average_ticket,
        "outstanding": outstanding,
        "expense_total": expense_total,
        "expense_count": expense_count,
        "servers": server_rows,
    }
