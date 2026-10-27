"""Immutable ownership of a V2 inventory by an on-duty cashier shift.

Legacy inventories intentionally have no assignment record. This extension
does not change the legacy inventory schema or its financial calculations.
"""
from sqlalchemy import ForeignKeyConstraint, Index, UniqueConstraint

from app.extensions import db
from app.models import ID, Tenant, datetime_type, tenant_args, utcnow


class InventoryCashierAssignment(Tenant, db.Model):
    __tablename__ = "inventory_cashier_assignments"

    id = db.Column(ID, primary_key=True)
    inventory_id = db.Column(ID, nullable=False)
    cashier_shift_id = db.Column(ID, nullable=False)
    cashier_user_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    cashier_name_snapshot = db.Column(db.String(120), nullable=False)
    assigned_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_at = db.Column(datetime_type(fsp=6), nullable=False, default=utcnow)
    movement_checkpoint_id = db.Column(ID)  # null for inventories created before V2 live reconciliation

    __table_args__ = tenant_args(
        "inventory_cashier_assignments",
        ForeignKeyConstraint(
            ["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "cashier_shift_id"], ["employee_shifts.bar_id", "employee_shifts.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_cashier_one_inventory"),
        Index("ix_inventory_cashier_shift", "bar_id", "cashier_shift_id", "inventory_id"),
        Index("ix_inventory_cashier_user", "bar_id", "cashier_user_id", "inventory_id"),
    )
