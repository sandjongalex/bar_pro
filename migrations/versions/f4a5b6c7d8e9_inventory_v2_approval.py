"""Add per-bar inventory approval policy and per-inventory review transitions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "f4a5b6c7d8e9"
down_revision = "e3f4a5b6c7d8"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")
    money = sa.Numeric(19, 4)
    qty = sa.Numeric(20, 6)
    op.create_table(
        "inventory_approval_policies",
        sa.Column("bar_id", ident, nullable=False, primary_key=True),
        sa.Column("max_unit_difference", qty, nullable=False),
        sa.Column("max_product_value", money, nullable=False),
        sa.Column("max_total_value", money, nullable=False),
        sa.Column("changed_by_id", ident, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "max_unit_difference >= 0 AND max_product_value >= 0 AND max_total_value >= 0",
            name="ck_inventory_policy_nonnegative",
        ),
    )
    op.create_table(
        "inventory_approvals",
        sa.Column("id", ident, primary_key=True, nullable=False),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("inventory_id", ident, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("submitted_by_id", ident, nullable=False),
        sa.Column("submitted_at", dt, nullable=False),
        sa.Column("decided_by_id", ident),
        sa.Column("decided_at", dt),
        sa.Column("decision_reason", sa.String(500)),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "inventory_id"], ["inventories.bar_id", "inventories.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["submitted_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decided_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_inventory_approvals_bar_id_id"),
        sa.UniqueConstraint("bar_id", "inventory_id", name="uq_inventory_approval_per_inventory"),
        sa.CheckConstraint(
            "status IN ('SUBMITTED','RECOUNT','POSTED')", name="ck_inventory_approval_status",
        ),
    )
    op.create_index("ix_inventory_approvals_bar_id", "inventory_approvals", ["bar_id"])
    op.create_index("ix_inventory_approvals_bar_created", "inventory_approvals", ["bar_id", "created_at", "id"])
    op.create_index("ix_inventory_approvals_status", "inventory_approvals", ["bar_id", "status", "submitted_at"])


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text("SELECT 1 FROM inventory_approvals LIMIT 1")).first():
        raise RuntimeError("V2 inventory approvals exist: destructive downgrade refused")
    if bind.execute(sa.text("SELECT 1 FROM inventory_approval_policies LIMIT 1")).first():
        raise RuntimeError("V2 inventory approval settings exist: destructive downgrade refused")
    op.drop_index("ix_inventory_approvals_status", table_name="inventory_approvals")
    op.drop_index("ix_inventory_approvals_bar_created", table_name="inventory_approvals")
    op.drop_index("ix_inventory_approvals_bar_id", table_name="inventory_approvals")
    op.drop_table("inventory_approvals")
    op.drop_table("inventory_approval_policies")
