"""Per-bar 58/80 mm receipt format. Existing bars retain 58 mm.

Revision ID: e2a4b6c8d0f1
Revises: c0f1a2b3c4d5
"""
from alembic import op
import sqlalchemy as sa

revision = "e2a4b6c8d0f1"
down_revision = "c0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("bars") as batch:
        batch.add_column(sa.Column(
            "printer_width_mm", sa.SmallInteger(), nullable=False,
            server_default="58",
        ))
        batch.create_check_constraint(
            "ck_bars_printer_width", "printer_width_mm IN (58,80)"
        )


def downgrade():
    raise RuntimeError("Refusing to erase per-bar paper settings.")
