"""Filterable cashier history for real receipts and refunds."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Blueprint, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import func, or_, select

from app.extensions import db
from app.models import Bar, Order, Payment, Refund, StaffAssignment, User
from app.permissions import permissions

bp = Blueprint("cashier_history_web", __name__, url_prefix="/bars/<int:bar_id>/cashier-history")

PAYMENT_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement",
}


def _parse_date(raw: str | None, fallback: date) -> date:
    if not raw:
        return fallback
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return fallback


def _utc_bounds(local_start: date, local_end: date, timezone_name: str):
    tz = ZoneInfo(timezone_name)
    start_local = datetime.combine(local_start, time.min, tzinfo=tz)
    end_local = datetime.combine(local_end, time.max, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _local_display(value, timezone_name: str) -> str:
    if value is None:
        return "—"
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(timezone_name)).strftime("%d/%m/%Y %H:%M")


def _filters(model, date_column, bar_id: int, start_at, end_at, q: str, method: str, server_id: int | None):
    filters = [
        model.bar_id == bar_id,
        date_column >= start_at,
        date_column <= end_at,
    ]
    if method in PAYMENT_LABELS:
        filters.append(model.method == method)
    if server_id:
        filters.append(Order.assigned_staff_id == server_id)
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                model.reference.ilike(pattern),
                Order.reference.ilike(pattern),
                Order.customer_name_snapshot.ilike(pattern),
                Order.table_label_snapshot.ilike(pattern),
            )
        )
    return filters


@bp.get("")
@login_required
def history(bar_id: int):
    permissions.require(current_user, "payments.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    tz = ZoneInfo(bar.timezone)
    today = datetime.now(tz).date()
    default_start = today - timedelta(days=6)
    start_date = _parse_date(request.args.get("date_from"), default_start)
    end_date = _parse_date(request.args.get("date_to"), today)
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    range_limited = False
    if (end_date - start_date).days > 366:
        start_date = end_date - timedelta(days=366)
        range_limited = True

    start_at, end_at = _utc_bounds(start_date, end_date, bar.timezone)
    q = (request.args.get("q") or "").strip()
    method = (request.args.get("method") or "ALL").strip().upper()
    entry_type = (request.args.get("type") or "ALL").strip().upper()
    if entry_type not in {"ALL", "PAYMENT", "REFUND"}:
        entry_type = "ALL"
    if method not in {"ALL", *PAYMENT_LABELS.keys()}:
        method = "ALL"
    server_id = request.args.get("server_id", type=int)

    staff_rows = list(
        db.session.execute(
            select(StaffAssignment.id, User.display_name, StaffAssignment.ended_at)
            .join(User, User.id == StaffAssignment.user_id)
            .where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.role == "SERVER",
            )
            .order_by(User.display_name, StaffAssignment.id)
        ).all()
    )
    server_names = {row.id: row.display_name for row in staff_rows}

    payment_filters = _filters(Payment, Payment.received_at, bar_id, start_at, end_at, q, method, server_id)
    refund_filters = _filters(Refund, Refund.refunded_at, bar_id, start_at, end_at, q, method, server_id)

    payment_count = 0
    payment_total = Decimal("0")
    refund_count = 0
    refund_total = Decimal("0")

    if entry_type in {"ALL", "PAYMENT"}:
        count_value, total_value = db.session.execute(
            select(
                func.count(Payment.id),
                func.coalesce(func.sum(Payment.amount_applied), 0),
            )
            .join(Order, (Order.id == Payment.order_id) & (Order.bar_id == Payment.bar_id))
            .where(*payment_filters)
        ).one()
        payment_count = int(count_value or 0)
        payment_total = Decimal(total_value or 0)

    if entry_type in {"ALL", "REFUND"}:
        count_value, total_value = db.session.execute(
            select(
                func.count(Refund.id),
                func.coalesce(func.sum(Refund.amount), 0),
            )
            .join(Order, (Order.id == Refund.order_id) & (Order.bar_id == Refund.bar_id))
            .where(*refund_filters)
        ).one()
        refund_count = int(count_value or 0)
        refund_total = Decimal(total_value or 0)

    payment_rows = []
    if entry_type in {"ALL", "PAYMENT"}:
        payment_rows = list(
            db.session.execute(
                select(Payment, Order.reference, Order.table_label_snapshot, Order.assigned_staff_id, Order.customer_name_snapshot)
                .join(Order, (Order.id == Payment.order_id) & (Order.bar_id == Payment.bar_id))
                .where(*payment_filters)
                .order_by(Payment.received_at.desc(), Payment.id.desc())
                .limit(400)
            ).all()
        )

    refund_rows = []
    if entry_type in {"ALL", "REFUND"}:
        refund_rows = list(
            db.session.execute(
                select(Refund, Order.reference, Order.table_label_snapshot, Order.assigned_staff_id, Order.customer_name_snapshot)
                .join(Order, (Order.id == Refund.order_id) & (Order.bar_id == Refund.bar_id))
                .where(*refund_filters)
                .order_by(Refund.refunded_at.desc(), Refund.id.desc())
                .limit(400)
            ).all()
        )

    actor_ids = {
        row[0].recorded_by_id
        for row in [*payment_rows, *refund_rows]
        if row[0].recorded_by_id is not None
    }
    actor_names = {
        user.id: user.display_name
        for user in db.session.scalars(select(User).where(User.id.in_(actor_ids)))
    } if actor_ids else {}

    events = []
    for payment, order_reference, table_label, assigned_staff_id, invoice_name in payment_rows:
        events.append(
            {
                "kind": "PAYMENT",
                "reference": payment.reference,
                "order_id": payment.order_id,
                "order_reference": order_reference,
                "order_label": invoice_name or table_label or "COMPTOIR",
                "server": server_names.get(assigned_staff_id, "Sans serveuse"),
                "method": payment.method,
                "method_label": PAYMENT_LABELS.get(payment.method, payment.method),
                "amount": Decimal(payment.amount_applied or 0),
                "currency": payment.currency,
                "recorded_by": actor_names.get(payment.recorded_by_id, "—"),
                "occurred_at": payment.received_at,
                "occurred_label": _local_display(payment.received_at, bar.timezone),
            }
        )
    for refund, order_reference, table_label, assigned_staff_id, invoice_name in refund_rows:
        events.append(
            {
                "kind": "REFUND",
                "reference": refund.reference,
                "order_id": refund.order_id,
                "order_reference": order_reference,
                "order_label": invoice_name or table_label or "COMPTOIR",
                "server": server_names.get(assigned_staff_id, "Sans serveuse"),
                "method": refund.method,
                "method_label": PAYMENT_LABELS.get(refund.method, refund.method),
                "amount": -Decimal(refund.amount or 0),
                "currency": refund.currency,
                "recorded_by": actor_names.get(refund.recorded_by_id, "—"),
                "occurred_at": refund.refunded_at,
                "occurred_label": _local_display(refund.refunded_at, bar.timezone),
            }
        )

    events.sort(
        key=lambda item: (
            item["occurred_at"].replace(tzinfo=None) if getattr(item["occurred_at"], "tzinfo", None) else item["occurred_at"],
            item["reference"],
        ),
        reverse=True,
    )
    events = events[:400]

    return render_template(
        "cashier_history.html",
        bar=bar,
        events=events,
        payment_labels=PAYMENT_LABELS,
        staff_rows=staff_rows,
        filters={
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
            "q": q,
            "method": method,
            "type": entry_type,
            "server_id": server_id,
        },
        summary={
            "payment_count": payment_count,
            "payment_total": payment_total,
            "refund_count": refund_count,
            "refund_total": refund_total,
            "net_total": payment_total - refund_total,
            "operation_count": payment_count + refund_count,
        },
        range_limited=range_limited,
        result_limited=(payment_count + refund_count) > 400,
        today=today.isoformat(),
        seven_days=(today - timedelta(days=6)).isoformat(),
        month_start=today.replace(day=1).isoformat(),
    )