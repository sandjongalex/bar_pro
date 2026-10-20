"""Add optional cashier-to-shift attribution for V2 inventories.

Legacy inventory documents remain unchanged; only new V2 inventories get
assignment rows. No payment, sales or stock records are rewritten.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "c1d2e3f4a5b6"
down_revision = "ba5a5a1f0001"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")
    op.create_table(
        "inventory_cashier_assignments",
        sa.Column("id", ident, primary_key=True, nullable=False),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("inventory_id", ident, nullable=False),
        sa.Column("cashier_shift_id", ident, nullable=False),
        sa.Column("cashier_user_id", ident, nullable=False),
        sa.Column("cashier_name_snapshot", sa.String(120), nullable=False),
        sa.Column("assigned_by_id", ident, nullable=False),
        sa.Column("assigned_at", dt, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "inventory_id"],
                                ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["bar_id", "cashier_shift_id"],
                                ["employee_shifts.bar_id", "employee_shifts.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["cashier_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_inventory_cashier_assignments_bar_id_id"),
        sa.UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_cashier_one_inventory"),
    )
    op.create_index("ix_inventory_cashier_assignments_bar_id",
                    "inventory_cashier_assignments", ["bar_id"])
    op.create_index("ix_inventory_cashier_assignments_bar_created",
                    "inventory_cashier_assignments", ["bar_id", "created_at", "id"])
    op.create_index("ix_inventory_cashier_shift", "inventory_cashier_assignments",
                    ["bar_id", "cashier_shift_id", "inventory_id"])
    op.create_index("ix_inventory_cashier_user", "inventory_cashier_assignments",
                    ["bar_id", "cashier_user_id", "inventory_id"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM inventory_cashier_assignments LIMIT 1")).first():
        raise RuntimeError("Cashier inventory attribution exists; destructive downgrade refused")
    op.drop_index("ix_inventory_cashier_user", table_name="inventory_cashier_assignments")
    op.drop_index("ix_inventory_cashier_shift", table_name="inventory_cashier_assignments")
    op.drop_index("ix_inventory_cashier_assignments_bar_created", table_name="inventory_cashier_assignments")
    op.drop_index("ix_inventory_cashier_assignments_bar_id", table_name="inventory_cashier_assignments")
    op.drop_table("inventory_cashier_assignments")
