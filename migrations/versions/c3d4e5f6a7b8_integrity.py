"""Reconcile tender and tenant source integrity without rewriting history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
revision="c3d4e5f6a7b8"
down_revision="b2c3d4e5f6a7"
branch_labels=None
depends_on=None

def upgrade():
    if op.get_bind().dialect.name == "mysql":
        op.alter_column("products","units_per_case",existing_type=sa.Integer(),type_=mysql.INTEGER(unsigned=True),existing_nullable=True)
        for table,columns in {
            "user_sessions":{"session_digest":32,"csrf_digest":32},
            "api_tokens":{"token_digest":32,"family_id":16},
            "audit_logs":{"request_id":16},
            "idempotency_records":{"idempotency_key":128,"request_hash":32},
        }.items():
            for column,length in columns.items():
                op.alter_column(table,column,existing_type=sa.LargeBinary(length),type_=mysql.VARBINARY(length) if length==128 else mysql.BINARY(length),existing_nullable=False)

    op.execute("UPDATE payments SET amount_presented=amount, amount_applied=amount WHERE amount_applied=0 AND amount_presented=0")
    op.execute("""UPDATE orders SET payment_status=CASE
        WHEN (SELECT COALESCE(SUM(amount_applied),0) FROM payments WHERE payments.order_id=orders.id AND payments.bar_id=orders.bar_id) >= orders.total_amount - (SELECT COALESCE(SUM(total_amount),0) FROM order_returns WHERE order_returns.order_id=orders.id AND order_returns.bar_id=orders.bar_id AND order_returns.status='POSTED') THEN 'PAID'
        ELSE 'PARTIAL' END
        WHERE EXISTS (SELECT 1 FROM payments WHERE payments.order_id=orders.id AND payments.bar_id=orders.bar_id)""")
    with op.batch_alter_table("payments") as batch:
        batch.create_check_constraint("ck_payments_tender", "amount = amount_applied AND amount_applied > 0 AND change_given >= 0 AND amount_presented = amount_applied + change_given")
    with op.batch_alter_table("stock_balances") as batch:
        batch.create_check_constraint("ck_stock_balances_nonnegative", "quantity >= 0 AND version >= 0")
    with op.batch_alter_table("stock_movements") as batch:
        batch.create_check_constraint("ck_stock_movement_type", "movement_type IN ('INITIAL','PURCHASE','SALE','RETURN','LOSS','ADJUSTMENT','INVENTORY_ADJUSTMENT')")
    for table,sources in (
        ("cash_movements", {"payment_id":"payments","refund_id":"refunds","supplier_payment_id":"supplier_payments","expense_id":"expenses","cash_handover_id":"cash_handovers","reversal_of_id":"cash_movements"}),
        ("staff_cash_ledgers", {"payment_id":"payments","refund_id":"refunds","cash_handover_id":"cash_handovers","reversal_of_id":"staff_cash_ledgers"}),
        ("expenses", {"reversal_of_id":"expenses"}),
    ):
        with op.batch_alter_table(table) as batch:
            for column,target in sources.items():
                batch.create_foreign_key(f"fk_{table}_{column}",target,["bar_id",column],["bar_id","id"],ondelete="RESTRICT")
    with op.batch_alter_table("order_return_lines") as batch:
        batch.create_foreign_key("fk_return_line_order_return","order_returns",["bar_id","order_id","order_return_id"],["bar_id","order_id","id"],ondelete="RESTRICT")
        batch.create_foreign_key("fk_return_line_order","order_lines",["bar_id","order_id","order_line_id"],["bar_id","order_id","id"],ondelete="RESTRICT")
        batch.create_foreign_key("fk_return_line_product","order_lines",["bar_id","order_line_id","product_id"],["bar_id","id","product_id"],ondelete="RESTRICT")

def downgrade():
    raise RuntimeError("Refusing to remove financial integrity constraints.")
