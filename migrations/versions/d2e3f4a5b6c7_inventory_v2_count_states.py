"""Add per-product count timestamps and authors without modifying legacy inventory."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "d2e3f4a5b6c7"
down_revision = "c1d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    dt = mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), "sqlite")
    op.create_table(
        "inventory_line_count_states",
        sa.Column("id", ident, primary_key=True, nullable=False),
        sa.Column("bar_id", ident, nullable=False),
        sa.Column("inventory_line_id", ident, nullable=False),
        sa.Column("counted_at", dt),
        sa.Column("counted_by_id", ident, nullable=False),
        sa.Column("revision", ident, nullable=False),
        sa.Column("created_at", dt, nullable=False),
        sa.Column("updated_at", dt, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "inventory_line_id"],
            ["inventory_lines.bar_id", "inventory_lines.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["counted_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("bar_id", "id", name="uq_inventory_line_count_states_bar_id_id"),
        sa.UniqueConstraint("bar_id", "inventory_line_id", name="uq_inventory_line_count_state"),
        sa.CheckConstraint("revision > 0", name="ck_inventory_line_count_revision"),
    )
    op.create_index("ix_inventory_line_count_states_bar_id",
                    "inventory_line_count_states", ["bar_id"])
    op.create_index("ix_inventory_line_count_states_bar_created",
                    "inventory_line_count_states", ["bar_id", "created_at", "id"])
    op.create_index("ix_inventory_line_count_actor",
                    "inventory_line_count_states", ["bar_id", "counted_by_id", "counted_at"])


def downgrade():
    if op.get_bind().execute(
        sa.text("SELECT 1 FROM inventory_line_count_states LIMIT 1")
    ).first():
        raise RuntimeError("Physical count history exists; destructive downgrade prohibited")
    op.drop_index("ix_inventory_line_count_actor", table_name="inventory_line_count_states")
    op.drop_index("ix_inventory_line_count_states_bar_created", table_name="inventory_line_count_states")
    op.drop_index("ix_inventory_line_count_states_bar_id", table_name="inventory_line_count_states")
    op.drop_table("inventory_line_count_states")
