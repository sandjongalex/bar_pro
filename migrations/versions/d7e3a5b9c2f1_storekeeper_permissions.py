"""Allow STOREKEEPER on existing staff assignments and add owner-delegated grants.

Revision ID: d7e3a5b9c2f1
Revises: c6f2d4e8a1b0
Existing accounts and assignments are kept intact.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "d7e3a5b9c2f1"
down_revision = "c6f2d4e8a1b0"
branch_labels = None
depends_on = None

ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")


def _staff_role_check(value):
    with op.batch_alter_table("staff_assignments") as batch:
        if op.get_bind().dialect.name == "sqlite":
            # Alembic's SQLite batch-copy otherwise tries to INSERT a value
            # into the generated active_user_id column. Rebuild it as a
            # generated column with *no* copied expression; SQLite recomputes
            # its value for every preserved assignment automatically.
            batch.drop_column("active_user_id")
            batch.add_column(
                sa.Column(
                    "active_user_id", ident,
                    sa.Computed("CASE WHEN ended_at IS NULL THEN user_id ELSE NULL END"),
                )
            )
        batch.drop_constraint("ck_staff_role", type_="check")
        batch.create_check_constraint("ck_staff_role", f"role IN ({value})")


def upgrade():
    _staff_role_check("'BAR_ADMIN','CASHIER','SERVER','STOREKEEPER'")
    op.create_table(
        "warehouse_staff_grants",
        sa.Column("id", ident, primary_key=True),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("staff_assignment_id", ident, nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("granted_by_id", ident, nullable=False),
        sa.Column("granted_at", dt, nullable=False),
        sa.Column("revoked_by_id", ident),
        sa.Column("revoked_at", dt),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "staff_assignment_id"],
            ["staff_assignments.bar_id", "staff_assignments.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["granted_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["revoked_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_warehouse_staff_grants_bar_id_id"),
        sa.UniqueConstraint(
            "bar_id", "staff_assignment_id", "action",
            name="uq_warehouse_staff_grant_action",
        ),
        sa.CheckConstraint(
            "(enabled = 1 AND revoked_at IS NULL AND revoked_by_id IS NULL) "
            "OR (enabled = 0 AND revoked_at IS NOT NULL AND revoked_by_id IS NOT NULL)",
            name="ck_warehouse_staff_grant_state",
        ),
    )
    op.create_index("ix_warehouse_staff_grants_bar_id", "warehouse_staff_grants", ["bar_id"])
    op.create_index(
        "ix_warehouse_staff_grants_bar_created", "warehouse_staff_grants",
        ["bar_id", "created_at", "id"],
    )


def downgrade():
    if not op.get_context().as_sql:
        bind = op.get_bind()
        if bind.execute(sa.text(
            "SELECT 1 FROM staff_assignments WHERE role = 'STOREKEEPER' LIMIT 1"
        )).first():
            raise RuntimeError("STOREKEEPER_EXISTS: unsafe role downgrade refused")
        if bind.execute(sa.text(
            "SELECT 1 FROM warehouse_staff_grants LIMIT 1"
        )).first():
            raise RuntimeError("WAREHOUSE_GRANTS_EXIST: unsafe downgrade refused")
    op.drop_table("warehouse_staff_grants")
    _staff_role_check("'BAR_ADMIN','CASHIER','SERVER'")
