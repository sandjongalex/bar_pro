"""Cash count and owner-reviewed debt reports attached to physical inventory.

These records are intentionally NOT stock movements, payments, or a second
cash register. An off-invoice debt request never changes the drawer balance.
"""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.extensions import db
from app.models import ID, MONEY, Tenant, datetime_type, tenant_args, utcnow


class InventoryCashReconciliation(Tenant, db.Model):
    __tablename__ = "inventory_cash_reconciliations"
    id = db.Column(ID, primary_key=True)
    inventory_id = db.Column(ID, nullable=False)
    cash_session_id = db.Column(ID, nullable=False)
    counted_amount = db.Column(MONEY, nullable=False)
    expected_amount = db.Column(MONEY, nullable=False)
    opening_amount = db.Column(MONEY, nullable=False)
    difference_amount = db.Column(MONEY, nullable=False)
    reason = db.Column(db.String(500))
    counted_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    counted_at = db.Column(datetime_type(fsp=6), nullable=False, default=utcnow)
    __table_args__ = tenant_args(
        "inventory_cash_reconciliations",
        ForeignKeyConstraint(
            ["bar_id", "inventory_id"],
            ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "cash_session_id"],
            ["cash_sessions.bar_id", "cash_sessions.id"], ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_cash_check"),
        CheckConstraint(
            "counted_amount >= 0 AND opening_amount >= 0",
            name="ck_inventory_cash_check_nonnegative",
        ),
        Index("ix_inventory_cash_check_session", "bar_id", "cash_session_id"),
    )


class InventoryDebtRequest(Tenant, db.Model):
    __tablename__ = "inventory_debt_requests"
    id = db.Column(ID, primary_key=True)
    inventory_id = db.Column(ID, nullable=False)
    customer_id = db.Column(ID, nullable=False)
    order_id = db.Column(ID)
    amount = db.Column(MONEY, nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="PENDING")
    ledger_entry_id = db.Column(ID)
    requested_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    requested_at = db.Column(datetime_type(fsp=6), nullable=False, default=utcnow)
    decided_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at = db.Column(datetime_type(fsp=6))
    decision_reason = db.Column(db.String(500))
    __table_args__ = tenant_args(
        "inventory_debt_requests",
        ForeignKeyConstraint(
            ["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "customer_id"], ["customers.bar_id", "customers.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "order_id"], ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "ledger_entry_id"],
            ["customer_ledger_entries.bar_id", "customer_ledger_entries.id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount > 0 AND status IN ('PENDING','APPROVED','REJECTED')",
            name="ck_inventory_debt_request_values",
        ),
        Index("ix_inventory_debt_requests_inventory", "bar_id", "inventory_id", "status"),
    )
