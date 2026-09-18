"""Historical inventory-period snapshots.

These tables extend the legacy inventory/count models without rewriting their
stock-integrity contract.  They preserve the figures used to explain an
inventory after it has been posted: previous physical stock, purchases,
theoretical sales and the cash reconciliation.
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from app.extensions import db
from app.models import ID, MONEY, QTY, Tenant, datetime_type, tenant_args


class InventoryPeriodSnapshot(Tenant, db.Model):
    __tablename__ = "inventory_period_snapshots"

    id = db.Column(ID, primary_key=True)
    inventory_id = db.Column(ID, nullable=False)
    period_start_at = db.Column(datetime_type(fsp=6))
    period_end_at = db.Column(datetime_type(fsp=6), nullable=False)
    theoretical_sales_amount = db.Column(MONEY, nullable=False, default=0)
    expenses_amount = db.Column(MONEY, nullable=False, default=0)
    credit_sales_amount = db.Column(MONEY, nullable=False, default=0)
    expected_cash_amount = db.Column(MONEY, nullable=False, default=0)
    recorded_net_amount = db.Column(MONEY, nullable=False, default=0)
    cash_difference_amount = db.Column(MONEY, nullable=False, default=0)

    __table_args__ = tenant_args(
        "inventory_period_snapshots",
        ForeignKeyConstraint(
            ["bar_id", "inventory_id"],
            ["inventories.bar_id", "inventories.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_period_inventory"),
        CheckConstraint(
            "period_start_at IS NULL OR period_end_at > period_start_at",
            name="ck_inventory_period_dates",
        ),
    )


class InventoryLineSnapshot(Tenant, db.Model):
    __tablename__ = "inventory_line_snapshots"

    id = db.Column(ID, primary_key=True)
    inventory_line_id = db.Column(ID, nullable=False)
    opening_quantity = db.Column(QTY, nullable=False, default=0)
    purchase_quantity = db.Column(QTY, nullable=False, default=0)
    theoretical_quantity = db.Column(QTY, nullable=False, default=0)
    sale_price_snapshot = db.Column(MONEY, nullable=False, default=0)
    theoretical_sold_quantity = db.Column(QTY, nullable=False, default=0)
    theoretical_sales_amount = db.Column(MONEY, nullable=False, default=0)
    note = db.Column(db.String(500))

    __table_args__ = tenant_args(
        "inventory_line_snapshots",
        ForeignKeyConstraint(
            ["bar_id", "inventory_line_id"],
            ["inventory_lines.bar_id", "inventory_lines.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "inventory_line_id", name="uq_inventory_line_snapshot_line"),
        CheckConstraint(
            "opening_quantity >= 0 AND purchase_quantity >= 0 AND theoretical_quantity >= 0 AND sale_price_snapshot >= 0",
            name="ck_inventory_line_snapshot_nonnegative",
        ),
    )
