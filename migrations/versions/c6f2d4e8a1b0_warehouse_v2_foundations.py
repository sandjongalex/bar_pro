"""Additive warehouse V2 foundation; no migration of legacy quantities.

Revision ID: c6f2d4e8a1b0
Revises: a9b0c1d2e3f4
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "c6f2d4e8a1b0"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None

ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
qty = sa.Numeric(20, 6)
money = sa.Numeric(19, 4)
dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")


def _create(name, columns, constraints=(), indexes=()):
    op.create_table(
        name,
        sa.Column("id", ident, primary_key=True),
        sa.Column("bar_id", ident, nullable=False),
        *columns,
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name=f"uq_{name}_bar_id_id"),
        *constraints,
    )
    op.create_index(f"ix_{name}_bar_id", name, ["bar_id"])
    op.create_index(f"ix_{name}_bar_created", name, ["bar_id", "created_at", "id"])
    for index_name, cols in indexes:
        op.create_index(index_name, name, cols)


def upgrade():
    _create(
        "warehouse_locations",
        [sa.Column("code", sa.String(16), nullable=False), sa.Column("label", sa.String(80), nullable=False)],
        [
            sa.UniqueConstraint("bar_id", "code", name="uq_warehouse_locations_code"),
            sa.CheckConstraint("code IN ('MAIN','CASHIER')", name="ck_warehouse_locations_code"),
        ],
    )
    _create(
        "warehouse_stock_balances",
        [
            sa.Column("location_id", ident, nullable=False),
            sa.Column("product_id", ident, nullable=False),
            sa.Column("quantity", qty, nullable=False),
            sa.Column("version", ident, nullable=False),
        ],
        [
            sa.ForeignKeyConstraint(["bar_id", "location_id"], ["warehouse_locations.bar_id", "warehouse_locations.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("bar_id", "location_id", "product_id", name="uq_warehouse_stock_location_product"),
            sa.CheckConstraint("quantity >= 0 AND version >= 0", name="ck_warehouse_stock_nonnegative"),
        ],
        [("ix_warehouse_stock_product", ["bar_id", "product_id", "location_id"])],
    )
    _create(
        "warehouse_transit_balances",
        [
            sa.Column("product_id", ident, nullable=False),
            sa.Column("quantity", qty, nullable=False),
            sa.Column("version", ident, nullable=False),
        ],
        [
            sa.ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("bar_id", "product_id", name="uq_warehouse_transit_product"),
            sa.CheckConstraint("quantity >= 0 AND version >= 0", name="ck_warehouse_transit_nonnegative"),
        ],
    )
    _create(
        "warehouse_allocations",
        [
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("validated_by_id", ident, nullable=False),
            sa.Column("validated_at", dt, nullable=False),
            sa.Column("legacy_movement_checkpoint_id", ident),
        ],
        [
            sa.ForeignKeyConstraint(["validated_by_id"], ["users.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("bar_id", name="uq_warehouse_allocations_one_bar"),
            sa.CheckConstraint("status IN ('STAGED','LIVE')", name="ck_warehouse_allocations_status"),
        ],
    )
    _create(
        "warehouse_allocation_lines",
        [
            sa.Column("allocation_id", ident, nullable=False),
            sa.Column("product_id", ident, nullable=False),
            sa.Column("legacy_quantity_snapshot", qty, nullable=False),
            sa.Column("legacy_balance_version", ident, nullable=False),
            sa.Column("main_quantity", qty, nullable=False),
            sa.Column("cashier_quantity", qty, nullable=False),
            sa.Column("unit_cost_snapshot", money, nullable=False),
        ],
        [
            sa.ForeignKeyConstraint(["bar_id", "allocation_id"], ["warehouse_allocations.bar_id", "warehouse_allocations.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("bar_id", "allocation_id", "product_id", name="uq_warehouse_allocation_product"),
            sa.CheckConstraint(
                "legacy_quantity_snapshot >= 0 AND legacy_balance_version >= 0 AND main_quantity >= 0 AND cashier_quantity >= 0 AND unit_cost_snapshot >= 0 AND main_quantity + cashier_quantity = legacy_quantity_snapshot",
                name="ck_warehouse_allocation_conservation",
            ),
        ],
    )
    _create(
        "warehouse_transfers",
        [
            sa.Column("reference", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False),
            sa.Column("dispatched_by_id", ident, nullable=False),
            sa.Column("dispatched_at", dt, nullable=False),
        ],
        [
            sa.ForeignKeyConstraint(["dispatched_by_id"], ["users.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("bar_id", "reference", name="uq_warehouse_transfers_reference"),
            sa.CheckConstraint("status IN ('IN_TRANSIT','PARTIAL','DISPUTED','RECEIVED')", name="ck_warehouse_transfers_status"),
        ],
    )
    _create(
        "warehouse_transfer_lines",
        [
            sa.Column("transfer_id", ident, nullable=False),
            sa.Column("product_id", ident, nullable=False),
            sa.Column("quantity_dispatched", qty, nullable=False),
            sa.Column("quantity_received", qty, nullable=False),
            sa.Column("quantity_disputed", qty, nullable=False),
            sa.Column("unit_cost_snapshot", money, nullable=False),
        ],
        [
            sa.ForeignKeyConstraint(["bar_id", "transfer_id"], ["warehouse_transfers.bar_id", "warehouse_transfers.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("bar_id", "transfer_id", "product_id", name="uq_warehouse_transfer_product"),
            sa.CheckConstraint(
                "quantity_dispatched > 0 AND quantity_received >= 0 AND quantity_disputed >= 0 AND quantity_received + quantity_disputed <= quantity_dispatched AND unit_cost_snapshot >= 0",
                name="ck_warehouse_transfer_line_values",
            ),
        ],
    )
    _create(
        "warehouse_transfer_receipts",
        [
            sa.Column("transfer_id", ident, nullable=False),
            sa.Column("idempotency_key", sa.String(80), nullable=False),
            sa.Column("payload_hash", sa.String(64), nullable=False),
            sa.Column("quantities", sa.JSON(), nullable=False),
            sa.Column("received_by_id", ident, nullable=False),
            sa.Column("received_at", dt, nullable=False),
        ],
        [
            sa.ForeignKeyConstraint(["bar_id", "transfer_id"], ["warehouse_transfers.bar_id", "warehouse_transfers.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["received_by_id"], ["users.id"], ondelete="RESTRICT"),
            sa.UniqueConstraint("bar_id", "idempotency_key", name="uq_warehouse_receipt_idempotency"),
        ],
    )
    _create(
        "warehouse_stock_movements",
        [
            sa.Column("location_id", ident),
            sa.Column("product_id", ident, nullable=False),
            sa.Column("movement_type", sa.String(20), nullable=False),
            sa.Column("quantity_delta", qty, nullable=False),
            sa.Column("unit_cost_snapshot", money, nullable=False),
            sa.Column("allocation_line_id", ident),
            sa.Column("transfer_line_id", ident),
            sa.Column("receipt_id", ident),
            sa.Column("occurred_at", dt, nullable=False),
            sa.Column("recorded_by_id", ident, nullable=False),
            sa.Column("reason", sa.String(500), nullable=False),
        ],
        [
            sa.ForeignKeyConstraint(["bar_id", "location_id"], ["warehouse_locations.bar_id", "warehouse_locations.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["bar_id", "allocation_line_id"], ["warehouse_allocation_lines.bar_id", "warehouse_allocation_lines.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["bar_id", "transfer_line_id"], ["warehouse_transfer_lines.bar_id", "warehouse_transfer_lines.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["bar_id", "receipt_id"], ["warehouse_transfer_receipts.bar_id", "warehouse_transfer_receipts.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["recorded_by_id"], ["users.id"], ondelete="RESTRICT"),
            sa.CheckConstraint(
                "(location_id IS NOT NULL AND movement_type IN ('OPENING','TRANSFER_OUT','TRANSFER_IN','ADJUSTMENT','LOSS')) OR (location_id IS NULL AND movement_type IN ('TRANSIT_IN','TRANSIT_OUT'))",
                name="ck_warehouse_movement_bucket",
            ),
            sa.CheckConstraint("quantity_delta <> 0 AND unit_cost_snapshot >= 0", name="ck_warehouse_movement_values"),
        ],
        [("ix_warehouse_movements_product_time", ["bar_id", "product_id", "occurred_at", "id"])],
    )

    # Existing bars: only physical location metadata is backfilled.
    # No legacy quantity, purchase, cash entry or stock movement is touched.
    for code, label in (("MAIN", "Magasin principal"), ("CASHIER", "Magasin de la caisse")):
        op.execute(sa.text(
            "INSERT INTO warehouse_locations (bar_id, code, label, created_at, updated_at) "
            f"SELECT b.id, '{code}', '{label}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP "
            "FROM bars AS b WHERE NOT EXISTS ("
            "SELECT 1 FROM warehouse_locations AS w WHERE w.bar_id = b.id "
            f"AND w.code = '{code}')"
        ))


def downgrade():
    tables = [
        "warehouse_stock_movements", "warehouse_transfer_receipts", "warehouse_transfer_lines",
        "warehouse_transfers", "warehouse_allocation_lines", "warehouse_allocations",
        "warehouse_transit_balances", "warehouse_stock_balances", "warehouse_locations",
    ]
    # Downgrade is safe only before a live warehouse operation has ever been recorded.
    if not op.get_context().as_sql:
        for table in tables[:-1]:
            if op.get_bind().execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first():
                raise RuntimeError("WAREHOUSE_V2_DATA_EXISTS: restore from verified backup")
    for table in tables:
        op.drop_table(table)
