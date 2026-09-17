"""bar settings

Revision ID: 7c2a1b8d9e10
Revises: 6b1599cad0b4
"""
from alembic import op
import sqlalchemy as sa

revision = "7c2a1b8d9e10"
down_revision = "6b1599cad0b4"
branch_labels = None
depends_on = None

def upgrade():
    with op.batch_alter_table("bars") as batch:
        batch.add_column(sa.Column("address", sa.String(500)))
        batch.add_column(sa.Column("phone", sa.String(32)))
        batch.add_column(sa.Column("logo_key", sa.String(255)))
        batch.add_column(sa.Column("stock_alert_threshold", sa.Numeric(20, 6), nullable=False, server_default="0"))
        batch.add_column(sa.Column("credit_sales_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))

def downgrade():
    raise RuntimeError("Refusing destructive downgrade; review retained bar settings explicitly.")
