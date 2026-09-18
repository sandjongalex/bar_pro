"""Add customer receivables, returnable cases and persistent notifications."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "d4e5f6a7b8c9"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def _types():
    dialect = op.get_bind().dialect.name
    id_type = sa.Integer() if dialect == "sqlite" else sa.BigInteger()
    dt_type = sa.DateTime() if dialect == "sqlite" else mysql.DATETIME(fsp=6)
    return id_type, dt_type


def upgrade():
    id_type, dt_type = _types()
    money = sa.Numeric(19, 4)

    op.create_table(
        "customer_ledger_entries",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("bar_id", id_type, nullable=False),
        sa.Column("customer_id", id_type, nullable=False),
        sa.Column("order_id", id_type, nullable=True),
        sa.Column("reference", sa.String(64), nullable=False),
        sa.Column("entry_kind", sa.String(16), nullable=False),
        sa.Column("amount_delta", money, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("method", sa.String(16), nullable=True),
        sa.Column("provider_code", sa.String(32), nullable=True),
        sa.Column("provider_transaction_id", sa.String(128), nullable=True),
        sa.Column("cash_session_id", id_type, nullable=True),
        sa.Column("reversal_of_id", id_type, nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("occurred_at", dt_type, nullable=False),
        sa.Column("recorded_by_id", id_type, nullable=False),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "customer_id"], ["customers.bar_id", "customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "order_id"], ["orders.bar_id", "orders.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "cash_session_id"], ["cash_sessions.bar_id", "cash_sessions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "reversal_of_id"], ["customer_ledger_entries.bar_id", "customer_ledger_entries.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_customer_ledger_entries_bar_id_id"),
        sa.UniqueConstraint("bar_id", "reference", name="uq_customer_ledger_reference"),
        sa.UniqueConstraint("bar_id", "reversal_of_id", name="uq_customer_ledger_reversal"),
        sa.CheckConstraint("amount_delta <> 0", name="ck_customer_ledger_amount"),
        sa.CheckConstraint("entry_kind IN ('CREDIT_SALE','PAYMENT','REVERSAL','ADJUSTMENT')", name="ck_customer_ledger_kind"),
    )
    op.create_index("ix_customer_ledger_entries_bar_created", "customer_ledger_entries", ["bar_id", "created_at", "id"])
    op.create_index("ix_customer_ledger_customer_time", "customer_ledger_entries", ["bar_id", "customer_id", "occurred_at", "id"])
    op.create_index("ix_customer_ledger_order", "customer_ledger_entries", ["bar_id", "order_id", "id"])

    op.create_table(
        "customer_case_entries",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("bar_id", id_type, nullable=False),
        sa.Column("customer_id", id_type, nullable=False),
        sa.Column("product_id", id_type, nullable=False),
        sa.Column("order_id", id_type, nullable=True),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("occurred_at", dt_type, nullable=False),
        sa.Column("recorded_by_id", id_type, nullable=False),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "customer_id"], ["customers.bar_id", "customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "order_id"], ["orders.bar_id", "orders.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_customer_case_entries_bar_id_id"),
        sa.CheckConstraint("quantity_delta <> 0", name="ck_customer_case_quantity"),
    )
    op.create_index("ix_customer_case_entries_bar_created", "customer_case_entries", ["bar_id", "created_at", "id"])
    op.create_index("ix_customer_case_customer_product", "customer_case_entries", ["bar_id", "customer_id", "product_id", "id"])

    op.create_table(
        "user_notifications",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("bar_id", id_type, nullable=False),
        sa.Column("user_id", id_type, nullable=False),
        sa.Column("order_id", id_type, nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("read_at", dt_type, nullable=True),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "order_id"], ["orders.bar_id", "orders.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_user_notifications_bar_id", "user_notifications", ["bar_id"])
    op.create_index("ix_user_notifications_user_id", "user_notifications", ["user_id"])
    op.create_index("ix_user_notifications_user_unread", "user_notifications", ["user_id", "read_at", "created_at", "id"])
    op.create_index("ix_user_notifications_bar_order", "user_notifications", ["bar_id", "order_id", "id"])


def downgrade():
    op.drop_table("user_notifications")
    op.drop_table("customer_case_entries")
    op.drop_table("customer_ledger_entries")
