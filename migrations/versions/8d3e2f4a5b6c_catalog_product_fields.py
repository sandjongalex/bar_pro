"""catalog product fields"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
revision="8d3e2f4a5b6c"
down_revision="7c2a1b8d9e10"
branch_labels=None
depends_on=None
def upgrade():
    with op.batch_alter_table("products") as batch:
        batch.add_column(sa.Column("stock_alert_threshold",sa.Numeric(20,6),nullable=False,server_default="0"))
        batch.add_column(sa.Column("units_per_case",mysql.INTEGER(unsigned=True).with_variant(sa.Integer(),"sqlite")))
        batch.add_column(sa.Column("image_key",sa.String(255)))
def downgrade(): raise RuntimeError("Refusing destructive downgrade; archive catalogue data explicitly.")
