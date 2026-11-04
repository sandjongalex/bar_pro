"""Request -> ship -> receive warehouse V2 vouchers (additive to existing stock).

Revision ID: f2c9a4d1b8e7
Revises: e8f4b6c0d3a2
Legacy warehouse transfers, if any, are retained and upgraded in place.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "f2c9a4d1b8e7"
down_revision = "e8f4b6c0d3a2"
branch_labels = None
depends_on = None

ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
qty = sa.Numeric(20, 6)
money = sa.Numeric(19, 4)
dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")

STATUS_FINAL = "status IN ('REQUESTED','PREPARING','SHIPPED','PARTIALLY_RECEIVED','RECEIVED','DISPUTED','CLOSED')"
STATUS_INTERMEDIATE = "status IN ('IN_TRANSIT','PARTIAL','REQUESTED','PREPARING','SHIPPED','PARTIALLY_RECEIVED','RECEIVED','DISPUTED','CLOSED')"
LINE_VALUES = (
    "quantity_requested > 0 AND quantity_prepared >= 0 AND quantity_dispatched >= 0 "
    "AND quantity_received >= 0 AND quantity_disputed >= 0 AND quantity_lost >= 0 "
    "AND quantity_prepared <= quantity_requested AND quantity_dispatched <= quantity_prepared "
    "AND quantity_received + quantity_disputed + quantity_lost <= quantity_dispatched "
    "AND requested_unit IN ('CASE','BOTTLE') AND units_per_case_snapshot > 0 "
    "AND unit_cost_snapshot >= 0"
)


def upgrade():
    for name in ("requested_by_id", "recipient_user_id", "prepared_by_id",
                 "last_received_by_id", "closed_by_id"):
        op.add_column("warehouse_transfers", sa.Column(name, ident, nullable=True))
    for name in ("requested_at", "prepared_at", "closed_at"):
        op.add_column("warehouse_transfers", sa.Column(name, dt, nullable=True))
    for name in ("quantity_requested", "quantity_prepared", "quantity_lost", "units_per_case_snapshot"):
        op.add_column("warehouse_transfer_lines", sa.Column(name, qty, nullable=True))
    op.add_column("warehouse_transfer_lines", sa.Column("requested_unit", sa.String(8), nullable=True))

    # Do not invent prior actors/destinations. Earlier vouchers had only
    # dispatched_by_id and dispatched_at; these are the closest known sources.
    op.execute(sa.text(
        "UPDATE warehouse_transfers SET requested_by_id = dispatched_by_id, "
        "requested_at = dispatched_at, prepared_by_id = dispatched_by_id, "
        "prepared_at = dispatched_at WHERE dispatched_by_id IS NOT NULL"
    ))
    op.execute(sa.text(
        "UPDATE warehouse_transfer_lines SET quantity_requested = quantity_dispatched, "
        "quantity_prepared = quantity_dispatched, quantity_lost = 0, "
        "requested_unit = 'BOTTLE', units_per_case_snapshot = 1"
    ))
    with op.batch_alter_table("warehouse_transfers") as batch:
        batch.alter_column("dispatched_by_id", existing_type=ident, existing_nullable=False, nullable=True)
        batch.alter_column("dispatched_at", existing_type=dt, existing_nullable=False, nullable=True)
        batch.drop_constraint("ck_warehouse_transfers_status", type_="check")
        batch.create_check_constraint("ck_warehouse_transfers_status", STATUS_INTERMEDIATE)

    op.execute(sa.text(
        "UPDATE warehouse_transfers SET status = CASE "
        "WHEN status = 'IN_TRANSIT' THEN 'SHIPPED' "
        "WHEN status = 'PARTIAL' THEN 'PARTIALLY_RECEIVED' "
        "ELSE status END"
    ))
    with op.batch_alter_table("warehouse_transfers") as batch:
        batch.drop_constraint("ck_warehouse_transfers_status", type_="check")
        batch.create_check_constraint("ck_warehouse_transfers_status", STATUS_FINAL)
    with op.batch_alter_table("warehouse_transfer_lines") as batch:
        for name in ("quantity_requested", "quantity_prepared", "quantity_lost", "units_per_case_snapshot"):
            batch.alter_column(name, existing_type=qty, existing_nullable=True, nullable=False)
        batch.alter_column("requested_unit", existing_type=sa.String(8), existing_nullable=True, nullable=False)
        batch.drop_constraint("ck_warehouse_transfer_line_values", type_="check")
        batch.create_check_constraint("ck_warehouse_transfer_line_values", LINE_VALUES)

    for name in ("requested_by_id", "recipient_user_id", "prepared_by_id",
                 "last_received_by_id", "closed_by_id"):
        op.create_foreign_key(
            f"fk_warehouse_transfers_{name}",
            "warehouse_transfers", "users", [name], ["id"], ondelete="RESTRICT",
        ) if op.get_bind().dialect.name != "sqlite" else None
    # SQLite batch copy is required to add FKs without unsupported ALTER TABLE.
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("warehouse_transfers") as batch:
            for name in ("requested_by_id", "recipient_user_id", "prepared_by_id",
                         "last_received_by_id", "closed_by_id"):
                batch.create_foreign_key(f"fk_warehouse_transfers_{name}", "users", [name], ["id"], ondelete="RESTRICT")

    op.create_table(
        "warehouse_transfer_events",
        sa.Column("id", ident, primary_key=True),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("transfer_id", ident, nullable=False),
        sa.Column("transfer_line_id", ident),
        sa.Column("event_kind", sa.String(24), nullable=False),
        sa.Column("quantity", qty, nullable=False),
        sa.Column("unit_cost_snapshot", money, nullable=False),
        sa.Column("actor_id", ident, nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("occurred_at", dt, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "transfer_id"], ["warehouse_transfers.bar_id", "warehouse_transfers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "transfer_line_id"], ["warehouse_transfer_lines.bar_id", "warehouse_transfer_lines.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_warehouse_transfer_events_bar_id_id"),
        sa.CheckConstraint("quantity >= 0 AND unit_cost_snapshot >= 0", name="ck_warehouse_transfer_events_values"),
        sa.CheckConstraint(
            "event_kind IN ('REQUESTED','PREPARED','SHIPPED','RECEIVED','DISPUTED','DELIVERED','LOSS_APPROVED','CLOSED')",
            name="ck_warehouse_transfer_events_kind",
        ),
    )
    op.create_index("ix_warehouse_transfer_events_bar_id", "warehouse_transfer_events", ["bar_id"])
    op.create_index("ix_warehouse_transfer_events_bar_created", "warehouse_transfer_events", ["bar_id", "created_at", "id"])
    op.create_index("ix_warehouse_transfer_events_transfer", "warehouse_transfer_events", ["bar_id", "transfer_id", "occurred_at"])


def downgrade():
    raise RuntimeError("WAREHOUSE_TRANSFER_AUDIT_IMMUTABLE: restore from a verified backup")
