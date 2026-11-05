"""Record previous customer-debt cash collections in the active inventory.

Revision ID: f9c0d1e2f3a4
Revises: f8b9c0d1e2f3
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "f9c0d1e2f3a4"
down_revision = "f8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")
    op.add_column(
        "inventory_period_snapshots",
        sa.Column(
            "debt_repayments_amount",
            sa.Numeric(19, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.create_table(
        "inventory_debt_payment_links",
        sa.Column("id", ident, primary_key=True, nullable=False),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("inventory_id", ident, nullable=False),
        sa.Column("ledger_entry_id", ident, nullable=False),
        sa.Column("debt_kind", sa.String(16), nullable=False),
        sa.Column("debt_origin_date", sa.Date(), nullable=False),
        sa.Column("request_nonce", sa.String(48), nullable=False),
        sa.Column("linked_by_id", ident, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "inventory_id"],
            ["inventories.bar_id", "inventories.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "ledger_entry_id"],
            ["customer_ledger_entries.bar_id", "customer_ledger_entries.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "bar_id", "id", name="uq_inventory_debt_payment_links_bar_id_id"
        ),
        sa.UniqueConstraint(
            "bar_id", "ledger_entry_id",
            name="uq_inventory_debt_payment_ledger_entry",
        ),
        sa.UniqueConstraint(
            "bar_id", "request_nonce",
            name="uq_inventory_debt_payment_nonce",
        ),
        sa.CheckConstraint(
            "debt_kind IN ('CREDIT','MANUAL')",
            name="ck_inventory_debt_payment_kind",
        ),
    )
    op.create_index(
        "ix_inventory_debt_payment_links_bar_id",
        "inventory_debt_payment_links",
        ["bar_id"],
    )
    op.create_index(
        "ix_inventory_debt_payment_links_bar_created",
        "inventory_debt_payment_links",
        ["bar_id", "created_at", "id"],
    )
    op.create_index(
        "ix_inventory_debt_payment_inventory",
        "inventory_debt_payment_links",
        ["bar_id", "inventory_id", "ledger_entry_id"],
    )


def downgrade():
    op.drop_index(
        "ix_inventory_debt_payment_inventory",
        table_name="inventory_debt_payment_links",
    )
    op.drop_index(
        "ix_inventory_debt_payment_links_bar_created",
        table_name="inventory_debt_payment_links",
    )
    op.drop_index(
        "ix_inventory_debt_payment_links_bar_id",
        table_name="inventory_debt_payment_links",
    )
    op.drop_table("inventory_debt_payment_links")
    op.drop_column("inventory_period_snapshots", "debt_repayments_amount")
