"""Add invoice-independent bottle exchanges with linked stock and cash journals."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('beverage_exchanges',
    sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('reference', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('staff_assignment_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('returned_product_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('replacement_product_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('returned_name', sa.String(length=160), nullable=False),
    sa.Column('replacement_name', sa.String(length=160), nullable=False),
    sa.Column('returned_quantity', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('replacement_quantity', sa.Numeric(precision=20, scale=6), nullable=False),
    sa.Column('returned_price', sa.Numeric(precision=19, scale=4), nullable=False),
    sa.Column('replacement_price', sa.Numeric(precision=19, scale=4), nullable=False),
    sa.Column('supplement', sa.Numeric(precision=19, scale=4), nullable=False),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('reason', sa.String(length=500), nullable=False),
    sa.Column('created_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('decided_by_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('decided_at', mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), 'sqlite'), nullable=True),
    sa.Column('return_movement_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('replacement_movement_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('cash_movement_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=True),
    sa.Column('bar_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
    sa.Column('created_at', mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), 'sqlite'), nullable=False),
    sa.Column('updated_at', mysql.DATETIME(fsp=6).with_variant(sa.DateTime(), 'sqlite'), nullable=False),
    sa.CheckConstraint("(status = 'PENDING' AND decided_by_id IS NULL AND decided_at IS NULL) OR (status <> 'PENDING' AND decided_by_id IS NOT NULL AND decided_at IS NOT NULL)", name='ck_beverage_exchanges_decision'),
    sa.CheckConstraint("(status = 'POSTED' AND return_movement_id IS NOT NULL AND replacement_movement_id IS NOT NULL AND ((supplement = 0 AND cash_movement_id IS NULL) OR (supplement > 0 AND cash_movement_id IS NOT NULL))) OR (status <> 'POSTED' AND return_movement_id IS NULL AND replacement_movement_id IS NULL AND cash_movement_id IS NULL)", name='ck_beverage_exchanges_journals'),
    sa.CheckConstraint("status IN ('PENDING','POSTED','CANCELLED')", name='ck_beverage_exchanges_status'),
    sa.CheckConstraint('returned_product_id <> replacement_product_id AND returned_quantity > 0 AND replacement_quantity > 0 AND returned_price >= 0 AND replacement_price >= 0 AND supplement >= 0', name='ck_beverage_exchanges_values'),
    sa.ForeignKeyConstraint(['bar_id', 'cash_movement_id'], ['cash_movements.bar_id', 'cash_movements.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['bar_id', 'replacement_movement_id'], ['stock_movements.bar_id', 'stock_movements.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['bar_id', 'replacement_product_id'], ['products.bar_id', 'products.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['bar_id', 'return_movement_id'], ['stock_movements.bar_id', 'stock_movements.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['bar_id', 'returned_product_id'], ['products.bar_id', 'products.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['bar_id', 'staff_assignment_id'], ['staff_assignments.bar_id', 'staff_assignments.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['bar_id'], ['bars.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['decided_by_id'], ['users.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('bar_id', 'cash_movement_id', name='uq_beverage_exchanges_cash'),
    sa.UniqueConstraint('bar_id', 'id', name='uq_beverage_exchanges_bar_id_id'),
    sa.UniqueConstraint('bar_id', 'reference', name='uq_beverage_exchanges_reference'),
    sa.UniqueConstraint('bar_id', 'replacement_movement_id', name='uq_beverage_exchanges_replacement'),
    sa.UniqueConstraint('bar_id', 'return_movement_id', name='uq_beverage_exchanges_return')
    )
    op.create_index('ix_beverage_exchanges_bar_created', 'beverage_exchanges', ['bar_id', 'created_at', 'id'], unique=False)
    op.create_index(op.f('ix_beverage_exchanges_bar_id'), 'beverage_exchanges', ['bar_id'], unique=False)


def downgrade():
    raise RuntimeError("Refusing to delete the exchange audit trail; restore a backup explicitly.")
