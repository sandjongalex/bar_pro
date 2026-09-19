"""Latest posted inventory reconciliation for the owner/admin dashboard."""
from __future__ import annotations

from datetime import timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.extensions import db
from app.inventory_period_models import InventoryLineSnapshot, InventoryPeriodSnapshot
from app.models import Bar, Inventory, InventoryLine, Order, OrderReturn, Product
from app.permissions import permissions

ZERO = Decimal("0")


def _decimal(value) -> Decimal:
    return Decimal(value or 0)


def _in_period(column, start_at, end_at):
    filters = [column <= end_at]
    if start_at is not None:
        filters.append(column > start_at)
    return filters


def _local_date(value, timezone_name):
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(timezone_name)).date()


def _recorded_sales(bar_id: int, start_at, end_at) -> Decimal:
    order_ids = list(
        db.session.scalars(
            select(Order.id).where(
                Order.bar_id == bar_id,
                Order.status.in_(["CONFIRMED", "SERVED"]),
                Order.posted_at.is_not(None),
                *_in_period(Order.posted_at, start_at, end_at),
            )
        )
    )
    if not order_ids:
        return ZERO

    gross = _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(Order.total_amount), 0)).where(
                Order.bar_id == bar_id,
                Order.id.in_(order_ids),
            )
        )
    )
    returns = _decimal(
        db.session.scalar(
            select(func.coalesce(func.sum(OrderReturn.total_amount), 0)).where(
                OrderReturn.bar_id == bar_id,
                OrderReturn.order_id.in_(order_ids),
                OrderReturn.status == "POSTED",
                OrderReturn.posted_at.is_not(None),
                OrderReturn.posted_at <= end_at,
            )
        )
    )
    return max(gross - returns, ZERO)


def dashboard_inventory_control(actor, bar_id: int):
    """Return the most recent posted physical-inventory reconciliation."""
    permissions.require(actor, "reports.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    latest = db.session.execute(
        select(Inventory, InventoryPeriodSnapshot)
        .join(
            InventoryPeriodSnapshot,
            (InventoryPeriodSnapshot.bar_id == Inventory.bar_id)
            & (InventoryPeriodSnapshot.inventory_id == Inventory.id),
        )
        .where(Inventory.bar_id == bar_id, Inventory.status == "POSTED")
        .order_by(InventoryPeriodSnapshot.period_end_at.desc(), Inventory.id.desc())
        .limit(1)
    ).first()
    if not latest:
        return None

    inventory, period = latest
    rows = db.session.execute(
        select(InventoryLine, InventoryLineSnapshot, Product)
        .join(
            InventoryLineSnapshot,
            (InventoryLineSnapshot.bar_id == InventoryLine.bar_id)
            & (InventoryLineSnapshot.inventory_line_id == InventoryLine.id),
        )
        .join(
            Product,
            (Product.id == InventoryLine.product_id)
            & (Product.bar_id == InventoryLine.bar_id),
        )
        .where(
            InventoryLine.bar_id == bar_id,
            InventoryLine.inventory_id == inventory.id,
        )
        .order_by(Product.name, Product.id)
    ).all()

    theoretical_stock = ZERO
    machine_stock = ZERO
    physical_stock = ZERO
    discrepancies = []
    for line, snapshot, product in rows:
        theoretical = _decimal(snapshot.theoretical_quantity)
        machine = _decimal(line.expected_quantity_snapshot)
        physical = _decimal(line.counted_quantity)
        difference = physical - machine
        theoretical_stock += theoretical
        machine_stock += machine
        physical_stock += physical
        if difference:
            discrepancies.append(
                {
                    "product_id": product.id,
                    "name": product.name,
                    "unit": product.base_unit,
                    "theoretical": theoretical,
                    "machine": machine,
                    "physical": physical,
                    "difference": difference,
                    "note": snapshot.note,
                }
            )

    discrepancies.sort(key=lambda item: (-abs(item["difference"]), item["name"].lower()))

    theoretical_sales = _decimal(period.theoretical_sales_amount)
    recorded_sales = _recorded_sales(bar_id, period.period_start_at, period.period_end_at)
    sales_difference = recorded_sales - theoretical_sales
    cash_difference = _decimal(period.cash_difference_amount)

    return {
        "inventory_id": inventory.id,
        "reference": inventory.reference,
        "currency": bar.currency,
        "period_start_date": _local_date(period.period_start_at, bar.timezone),
        "period_end_date": _local_date(period.period_end_at, bar.timezone),
        "posted_at": inventory.posted_at,
        "theoretical_sales": theoretical_sales,
        "recorded_sales": recorded_sales,
        "sales_difference": sales_difference,
        "credit_sales": _decimal(period.credit_sales_amount),
        "expenses": _decimal(period.expenses_amount),
        "expected_cash": _decimal(period.expected_cash_amount),
        "recorded_net": _decimal(period.recorded_net_amount),
        "cash_difference": cash_difference,
        "cash_shortage": max(-cash_difference, ZERO),
        "cash_surplus": max(cash_difference, ZERO),
        "theoretical_stock": theoretical_stock,
        "machine_stock": machine_stock,
        "physical_stock": physical_stock,
        "stock_difference": physical_stock - machine_stock,
        "discrepancy_count": len(discrepancies),
        "discrepancies": discrepancies[:8],
    }
