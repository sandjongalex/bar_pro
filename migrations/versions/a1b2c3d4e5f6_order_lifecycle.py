"""order confirmation and service lifecycle"""
from alembic import op
import sqlalchemy as sa
revision="a1b2c3d4e5f6"
down_revision="9e4f5a6b7c8d"
branch_labels=None
depends_on=None
def upgrade():
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("ck_orders_status",type_="check")
    op.execute("UPDATE orders SET status='CONFIRMED' WHERE status='POSTED'")
    op.execute("UPDATE orders SET status='SERVED' WHERE status='CLOSED'")
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("payment_status",sa.String(16),nullable=False,server_default="UNPAID"))
        batch.add_column(sa.Column("notes",sa.String(500)))
        batch.create_check_constraint("ck_orders_status","status IN ('DRAFT','CONFIRMED','SERVED','CANCELLED')")
        batch.create_check_constraint("ck_orders_payment_status","payment_status IN ('UNPAID','PARTIAL','PAID')")
    with op.batch_alter_table("order_lines") as batch: batch.add_column(sa.Column("note",sa.String(500)))
def downgrade(): raise RuntimeError("Refusing destructive downgrade of orders.")
