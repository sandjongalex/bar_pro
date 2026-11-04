"""Warehouse V2 supplier receipts: MAIN-only receipt evidence and movement types.

Revision ID: e8f4b6c0d3a2
Revises: d7e3a5b9c2f1

Additive migration: purchases, supplier settlements, legacy stock and accounts
are never rewritten. The existing warehouse stock journal gets two explicit
movement kinds for received/cancelled supplier purchases.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "e8f4b6c0d3a2"
down_revision = "d7e3a5b9c2f1"
branch_labels = None
depends_on = None

ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
qty = sa.Numeric(20, 6)
money = sa.Numeric(19, 4)
dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")

OLD_BUCKET = (
    "(location_id IS NOT NULL AND movement_type IN "
    "('OPENING','TRANSFER_OUT','TRANSFER_IN','ADJUSTMENT','LOSS')) OR "
    "(location_id IS NULL AND movement_type IN ('TRANSIT_IN','TRANSIT_OUT'))"
)
NEW_BUCKET = (
    "(location_id IS NOT NULL AND movement_type IN "
    "('OPENING','TRANSFER_OUT','TRANSFER_IN','ADJUSTMENT','LOSS',"
    "'PURCHASE_IN','PURCHASE_REVERSAL')) OR "
    "(location_id IS NULL AND movement_type IN ('TRANSIT_IN','TRANSIT_OUT'))"
)


def _update_movement_check(expression):
    # SQLite requires recreating a CHECK-bearing table. No historical stock
    # movements are deleted or rewritten; Alembic copies the intact rows.
    with op.batch_alter_table("warehouse_stock_movements") as batch:
        batch.drop_constraint("ck_warehouse_movement_bucket", type_="check")
        batch.create_check_constraint("ck_warehouse_movement_bucket", expression)


def upgrade():
    _update_movement_check(NEW_BUCKET)
    op.create_table(
        "warehouse_purchase_receipt_lines",
        sa.Column("id", ident, primary_key=True),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("purchase_line_id", ident, nullable=False),
        sa.Column("location_id", ident, nullable=False),
        sa.Column("product_id", ident, nullable=False),
        sa.Column("quantity", qty, nullable=False),
        sa.Column("unit_cost_snapshot", money, nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("warehouse_movement_id", ident),
        sa.Column("reversal_movement_id", ident),
        sa.Column("received_by_id", ident, nullable=False),
        sa.Column("received_at", dt, nullable=False),
        sa.Column("reversed_by_id", ident),
        sa.Column("reversed_at", dt),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "purchase_line_id"], ["purchase_lines.bar_id", "purchase_lines.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "location_id"], ["warehouse_locations.bar_id", "warehouse_locations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "product_id"], ["products.bar_id", "products.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "warehouse_movement_id"],
            ["warehouse_stock_movements.bar_id", "warehouse_stock_movements.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "reversal_movement_id"],
            ["warehouse_stock_movements.bar_id", "warehouse_stock_movements.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["received_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_warehouse_purchase_receipt_lines_bar_id_id"),
        sa.UniqueConstraint("bar_id", "purchase_line_id", name="uq_warehouse_purchase_receipt_line"),
        sa.UniqueConstraint("bar_id", "warehouse_movement_id", name="uq_warehouse_purchase_movement"),
        sa.UniqueConstraint("bar_id", "reversal_movement_id", name="uq_warehouse_purchase_reversal"),
        sa.CheckConstraint("quantity > 0 AND unit_cost_snapshot >= 0", name="ck_warehouse_purchase_receipt_values"),
        sa.CheckConstraint(
            "(mode = 'PRE_CUTOVER' AND warehouse_movement_id IS NULL) OR "
            "(mode = 'LIVE' AND warehouse_movement_id IS NOT NULL)",
            name="ck_warehouse_purchase_receipt_mode",
        ),
        sa.CheckConstraint(
            "(reversed_at IS NULL AND reversed_by_id IS NULL AND reversal_movement_id IS NULL) OR "
            "(reversed_at IS NOT NULL AND reversed_by_id IS NOT NULL AND "
            "((mode = 'PRE_CUTOVER' AND reversal_movement_id IS NULL) OR "
            "(mode = 'LIVE' AND reversal_movement_id IS NOT NULL)))",
            name="ck_warehouse_purchase_receipt_reversal",
        ),
    )
    op.create_index("ix_warehouse_purchase_receipt_lines_bar_created", "warehouse_purchase_receipt_lines", ["bar_id", "created_at", "id"])
    op.create_index("ix_warehouse_purchase_main_receipts", "warehouse_purchase_receipt_lines", ["bar_id", "location_id", "received_at", "id"])


def downgrade():
    if not op.get_context().as_sql:
        if op.get_bind().execute(sa.text(
            "SELECT 1 FROM warehouse_purchase_receipt_lines LIMIT 1"
        )).first():
            raise RuntimeError("WAREHOUSE_PURCHASE_RECEIPTS_EXIST: restore from backup instead")
        if op.get_bind().execute(sa.text(
            "SELECT 1 FROM warehouse_stock_movements "
            "WHERE movement_type IN ('PURCHASE_IN','PURCHASE_REVERSAL') LIMIT 1"
        )).first():
            raise RuntimeError("WAREHOUSE_PURCHASE_MOVEMENTS_EXIST")
    op.drop_table("warehouse_purchase_receipt_lines")
    _update_movement_check(OLD_BUCKET)
