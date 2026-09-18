"""Reconcile purchase extensions and inventory-period snapshots after f6.

This migration repairs an earlier revision-id collision: two independent files
used revision ``e5f6a7b8c9d0``.  Production databases may therefore have either
set of schema changes while reporting the same Alembic ancestry.  The upgrade
below is intentionally idempotent and creates whichever pieces are missing.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def _types():
    dialect = op.get_bind().dialect.name
    id_type = sa.Integer() if dialect == "sqlite" else sa.BigInteger()
    dt_type = sa.DateTime() if dialect == "sqlite" else mysql.DATETIME(fsp=6)
    return id_type, dt_type


def _columns(table):
    return {item["name"]: item for item in sa.inspect(op.get_bind()).get_columns(table)}


def _checks(table):
    return {item.get("name") for item in sa.inspect(op.get_bind()).get_check_constraints(table)}


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _ensure_purchase_extensions():
    qty = sa.Numeric(20, 6)
    money = sa.Numeric(19, 4)

    supplier_columns = _columns("suppliers")
    if "note" not in supplier_columns:
        with op.batch_alter_table("suppliers") as batch:
            batch.add_column(sa.Column("note", sa.String(500), nullable=True))

    purchase_columns = _columns("purchases")
    if "purchase_date" not in purchase_columns:
        with op.batch_alter_table("purchases") as batch:
            batch.add_column(sa.Column("purchase_date", sa.Date(), nullable=True))
    if "notes" not in purchase_columns:
        with op.batch_alter_table("purchases") as batch:
            batch.add_column(sa.Column("notes", sa.String(500), nullable=True))
    op.execute("UPDATE purchases SET purchase_date = DATE(created_at) WHERE purchase_date IS NULL")
    purchase_columns = _columns("purchases")
    if purchase_columns["purchase_date"].get("nullable", True):
        with op.batch_alter_table("purchases") as batch:
            batch.alter_column("purchase_date", existing_type=sa.Date(), nullable=False)

    line_columns = _columns("purchase_lines")
    with op.batch_alter_table("purchase_lines") as batch:
        if "purchase_unit" not in line_columns:
            batch.add_column(sa.Column("purchase_unit", sa.String(16), nullable=True))
        if "purchase_quantity" not in line_columns:
            batch.add_column(sa.Column("purchase_quantity", qty, nullable=True))
        if "units_per_case_snapshot" not in line_columns:
            batch.add_column(sa.Column("units_per_case_snapshot", sa.Integer(), nullable=True))
        if "purchase_unit_price_snapshot" not in line_columns:
            batch.add_column(sa.Column("purchase_unit_price_snapshot", money, nullable=True))

    op.execute("UPDATE purchase_lines SET purchase_unit = 'BOTTLE' WHERE purchase_unit IS NULL")
    op.execute("UPDATE purchase_lines SET purchase_quantity = quantity WHERE purchase_quantity IS NULL")
    op.execute(
        "UPDATE purchase_lines SET purchase_unit_price_snapshot = unit_cost_snapshot "
        "WHERE purchase_unit_price_snapshot IS NULL"
    )

    line_columns = _columns("purchase_lines")
    with op.batch_alter_table("purchase_lines") as batch:
        if line_columns["purchase_unit"].get("nullable", True):
            batch.alter_column("purchase_unit", existing_type=sa.String(16), nullable=False)
        if line_columns["purchase_quantity"].get("nullable", True):
            batch.alter_column("purchase_quantity", existing_type=qty, nullable=False)
        if line_columns["purchase_unit_price_snapshot"].get("nullable", True):
            batch.alter_column("purchase_unit_price_snapshot", existing_type=money, nullable=False)

    checks = _checks("purchase_lines")
    with op.batch_alter_table("purchase_lines") as batch:
        if "ck_purchase_lines_purchase_unit" not in checks:
            batch.create_check_constraint(
                "ck_purchase_lines_purchase_unit",
                "purchase_unit IN ('CASE','BOTTLE')",
            )
        if "ck_purchase_lines_purchase_quantity" not in checks:
            batch.create_check_constraint(
                "ck_purchase_lines_purchase_quantity",
                "purchase_quantity > 0",
            )
        if "ck_purchase_lines_case_size" not in checks:
            batch.create_check_constraint(
                "ck_purchase_lines_case_size",
                "(purchase_unit = 'BOTTLE' AND units_per_case_snapshot IS NULL) OR "
                "(purchase_unit = 'CASE' AND units_per_case_snapshot IS NOT NULL AND units_per_case_snapshot > 0)",
            )


def _ensure_inventory_tables():
    id_type, dt_type = _types()
    money = sa.Numeric(19, 4)
    qty = sa.Numeric(20, 6)
    tables = _tables()

    if "inventory_period_snapshots" not in tables:
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
            sa.ForeignKeyConstraint(
                ["bar_id", "inventory_id"],
                ["inventories.bar_id", "inventories.id"],
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("bar_id", "id", name="uq_inventory_period_snapshots_bar_id_id"),
            sa.UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_period_inventory"),
            sa.CheckConstraint(
                "period_start_at IS NULL OR period_end_at > period_start_at",
                name="ck_inventory_period_dates",
            ),
        )
        op.create_index(
            "ix_inventory_period_snapshots_bar_created",
            "inventory_period_snapshots",
            ["bar_id", "created_at", "id"],
        )
        op.create_index(
            "ix_inventory_period_snapshots_bar_id",
            "inventory_period_snapshots",
            ["bar_id"],
        )

    tables = _tables()
    if "inventory_line_snapshots" not in tables:
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
            sa.ForeignKeyConstraint(
                ["bar_id", "inventory_line_id"],
                ["inventory_lines.bar_id", "inventory_lines.id"],
                ondelete="RESTRICT",
            ),
            sa.UniqueConstraint("bar_id", "id", name="uq_inventory_line_snapshots_bar_id_id"),
            sa.UniqueConstraint("bar_id", "inventory_line_id", name="uq_inventory_line_snapshot_line"),
            sa.CheckConstraint(
                "opening_quantity >= 0 AND purchase_quantity >= 0 AND theoretical_quantity >= 0 AND sale_price_snapshot >= 0",
                name="ck_inventory_line_snapshot_nonnegative",
            ),
        )
        op.create_index(
            "ix_inventory_line_snapshots_bar_created",
            "inventory_line_snapshots",
            ["bar_id", "created_at", "id"],
        )
        op.create_index(
            "ix_inventory_line_snapshots_bar_id",
            "inventory_line_snapshots",
            ["bar_id"],
        )


def upgrade():
    _ensure_purchase_extensions()
    _ensure_inventory_tables()


def downgrade():
    raise RuntimeError("Refusing destructive downgrade of reconciled operational schema.")
