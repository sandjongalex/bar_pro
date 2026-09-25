"""Add customer change vouchers and settlement journal.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


OLD_TENDER_CHECK = (
    "amount = amount_applied AND amount_applied > 0 AND change_given >= 0 "
    "AND amount_presented = amount_applied + change_given"
)
NEW_TENDER_CHECK = (
    "amount = amount_applied AND amount_applied > 0 AND change_given >= 0 "
    "AND amount_presented >= amount_applied + change_given"
)


def _types():
    dialect = op.get_bind().dialect.name
    id_type = sa.Integer() if dialect == "sqlite" else sa.BigInteger()
    dt_type = sa.DateTime() if dialect == "sqlite" else mysql.DATETIME(fsp=6)
    return id_type, dt_type


def upgrade():
    id_type, dt_type = _types()
    money = sa.Numeric(19, 4)

    # A historical payment assumed every franc above the invoice amount had
    # already been handed back to the customer.  A change voucher deliberately
    # allows part of that tender to remain physically in the drawer as a
    # customer liability.  The service layer still enforces the exact identity
    # presented = applied + actual_change + voucher_amount atomically.
    with op.batch_alter_table("payments") as batch:
        batch.drop_constraint("ck_payments_tender", type_="check")
        batch.create_check_constraint("ck_payments_tender", NEW_TENDER_CHECK)

    op.create_table(
        "change_vouchers",
        sa.Column("id", id_type, primary_key=True, nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("origin_order_id", id_type, nullable=False),
        sa.Column("origin_payment_id", id_type, nullable=False),
        sa.Column("initial_amount", money, nullable=False),
        sa.Column("balance_amount", money, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("customer_name", sa.String(160), nullable=True),
        sa.Column("customer_phone", sa.String(32), nullable=True),
        sa.Column("issued_at", dt_type, nullable=False),
        sa.Column("settled_at", dt_type, nullable=True),
        sa.Column("issued_by_id", id_type, nullable=False),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.Column("bar_id", id_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["issued_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "origin_order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "origin_payment_id"],
            ["payments.bar_id", "payments.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("bar_id", "id", name="uq_change_vouchers_bar_id_id"),
        sa.UniqueConstraint("bar_id", "code", name="uq_change_vouchers_code"),
        sa.UniqueConstraint("bar_id", "origin_payment_id", name="uq_change_vouchers_origin_payment"),
        sa.CheckConstraint("initial_amount > 0", name="ck_change_vouchers_initial_amount"),
        sa.CheckConstraint(
            "balance_amount >= 0 AND balance_amount <= initial_amount",
            name="ck_change_vouchers_balance",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','PARTIAL','SETTLED','CANCELLED')",
            name="ck_change_vouchers_status",
        ),
    )
    op.create_index("ix_change_vouchers_bar_id", "change_vouchers", ["bar_id"])
    op.create_index(
        "ix_change_vouchers_bar_status",
        "change_vouchers",
        ["bar_id", "status", "issued_at", "id"],
    )
    op.create_index(
        "ix_change_vouchers_bar_created",
        "change_vouchers",
        ["bar_id", "created_at", "id"],
    )

    op.create_table(
        "change_voucher_transactions",
        sa.Column("id", id_type, primary_key=True, nullable=False),
        sa.Column("voucher_id", id_type, nullable=False),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("amount", money, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("target_order_id", id_type, nullable=True),
        sa.Column("cash_session_id", id_type, nullable=True),
        sa.Column("occurred_at", dt_type, nullable=False),
        sa.Column("recorded_by_id", id_type, nullable=False),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.Column("bar_id", id_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "voucher_id"],
            ["change_vouchers.bar_id", "change_vouchers.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "target_order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "cash_session_id"],
            ["cash_sessions.bar_id", "cash_sessions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("bar_id", "id", name="uq_change_voucher_transactions_bar_id_id"),
        sa.UniqueConstraint("bar_id", "reference", name="uq_change_voucher_transactions_reference"),
        sa.CheckConstraint("amount > 0", name="ck_change_voucher_transactions_amount"),
        sa.CheckConstraint(
            "kind IN ('REDEEM','CASH_REFUND')",
            name="ck_change_voucher_transactions_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'REDEEM' AND target_order_id IS NOT NULL AND cash_session_id IS NULL) OR "
            "(kind = 'CASH_REFUND' AND target_order_id IS NULL AND cash_session_id IS NOT NULL)",
            name="ck_change_voucher_transactions_destination",
        ),
    )
    op.create_index("ix_change_voucher_transactions_bar_id", "change_voucher_transactions", ["bar_id"])
    op.create_index(
        "ix_change_voucher_transactions_voucher",
        "change_voucher_transactions",
        ["bar_id", "voucher_id", "occurred_at", "id"],
    )
    op.create_index(
        "ix_change_voucher_transactions_order",
        "change_voucher_transactions",
        ["bar_id", "target_order_id", "kind", "id"],
    )
    op.create_index(
        "ix_change_voucher_transactions_bar_created",
        "change_voucher_transactions",
        ["bar_id", "created_at", "id"],
    )


def downgrade():
    op.drop_index("ix_change_voucher_transactions_bar_created", table_name="change_voucher_transactions")
    op.drop_index("ix_change_voucher_transactions_order", table_name="change_voucher_transactions")
    op.drop_index("ix_change_voucher_transactions_voucher", table_name="change_voucher_transactions")
    op.drop_index("ix_change_voucher_transactions_bar_id", table_name="change_voucher_transactions")
    op.drop_table("change_voucher_transactions")

    op.drop_index("ix_change_vouchers_bar_created", table_name="change_vouchers")
    op.drop_index("ix_change_vouchers_bar_status", table_name="change_vouchers")
    op.drop_index("ix_change_vouchers_bar_id", table_name="change_vouchers")
    op.drop_table("change_vouchers")

    with op.batch_alter_table("payments") as batch:
        batch.drop_constraint("ck_payments_tender", type_="check")
        batch.create_check_constraint("ck_payments_tender", OLD_TENDER_CHECK)
