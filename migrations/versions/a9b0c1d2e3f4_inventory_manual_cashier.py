"""Allow V2 inventory attribution before a cashier account or shift exists.

A manually supplied cashier name remains an audit snapshot, not an employee
login or attendance record. Existing assignments are preserved.
"""
from alembic import op
import sqlalchemy as sa

revision = "a9b0c1d2e3f4"
down_revision = "b5c6d7e8f901"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inventory_cashier_assignments") as batch:
        batch.alter_column("cashier_shift_id", existing_type=sa.BigInteger(),
                           existing_nullable=False, nullable=True)
        batch.alter_column("cashier_user_id", existing_type=sa.BigInteger(),
                           existing_nullable=False, nullable=True)


def downgrade():
    connection = op.get_bind()
    manual = connection.execute(sa.text(
        "SELECT id FROM inventory_cashier_assignments "
        "WHERE cashier_shift_id IS NULL OR cashier_user_id IS NULL LIMIT 1"
    )).first()
    if manual:
        raise RuntimeError("Inventaires affectés manuellement : retour arrière interdit")
    with op.batch_alter_table("inventory_cashier_assignments") as batch:
        batch.alter_column("cashier_user_id", existing_type=sa.BigInteger(),
                           existing_nullable=True, nullable=False)
        batch.alter_column("cashier_shift_id", existing_type=sa.BigInteger(),
                           existing_nullable=True, nullable=False)
