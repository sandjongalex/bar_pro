"""Add optional due date to manual customer debt ledger rows.

Revision ID: d1e2f3a4b5c6
Revises: e1f2a3b4c5d6
"""
from alembic import op
import sqlalchemy as sa

revision = "d1e2f3a4b5c6"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable: existing customer entries keep their original semantics.
    with op.batch_alter_table("customer_ledger_entries") as batch:
        batch.add_column(sa.Column("due_date", sa.Date(), nullable=True))
    op.create_index(
        "ix_customer_ledger_due_date",
        "customer_ledger_entries",
        ["bar_id", "due_date", "id"],
    )


def downgrade():
    op.drop_index("ix_customer_ledger_due_date", table_name="customer_ledger_entries")
    with op.batch_alter_table("customer_ledger_entries") as batch:
        batch.drop_column("due_date")
