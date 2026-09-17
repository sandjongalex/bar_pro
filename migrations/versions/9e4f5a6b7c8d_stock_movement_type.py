"""stock movement type"""
from alembic import op
import sqlalchemy as sa
revision="9e4f5a6b7c8d"
down_revision="8d3e2f4a5b6c"
branch_labels=None
depends_on=None
def upgrade(): op.add_column("stock_movements",sa.Column("movement_type",sa.String(24),nullable=False,server_default="ADJUSTMENT"))
def downgrade(): raise RuntimeError("Refusing destructive downgrade of stock history.")
