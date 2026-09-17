"""payment tender amounts"""
from alembic import op
import sqlalchemy as sa
revision="b2c3d4e5f6a7"; down_revision="a1b2c3d4e5f6"; branch_labels=None; depends_on=None
def upgrade():
 with op.batch_alter_table("payments") as batch:
  batch.add_column(sa.Column("amount_presented",sa.Numeric(19,4),nullable=False,server_default="0"))
  batch.add_column(sa.Column("amount_applied",sa.Numeric(19,4),nullable=False,server_default="0"))
  batch.add_column(sa.Column("change_given",sa.Numeric(19,4),nullable=False,server_default="0"))
def downgrade(): raise RuntimeError("Refusing destructive downgrade of payments.")
