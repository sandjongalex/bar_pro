"""Add persistent cashier sub-orders awaiting server validation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def _types():
    dialect = op.get_bind().dialect.name
    id_type = sa.Integer() if dialect == "sqlite" else sa.BigInteger()
    dt_type = sa.DateTime() if dialect == "sqlite" else mysql.DATETIME(fsp=6)
    int_type = sa.Integer() if dialect == "sqlite" else mysql.INTEGER(unsigned=True)
    return id_type, dt_type, int_type


def upgrade():
    id_type, dt_type, int_type = _types()
    money = sa.Numeric(19, 4)
    qty = sa.Numeric(20, 6)

    op.create_table(
        "order_suborders",
        sa.Column("id", id_type, primary_key=True, nullable=False),
        sa.Column("order_id", id_type, nullable=False),
        sa.Column("sequence_no", int_type, nullable=False),
        sa.Column("assigned_staff_id", id_type, nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("delivery_status", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("subtotal_amount", money, nullable=False),
        sa.Column("discount_amount", money, nullable=False),
        sa.Column("tax_amount", money, nullable=False),
        sa.Column("total_amount", money, nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("created_by_id", id_type, nullable=False),
        sa.Column("validated_by_id", id_type, nullable=True),
        sa.Column("validated_at", dt_type, nullable=True),
        sa.Column("rejected_at", dt_type, nullable=True),
        sa.Column("delivered_by_id", id_type, nullable=True),
        sa.Column("delivered_at", dt_type, nullable=True),
        sa.Column("cancelled_at", dt_type, nullable=True),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.Column("bar_id", id_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["validated_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["delivered_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "assigned_staff_id"],
            ["staff_assignments.bar_id", "staff_assignments.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("bar_id", "id", name="uq_order_suborders_bar_id_id"),
        sa.UniqueConstraint(
            "bar_id",
            "order_id",
            "sequence_no",
            name="uq_order_suborders_sequence",
        ),
        sa.UniqueConstraint(
            "bar_id",
            "order_id",
            "id",
            name="uq_order_suborders_order_id",
        ),
        sa.CheckConstraint("sequence_no > 0", name="ck_order_suborders_sequence"),
        sa.CheckConstraint(
            "status IN ('PENDING_VALIDATION','VALIDATED','REJECTED','CANCELLED')",
            name="ck_order_suborders_status",
        ),
        sa.CheckConstraint(
            "delivery_status IN ('PENDING','DELIVERED')",
            name="ck_order_suborders_delivery_status",
        ),
        sa.CheckConstraint(
            "subtotal_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 "
            "AND discount_amount <= subtotal_amount "
            "AND total_amount = subtotal_amount-discount_amount+tax_amount",
            name="ck_order_suborders_amounts",
        ),
    )
    op.create_index("ix_order_suborders_bar_id", "order_suborders", ["bar_id"])
    op.create_index(
        "ix_order_suborders_bar_created",
        "order_suborders",
        ["bar_id", "created_at", "id"],
    )
    op.create_index(
        "ix_order_suborders_order_status",
        "order_suborders",
        ["bar_id", "order_id", "status", "sequence_no"],
    )

    op.create_table(
        "order_suborder_lines",
        sa.Column("id", id_type, primary_key=True, nullable=False),
        sa.Column("order_suborder_id", id_type, nullable=False),
        sa.Column("order_id", id_type, nullable=False),
        sa.Column("product_id", id_type, nullable=False),
        sa.Column("line_no", int_type, nullable=False),
        sa.Column("product_name_snapshot", sa.String(160), nullable=False),
        sa.Column("unit_snapshot", sa.String(16), nullable=False),
        sa.Column("quantity", qty, nullable=False),
        sa.Column("note", sa.String(500), nullable=True),
        sa.Column("unit_sale_price_snapshot", money, nullable=False),
        sa.Column("unit_cost_snapshot", money, nullable=False),
        sa.Column("subtotal_amount", money, nullable=False),
        sa.Column("discount_amount", money, nullable=False),
        sa.Column("tax_amount", money, nullable=False),
        sa.Column("total_amount", money, nullable=False),
        sa.Column("created_at", dt_type, nullable=False),
        sa.Column("updated_at", dt_type, nullable=False),
        sa.Column("bar_id", id_type, nullable=False),
        sa.ForeignKeyConstraint(["bar_id"], ["bars.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bar_id", "order_id", "order_suborder_id"],
            ["order_suborders.bar_id", "order_suborders.order_id", "order_suborders.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["bar_id", "product_id"],
            ["products.bar_id", "products.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("bar_id", "id", name="uq_order_suborder_lines_bar_id_id"),
        sa.UniqueConstraint(
            "bar_id",
            "order_suborder_id",
            "line_no",
            name="uq_order_suborder_lines_no",
        ),
        sa.UniqueConstraint(
            "bar_id",
            "id",
            "product_id",
            name="uq_order_suborder_lines_id_product",
        ),
        sa.CheckConstraint(
            "line_no > 0 AND quantity > 0 AND unit_sale_price_snapshot >= 0 "
            "AND unit_cost_snapshot >= 0 AND subtotal_amount >= 0 "
            "AND discount_amount >= 0 AND tax_amount >= 0 "
            "AND discount_amount <= subtotal_amount "
            "AND total_amount = subtotal_amount-discount_amount+tax_amount",
            name="ck_order_suborder_lines_amounts",
        ),
    )
    op.create_index("ix_order_suborder_lines_bar_id", "order_suborder_lines", ["bar_id"])
    op.create_index(
        "ix_order_suborder_lines_bar_created",
        "order_suborder_lines",
        ["bar_id", "created_at", "id"],
    )


def downgrade():
    op.drop_index("ix_order_suborder_lines_bar_created", table_name="order_suborder_lines")
    op.drop_index("ix_order_suborder_lines_bar_id", table_name="order_suborder_lines")
    op.drop_table("order_suborder_lines")
    op.drop_index("ix_order_suborders_order_status", table_name="order_suborders")
    op.drop_index("ix_order_suborders_bar_created", table_name="order_suborders")
    op.drop_index("ix_order_suborders_bar_id", table_name="order_suborders")
    op.drop_table("order_suborders")
