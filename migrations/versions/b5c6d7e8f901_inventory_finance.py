"""Inventory-linked cash count and pending customer debt review.

Revision ID: b5c6d7e8f901
Revises: f4a5b6c7d8e9
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "b5c6d7e8f901"
down_revision = "f4a5b6c7d8e9"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    money = sa.Numeric(19, 4)
    dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")
    op.create_table(
        "inventory_cash_reconciliations",
        sa.Column("id", ident, primary_key=True),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("inventory_id", ident, nullable=False),
        sa.Column("cash_session_id", ident, nullable=False),
        sa.Column("counted_amount", money, nullable=False),
        sa.Column("expected_amount", money, nullable=False),
        sa.Column("opening_amount", money, nullable=False),
        sa.Column("difference_amount", money, nullable=False),
        sa.Column("reason", sa.String(500)),
        sa.Column("counted_by_id", ident, nullable=False),
        sa.Column("counted_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "cash_session_id"], ["cash_sessions.bar_id", "cash_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["counted_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_inventory_cash_reconciliations_bar_id_id"),
        sa.UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_cash_check"),
        sa.CheckConstraint("counted_amount >= 0 AND opening_amount >= 0", name="ck_inventory_cash_check_nonnegative"),
    )
    op.create_index("ix_inventory_cash_check_session", "inventory_cash_reconciliations", ["bar_id", "cash_session_id"])
    op.create_index("ix_inventory_cash_reconciliations_bar_id", "inventory_cash_reconciliations", ["bar_id"])
    op.create_table(
        "inventory_debt_requests",
        sa.Column("id", ident, primary_key=True),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("inventory_id", ident, nullable=False),
        sa.Column("customer_id", ident, nullable=False),
        sa.Column("order_id", ident),
        sa.Column("amount", money, nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("ledger_entry_id", ident),
        sa.Column("requested_by_id", ident, nullable=False),
        sa.Column("requested_at", dt, nullable=False),
        sa.Column("decided_by_id", ident),
        sa.Column("decided_at", dt),
        sa.Column("decision_reason", sa.String(500)),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "customer_id"], ["customers.bar_id", "customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "order_id"], ["orders.bar_id", "orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "ledger_entry_id"], ["customer_ledger_entries.bar_id", "customer_ledger_entries.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_inventory_debt_requests_bar_id_id"),
        sa.CheckConstraint("amount > 0 AND status IN ('PENDING','APPROVED','REJECTED')", name="ck_inventory_debt_request_values"),
    )
    op.create_index("ix_inventory_debt_requests_inventory", "inventory_debt_requests", ["bar_id", "inventory_id", "status"])
    op.create_index("ix_inventory_debt_requests_bar_id", "inventory_debt_requests", ["bar_id"])


def downgrade():
    bind = op.get_bind()
    for name in ("inventory_debt_requests", "inventory_cash_reconciliations"):
        if bind.execute(sa.text("SELECT 1 FROM " + name + " LIMIT 1")).first():
            raise RuntimeError("Inventory finance data exists: destructive downgrade refused")
    op.drop_index("ix_inventory_debt_requests_inventory", table_name="inventory_debt_requests")
    op.drop_index("ix_inventory_debt_requests_bar_id", table_name="inventory_debt_requests")
    op.drop_table("inventory_debt_requests")
    op.drop_index("ix_inventory_cash_check_session", table_name="inventory_cash_reconciliations")
    op.drop_index("ix_inventory_cash_reconciliations_bar_id", table_name="inventory_cash_reconciliations")
    op.drop_table("inventory_cash_reconciliations")
