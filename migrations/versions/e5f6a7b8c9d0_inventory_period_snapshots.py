"""Add auditable inventory-period and line projections."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
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
    qty = sa.Numeric(20, 6)

    op.create_table(
        "inventory_period_snapshots",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("bar_id", id_type, nullable=False),
        sa.Column("inventory_id", id_type, nullable=False),
        sa.Column("period_start_at", dt_type, nullable=True),
        sa.Column("period_end_at", dt_type, nullable=False),
        sa.Column("theoretical_sales_amount", money, nullable=False, server_default="0"),
        sa.Column("expenses_amount", money, nullable=False, server_default="0"),
        sa.Column("credit_sales_amount", money, nullable=False, server_default="0"),
        sa.Column("expected_cash_amount", money, nullable=False, server_default="0"),
        sa.Column("recorded_net_amount", money, nullable=False, server_default="0"),
        sa.Column("cash_difference_amount", money, nullable=False, server_default="0"),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_inventory_period_snapshots_bar_id_id"),
        sa.UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_period_inventory"),
        sa.CheckConstraint("period_start_at IS NULL OR period_end_at > period_start_at", name="ck_inventory_period_dates"),
    )
    op.create_index("ix_inventory_period_snapshots_bar_created", "inventory_period_snapshots", ["bar_id", "created_at", "id"])
    op.create_index("ix_inventory_period_snapshots_bar_id", "inventory_period_snapshots", ["bar_id"])

    op.create_table(
        "inventory_line_snapshots",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("bar_id", id_type, nullable=False),
        sa.Column("inventory_line_id", id_type, nullable=False),
        sa.Column("opening_quantity", qty, nullable=False, server_default="0"),
        sa.Column("purchase_quantity", qty, nullable=False, server_default="0"),
        sa.Column("theoretical_quantity", qty, nullable=False, server_default="0"),
        sa.Column("sale_price_snapshot", money, nullable=False, server_default="0"),
        sa.Column("theoretical_sold_quantity", qty, nullable=False, server_default="0"),
        sa.Column("theoretical_sales_amount", money, nullable=False, server_default="0"),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "inventory_line_id"], ["inventory_lines.bar_id", "inventory_lines.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_inventory_line_snapshots_bar_id_id"),
        sa.UniqueConstraint("bar_id", "inventory_line_id", name="uq_inventory_line_snapshot_line"),
        sa.CheckConstraint("opening_quantity >= 0 AND purchase_quantity >= 0 AND theoretical_quantity >= 0 AND sale_price_snapshot >= 0", name="ck_inventory_line_snapshot_nonnegative"),
    )
    op.create_index("ix_inventory_line_snapshots_bar_created", "inventory_line_snapshots", ["bar_id", "created_at", "id"])
    op.create_index("ix_inventory_line_snapshots_bar_id", "inventory_line_snapshots", ["bar_id"])


def downgrade():
    op.drop_table("inventory_line_snapshots")
    op.drop_table("inventory_period_snapshots")
