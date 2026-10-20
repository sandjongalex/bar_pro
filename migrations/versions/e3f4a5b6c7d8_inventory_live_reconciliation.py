"""Add non-destructive ledger cursors for V2 live reconciliation.

Older V2 draft inventories deliberately retain NULL cursors and require recount
rather than silently assuming an inaccurate inventory start snapshot.
"""
from alembic import op
import sqlalchemy as sa

revision = "e3f4a5b6c7d8"
down_revision = "d2e3f4a5b6c7"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.add_column("inventory_cashier_assignments",
                  sa.Column("movement_checkpoint_id", ident, nullable=True))
    op.add_column("inventory_line_count_states",
                  sa.Column("movement_checkpoint_id", ident, nullable=True))


def downgrade():
    op.drop_column("inventory_line_count_states", "movement_checkpoint_id")
    op.drop_column("inventory_cashier_assignments", "movement_checkpoint_id")
