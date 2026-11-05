"""Include non-sale stock movements in V1 inventory theoretical quantities.

Revision ID: f7b8c9d0e1f2
Revises: e8f4b6c0d3a2
"""
from alembic import op
import sqlalchemy as sa


revision = "f7b8c9d0e1f2"
down_revision = "e8f4b6c0d3a2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "inventory_line_snapshots",
        sa.Column(
            "other_movement_quantity",
            sa.Numeric(20, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade():
    op.drop_column("inventory_line_snapshots", "other_movement_quantity")
