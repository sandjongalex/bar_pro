"""Live bar service totals independent of midnight.

The cashier workspace uses open employee shifts, not calendar day boundaries.
Expose the same *posted sales* definition to owners without altering accounting,
settlement, inventory or the calendar-based seven-day trend.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select

from app.extensions import db
from app.models import Bar, CashSession, Order
from app.shift_models import EmployeeShift


def _utc_naive(value: datetime) -> datetime:
    """Database timestamps use naive UTC (and can be aware in test fixtures)."""
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def current_service_snapshot(bar: Bar, *, now_utc: datetime | None = None):
    """Return the current bar service or None when no shift/session is open.

    A cashier shift is the authoritative start, matching cashier_performance.
    When an older cashier has no employee shift, fall back to an open CashSession.
    A clock date rollover never closes either record.
    """
    cashier_shift = db.session.scalar(
        select(EmployeeShift)
        .where(
            EmployeeShift.bar_id == bar.id,
            EmployeeShift.status == "OPEN",
            EmployeeShift.role_snapshot == "CASHIER",
        )
        .order_by(EmployeeShift.started_at.asc(), EmployeeShift.id.asc())
        .limit(1)
    )
    source = "cashier_shift"
    if cashier_shift:
        started_at = _utc_naive(cashier_shift.started_at)
    else:
        open_cash = db.session.scalar(
            select(CashSession)
            .where(CashSession.bar_id == bar.id, CashSession.status == "OPEN")
            .order_by(CashSession.opened_at.desc(), CashSession.id.desc())
            .limit(1)
        )
        if open_cash is None:
            return None
        started_at = _utc_naive(open_cash.opened_at)
        source = "cash_session"

    now_utc = _utc_naive(now_utc or datetime.now(timezone.utc))
    if started_at > now_utc:
        # Do not count transactions from a future-dated shift.
        return None
    timezone_name = ZoneInfo(bar.timezone)
    local_start = started_at.replace(tzinfo=timezone.utc).astimezone(timezone_name)
    local_now = now_utc.replace(tzinfo=timezone.utc).astimezone(timezone_name)
    total, count, unpaid = db.session.execute(
        select(
            func.coalesce(func.sum(Order.total_amount), 0),
            func.count(Order.id),
            func.coalesce(
                func.sum(case(
                    (Order.payment_status != "PAID", 1), else_=0
                )), 0
            ),
        ).where(
            Order.bar_id == bar.id,
            Order.posted_at.is_not(None),
            Order.posted_at >= started_at,
            Order.posted_at <= now_utc,
            Order.status.in_(["CONFIRMED", "SERVED"]),
        )
    ).one()
    return {
        "bar_id": bar.id,
        "started_at": started_at,
        "local_start": local_start,
        "service_date": local_start.date(),
        "started_label": local_start.strftime("%d/%m/%Y à %H:%M"),
        "crossed_midnight": local_now.date() != local_start.date(),
        "source": source,
        "sales": Decimal(total or 0),
        "order_count": int(count or 0),
        "unpaid_count": int(unpaid or 0),
    }
