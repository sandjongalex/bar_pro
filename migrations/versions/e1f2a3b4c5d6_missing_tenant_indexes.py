"""Reconcile tenant indexes declared by newer customer and shift ORM models.

Safe for existing deployments: only missing indexes are created. No table,
order, stock or payment data is modified.
"""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None

INDEXES = (
    ("customer_case_entries", "ix_customer_case_entries_bar_id"),
    ("customer_ledger_entries", "ix_customer_ledger_entries_bar_id"),
    ("employee_shifts", "ix_employee_shifts_bar_id"),
)

def upgrade():
    for table, name in INDEXES:
        if not op.get_context().as_sql:
            present = {value["name"] for value in sa.inspect(op.get_bind()).get_indexes(table)}
            if name in present:
                continue
        op.create_index(name, table, ["bar_id"])

def downgrade():
    raise RuntimeError("Refusing destructive downgrade of operational tenant indexes.")
