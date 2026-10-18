"""Owner-only, cross-establishment overview for the Bento dashboard.

All bar input is pre-filtered by the dashboard route and rechecked here.
Business amounts are retained as Decimal; only Chart.js series use floats.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, func, or_, select

from app.dashboard_charts import _seven_day_trend
from app.dashboard_service import _day_bounds, _sales_and_margin
from app.extensions import db
from app.models import CashSession, Product, StaffAssignment, StockBalance
from app.permissions import permissions
from app.shift_models import EmployeeShift

ZERO = Decimal("0")
CHART_LABELS = ["J-6", "J-5", "J-4", "J-3", "J-2", "Hier", "Aujourd'hui"]


def owner_bento_overview(actor, reportable_bars):
    """Build a consolidated overview only for bars actually owned by actor.

    Each bar uses its own commercial date/timezone and the existing sales
    settlement rules. Different currencies are never summed together.
    """
    if actor.category != "OWNER":
        raise PermissionError("OWNER_REQUIRED")

    bars = [
        bar for bar in reportable_bars
        if bar.owner_id == actor.id
        and permissions.evaluate(actor, "reports.read", bar.id).allowed
    ]
    currencies = {bar.currency for bar in bars}
    single_currency = len(currencies) <= 1
    currency = next(iter(currencies)) if len(currencies) == 1 else None
    currency_label = "FCFA" if currency == "XAF" else (currency or "—")

    chart_totals = [ZERO] * 7
    turnover = ZERO
    venues = []
    cash_periods = []

    for bar in bars:
        local_date, start_at, end_at = _day_bounds(bar)
        sales, _ = _sales_and_margin(bar.id, start_at, end_at)
        venues.append({
            "id": bar.id,
            "name": bar.name,
            "status": bar.status,
            "currency": "FCFA" if bar.currency == "XAF" else bar.currency,
            "sales_today": sales,
            "local_date": local_date,
        })
        if single_currency:
            turnover += sales
            trend = _seven_day_trend(bar)["rows"]
            for index, row in enumerate(trend):
                chart_totals[index] += Decimal(str(row["sales"]))
        cash_periods.append(
            and_(
                CashSession.bar_id == bar.id,
                CashSession.closed_at >= start_at,
                CashSession.closed_at <= end_at,
            )
        )

    if not bars:
        return {
            "bars": [], "bar_count": 0, "currency": None,
            "currency_label": "FCFA", "mixed_currencies": False,
            "turnover_today": ZERO, "chart": {"labels": [], "values": [], "currency": "FCFA"},
            "critical_stocks": [], "critical_stock_count": 0,
            "servers_on_duty": 0, "cash_discrepancy_amount": ZERO,
            "cash_discrepancy_count": 0,
        }

    bar_ids = [bar.id for bar in bars]
    bar_names = {bar.id: bar.name for bar in bars}

    # Exact alert count plus a bounded preview across all owned bars.
    quantity = func.coalesce(StockBalance.quantity, 0)
    low_stock_query = (
        select(
            Product.id,
            Product.bar_id,
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
            Product.bar_id.in_(bar_ids),
            Product.is_active.is_(True),
            quantity <= Product.stock_alert_threshold,
        )
    )
    low_stock_count = int(db.session.scalar(
        select(func.count()).select_from(low_stock_query.subquery())
    ) or 0)
    stocks = [
        {
            "name": row.name,
            "bar_id": row.bar_id,
            "bar_name": bar_names[row.bar_id],
            "quantity": Decimal(row.quantity or 0),
            "unit": row.base_unit,
            "threshold": Decimal(row.threshold or 0),
            "severity": "critical" if Decimal(row.quantity or 0) <= 0 else "warning",
        }
        for row in db.session.execute(
            low_stock_query.order_by(quantity.asc(), Product.name.asc(), Product.id.asc()).limit(6)
        ).all()
    ]

    servers = int(db.session.scalar(
        select(func.count(func.distinct(EmployeeShift.staff_assignment_id)))
        .join(
            StaffAssignment,
            and_(
                StaffAssignment.id == EmployeeShift.staff_assignment_id,
                StaffAssignment.bar_id == EmployeeShift.bar_id,
            ),
        )
        .where(
            EmployeeShift.bar_id.in_(bar_ids),
            EmployeeShift.status == "OPEN",
            EmployeeShift.role_snapshot == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    ) or 0)

    # Use absolute cash differences; positive and negative discrepancies
    # must not cancel out. Closed sessions only, for each bar's local day.
    difference = CashSession.closing_difference
    cash_amount, cash_count = db.session.execute(
        select(
            func.coalesce(func.sum(func.abs(difference)), 0),
            func.count(CashSession.id),
        ).where(
            CashSession.status == "CLOSED",
            difference.is_not(None),
            difference != 0,
            or_(*cash_periods),
        )
    ).one()

    return {
        "bars": venues,
        "bar_count": len(venues),
        "currency": currency,
        "currency_label": currency_label,
        "mixed_currencies": not single_currency,
        "turnover_today": turnover if single_currency else None,
        "chart": {
            "labels": CHART_LABELS if single_currency else [],
            "values": [float(value) for value in chart_totals] if single_currency else [],
            "currency": currency_label,
        },
        "critical_stocks": stocks,
        "critical_stock_count": low_stock_count,
        "servers_on_duty": servers,
        "cash_discrepancy_amount": Decimal(cash_amount or 0) if single_currency else None,
        "cash_discrepancy_count": int(cash_count),
    }
