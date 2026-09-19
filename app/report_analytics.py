"""Comparison periods and chart-ready projections for operational reports."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import Bar, Expense, Order, OrderReturn, Payment, Refund
from app.permissions import permissions
from app.report_services import summary

ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _dates(start: str, end: str):
    try:
        start_day = date.fromisoformat(str(start))
        end_day = date.fromisoformat(str(end))
    except (TypeError, ValueError):
        raise ValueError("INVALID_DATE") from None
    if end_day < start_day:
        raise ValueError("INVALID_PERIOD")
    return start_day, end_day


def _utc_bounds(bar: Bar, start_day: date, end_day: date):
    tz = ZoneInfo(bar.timezone)
    start_local = datetime.combine(start_day, time.min, tzinfo=tz)
    end_local = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _local_day(value, tz: ZoneInfo):
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(tz).date()


def _previous_period(start_day: date, end_day: date):
    length = (end_day - start_day).days + 1
    previous_end = start_day - timedelta(days=1)
    previous_start = previous_end - timedelta(days=length - 1)
    return previous_start, previous_end


def report_presets(bar: Bar):
    today = datetime.now(ZoneInfo(bar.timezone)).date()
    yesterday = today - timedelta(days=1)
    week_start = today - timedelta(days=today.weekday())
    previous_week_end = week_start - timedelta(days=1)
    previous_week_start = previous_week_end - timedelta(days=6)
    month_start = today.replace(day=1)
    previous_month_end = month_start - timedelta(days=1)
    previous_month_start = previous_month_end.replace(day=1)

    items = [
        ("Aujourd'hui", today, today),
        ("Hier", yesterday, yesterday),
        ("7 derniers jours", today - timedelta(days=6), today),
        ("Cette semaine", week_start, today),
        ("Semaine précédente", previous_week_start, previous_week_end),
        ("Ce mois", month_start, today),
        ("Mois précédent", previous_month_start, previous_month_end),
    ]
    return [
        {"label": label, "start": start.isoformat(), "end": end.isoformat()}
        for label, start, end in items
    ]


def _metric(current, previous):
    current_value = _decimal(current)
    previous_value = _decimal(previous)
    delta = current_value - previous_value
    percent = None if previous_value == 0 else (delta / abs(previous_value)) * Decimal("100")
    return {
        "current": str(current_value),
        "previous": str(previous_value),
        "delta": str(delta),
        "percent": None if percent is None else float(percent),
        "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
    }


def comparison_bundle(actor, bar_id: int, start: str, end: str, current_report=None):
    permissions.require(actor, "reports.read", bar_id)
    start_day, end_day = _dates(start, end)
    previous_start, previous_end = _previous_period(start_day, end_day)
    current = current_report or summary(actor, bar_id, start_day.isoformat(), end_day.isoformat())
    previous = summary(actor, bar_id, previous_start.isoformat(), previous_end.isoformat())

    return {
        "current": {"start": start_day.isoformat(), "end": end_day.isoformat()},
        "previous": {"start": previous_start.isoformat(), "end": previous_end.isoformat()},
        "metrics": {
            "sales": _metric(current["sales"]["revenue"], previous["sales"]["revenue"]),
            "receipts": _metric(current["payments"]["net_received"], previous["payments"]["net_received"]),
            "expenses": _metric(current["expenses"]["total"], previous["expenses"]["total"]),
            "profit": _metric(
                current["sales"]["estimated_profit_after_expenses"],
                previous["sales"]["estimated_profit_after_expenses"],
            ),
            "orders": _metric(current["sales"]["orders"], previous["sales"]["orders"]),
        },
    }


def _settlement_times(bar_id: int, start_at, end_at):
    candidate_ids = set(
        db.session.scalars(
            select(Payment.order_id).where(
                Payment.bar_id == bar_id,
                Payment.received_at >= start_at,
                Payment.received_at < end_at,
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
                CustomerLedgerEntry.occurred_at >= start_at,
                CustomerLedgerEntry.occurred_at < end_at,
            )
        )
        if order_id is not None
    )
    if not candidate_ids:
        return {}

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

    result = {}
    for order_id in candidate_ids:
        events = [event for event in (last_payment.get(order_id), last_credit.get(order_id)) if event is not None]
        if not events:
            continue
        settled_at = max(events)
        normalized = settled_at.replace(tzinfo=None) if settled_at.tzinfo else settled_at
        if start_at <= normalized < end_at:
            result[order_id] = settled_at
    return result


def _return_amounts(bar_id: int, order_ids):
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


def _daily_trend(bar: Bar, start_day: date, end_day: date):
    day_count = (end_day - start_day).days + 1
    if day_count > 366:
        return {
            "available": False,
            "reason": "Période trop longue pour le graphique journalier. Sélectionnez au maximum 366 jours.",
            "labels": [],
            "sales": [],
            "receipts": [],
            "expenses": [],
        }

    tz = ZoneInfo(bar.timezone)
    start_at, end_at = _utc_bounds(bar, start_day, end_day)
    buckets = {
        start_day + timedelta(days=offset): {"sales": ZERO, "receipts": ZERO, "expenses": ZERO}
        for offset in range(day_count)
    }

    settlements = _settlement_times(bar.id, start_at, end_at)
    order_ids = list(settlements)
    if order_ids:
        orders = list(
            db.session.execute(
                select(Order.id, Order.total_amount).where(
                    Order.bar_id == bar.id,
                    Order.id.in_(order_ids),
                    Order.status.in_(["CONFIRMED", "SERVED"]),
                    Order.payment_status == "PAID",
                )
            ).all()
        )
        returns = _return_amounts(bar.id, [row.id for row in orders])
        for order in orders:
            local_day = _local_day(settlements[order.id], tz)
            if local_day in buckets:
                buckets[local_day]["sales"] += max(
                    _decimal(order.total_amount) - _decimal(returns.get(order.id, 0)), ZERO
                )

    for received_at, amount in db.session.execute(
        select(Payment.received_at, Payment.amount_applied).where(
            Payment.bar_id == bar.id,
            Payment.received_at >= start_at,
            Payment.received_at < end_at,
        )
    ).all():
        local_day = _local_day(received_at, tz)
        if local_day in buckets:
            buckets[local_day]["receipts"] += _decimal(amount)

    for refunded_at, amount in db.session.execute(
        select(Refund.refunded_at, Refund.amount).where(
            Refund.bar_id == bar.id,
            Refund.refunded_at >= start_at,
            Refund.refunded_at < end_at,
        )
    ).all():
        local_day = _local_day(refunded_at, tz)
        if local_day in buckets:
            buckets[local_day]["receipts"] -= _decimal(amount)

    for occurred_at, amount_delta in db.session.execute(
        select(CustomerLedgerEntry.occurred_at, CustomerLedgerEntry.amount_delta).where(
            CustomerLedgerEntry.bar_id == bar.id,
            CustomerLedgerEntry.entry_kind == "PAYMENT",
            CustomerLedgerEntry.occurred_at >= start_at,
            CustomerLedgerEntry.occurred_at < end_at,
        )
    ).all():
        local_day = _local_day(occurred_at, tz)
        if local_day in buckets:
            buckets[local_day]["receipts"] += abs(_decimal(amount_delta))

    for incurred_at, amount, entry_kind in db.session.execute(
        select(Expense.incurred_at, Expense.amount, Expense.entry_kind).where(
            Expense.bar_id == bar.id,
            Expense.incurred_at >= start_at,
            Expense.incurred_at < end_at,
        )
    ).all():
        local_day = _local_day(incurred_at, tz)
        if local_day in buckets:
            buckets[local_day]["expenses"] += _decimal(amount) if entry_kind == "EXPENSE" else -_decimal(amount)

    rows = []
    for local_day, values in buckets.items():
        rows.append(
            {
                "date": local_day.isoformat(),
                "label": local_day.strftime("%d/%m"),
                "sales": float(values["sales"]),
                "receipts": float(values["receipts"]),
                "expenses": float(max(values["expenses"], ZERO)),
            }
        )
    return {
        "available": True,
        "reason": None,
        "labels": [row["label"] for row in rows],
        "sales": [row["sales"] for row in rows],
        "receipts": [row["receipts"] for row in rows],
        "expenses": [row["expenses"] for row in rows],
    }


def report_chart_data(actor, bar_id: int, start: str, end: str, report=None):
    permissions.require(actor, "reports.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    start_day, end_day = _dates(start, end)
    report = report or summary(actor, bar_id, start, end)

    methods = report["payments"]["by_method"]
    top_products = report["top_products"][:10]
    servers = report["servers"][:10]
    return {
        "currency": report["currency"],
        "trend": _daily_trend(bar, start_day, end_day),
        "payment_methods": {
            "labels": list(methods.keys()),
            "values": [float(_decimal(value)) for value in methods.values()],
        },
        "top_products": {
            "labels": [item["name"] for item in top_products],
            "values": [float(_decimal(item["quantity"])) for item in top_products],
        },
        "servers": {
            "labels": [item["name"] for item in servers],
            "sales": [float(_decimal(item["sales"])) for item in servers],
            "credit": [float(_decimal(item["credit_sales"])) for item in servers],
        },
    }
