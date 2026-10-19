"""Independent bar and owner administrative governance (no data backfill)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "ba5a5a1f0001"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None
STATES = "'DRAFT','PENDING','INCOMPLETE','APPROVED','REJECTED','SUSPENDED'"

def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")
    op.create_table(
        "bar_governance",
        sa.Column("bar_id", ident, primary_key=True, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("suspension_level", sa.SmallInteger(), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("changed_by_id", ident, nullable=False),
        sa.Column("changed_at", dt, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(f"status IN ({STATES})", name="ck_bar_governance_status"),
        sa.CheckConstraint("(status = 'SUSPENDED' AND suspension_level IN (1,2)) OR "
                           "(status <> 'SUSPENDED' AND suspension_level = 0)", name="ck_bar_governance_level"),
    )
    op.create_index("ix_bar_governance_status", "bar_governance", ["status", "suspension_level", "bar_id"])
    op.create_table(
        "owner_governance",
        sa.Column("owner_id", ident, primary_key=True, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("changed_by_id", ident, nullable=False),
        sa.Column("changed_at", dt, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(f"status IN ({STATES})", name="ck_owner_governance_status"),
    )
    op.create_index("ix_owner_governance_status", "owner_governance", ["status", "owner_id"])

def downgrade():
    from sqlalchemy import text
    bind = op.get_bind()
    if bind.execute(text("SELECT 1 FROM bar_governance LIMIT 1")).first() or (
        bind.execute(text("SELECT 1 FROM owner_governance LIMIT 1")).first()
    ):
        raise RuntimeError("Administrative history present; downgrade prohibited")
    op.drop_index("ix_owner_governance_status", table_name="owner_governance")
    op.drop_table("owner_governance")
    op.drop_index("ix_bar_governance_status", table_name="bar_governance")
    op.drop_table("bar_governance")
