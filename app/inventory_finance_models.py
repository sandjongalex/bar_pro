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
    request_nonce = db.Column(db.String(48), nullable=False)
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
        UniqueConstraint("bar_id", "request_nonce", name="uq_inventory_debt_request_nonce"),
        CheckConstraint(
            "amount > 0 AND status IN ('PENDING','APPROVED','REJECTED')",
            name="ck_inventory_debt_request_values",
        ),
        Index("ix_inventory_debt_requests_inventory", "bar_id", "inventory_id", "status"),
    )


class InventoryExpenseLink(Tenant, db.Model):
    """Attach a late-entered expense to the inventory it must reconcile."""

    __tablename__ = "inventory_expense_links"
    id = db.Column(ID, primary_key=True)
    inventory_id = db.Column(ID, nullable=False)
    expense_id = db.Column(ID, nullable=False)
    linked_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = tenant_args(
        "inventory_expense_links",
        ForeignKeyConstraint(
            ["bar_id", "inventory_id"],
            ["inventories.bar_id", "inventories.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "expense_id"],
            ["expenses.bar_id", "expenses.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "bar_id", "expense_id", name="uq_inventory_expense_link_expense"
        ),
        Index(
            "ix_inventory_expense_link_inventory",
            "bar_id", "inventory_id", "expense_id",
        ),
    )


class InventoryDebtPaymentLink(Tenant, db.Model):
    """Attach a cash repayment of an older customer debt to one inventory."""
    __tablename__ = "inventory_debt_payment_links"
    id = db.Column(ID, primary_key=True)
    inventory_id = db.Column(ID, nullable=False)
    ledger_entry_id = db.Column(ID, nullable=False)
    debt_kind = db.Column(db.String(16), nullable=False)
    debt_origin_date = db.Column(db.Date, nullable=False)
    request_nonce = db.Column(db.String(48), nullable=False)
    linked_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    __table_args__ = tenant_args(
        "inventory_debt_payment_links",
        ForeignKeyConstraint(["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "ledger_entry_id"], ["customer_ledger_entries.bar_id", "customer_ledger_entries.id"], ondelete="RESTRICT"),
        UniqueConstraint("bar_id", "ledger_entry_id", name="uq_inventory_debt_payment_ledger_entry"),
        UniqueConstraint("bar_id", "request_nonce", name="uq_inventory_debt_payment_nonce"),
        CheckConstraint("debt_kind IN ('CREDIT','MANUAL')", name="ck_inventory_debt_payment_kind"),
        Index("ix_inventory_debt_payment_inventory", "bar_id", "inventory_id", "ledger_entry_id"),
    )
