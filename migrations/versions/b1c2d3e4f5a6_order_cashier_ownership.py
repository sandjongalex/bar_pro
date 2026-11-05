"""Assign every operational order to the cashier responsible for it.

Existing orders are attributed from cashier delivery audit events where
available, then from cashier-created counter sales. Orders without reliable
evidence remain unassigned for owner review.
"""
from alembic import op
import sqlalchemy as sa


revision = "b1c2d3e4f5a6"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    ident = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("cashier_assignment_id", ident, nullable=True))
        batch.create_foreign_key(
            "fk_orders_cashier_assignment",
            "staff_assignments",
            ["bar_id", "cashier_assignment_id"],
            ["bar_id", "id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        "ix_orders_cashier_queue",
        "orders",
        ["bar_id", "cashier_assignment_id", "status", "payment_status", "id"],
    )

    # First use the cashier who actually confirmed delivery, based on the
    # immutable audit log and the assignment that was valid at that time.
    op.execute(sa.text("""
        UPDATE orders
        SET cashier_assignment_id = (
            SELECT sa.id
            FROM audit_logs al
            JOIN staff_assignments sa
              ON sa.bar_id = al.bar_id
             AND sa.user_id = al.actor_id
             AND sa.role = 'CASHIER'
            WHERE al.bar_id = orders.bar_id
              AND al.target_table = 'orders'
              AND al.target_id = orders.id
              AND al.action = 'orders.deliver'
              AND al.outcome = 'SUCCESS'
              AND sa.started_at <= al.occurred_at
              AND (sa.ended_at IS NULL OR sa.ended_at >= al.occurred_at)
            ORDER BY al.occurred_at DESC, al.id DESC, sa.started_at DESC, sa.id DESC
            LIMIT 1
        )
        WHERE orders.cashier_assignment_id IS NULL
          AND orders.status IN ('CONFIRMED', 'SERVED')
    """))

    # Counter sales created directly by a cashier are attributed to their
    # assignment even if they have not yet been delivered.
    op.execute(sa.text("""
        UPDATE orders
        SET cashier_assignment_id = (
            SELECT sa.id
            FROM staff_assignments sa
            WHERE sa.bar_id = orders.bar_id
              AND sa.user_id = orders.created_by_id
              AND sa.role = 'CASHIER'
              AND sa.started_at <= orders.created_at
              AND (sa.ended_at IS NULL OR sa.ended_at >= orders.created_at)
            ORDER BY sa.started_at DESC, sa.id DESC
            LIMIT 1
        )
        WHERE orders.cashier_assignment_id IS NULL
    """))


def downgrade():
    bind = op.get_bind()
    if bind.execute(sa.text(
        "SELECT 1 FROM orders WHERE cashier_assignment_id IS NOT NULL LIMIT 1"
    )).first():
        raise RuntimeError("Cashier invoice ownership exists; destructive downgrade refused")
    op.drop_index("ix_orders_cashier_queue", table_name="orders")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("fk_orders_cashier_assignment", type_="foreignkey")
        batch.drop_column("cashier_assignment_id")
