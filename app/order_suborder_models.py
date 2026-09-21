"""Persistent sub-orders attached to one customer order.

A sub-order records an additional round of products without merging those
products into the original order lines.  Cashier-created additions can be
served immediately while still waiting for acknowledgement by the server
assigned to the parent order.
"""
from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.extensions import db
from app.models import ID, MONEY, QTY, Tenant, datetime_type, integer, tenant_args


class OrderSuborder(Tenant, db.Model):
    __tablename__ = "order_suborders"

    id = db.Column(ID, primary_key=True)
    order_id = db.Column(ID, nullable=False)
    sequence_no = db.Column(integer(unsigned=True), nullable=False)
    assigned_staff_id = db.Column(ID)
    status = db.Column(db.String(24), nullable=False, default="PENDING_VALIDATION")
    delivery_status = db.Column(db.String(16), nullable=False, default="PENDING")
    currency = db.Column(db.String(3), nullable=False)
    subtotal_amount = db.Column(MONEY, nullable=False, default=0)
    discount_amount = db.Column(MONEY, nullable=False, default=0)
    tax_amount = db.Column(MONEY, nullable=False, default=0)
    total_amount = db.Column(MONEY, nullable=False, default=0)
    note = db.Column(db.String(500))
    created_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    validated_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))
    validated_at = db.Column(datetime_type(fsp=6))
    rejected_at = db.Column(datetime_type(fsp=6))
    delivered_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))
    delivered_at = db.Column(datetime_type(fsp=6))
    cancelled_at = db.Column(datetime_type(fsp=6))

    __table_args__ = tenant_args(
        "order_suborders",
        ForeignKeyConstraint(
            ["bar_id", "order_id"],
            ["orders.bar_id", "orders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "assigned_staff_id"],
            ["staff_assignments.bar_id", "staff_assignments.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "bar_id",
            "order_id",
            "sequence_no",
            name="uq_order_suborders_sequence",
        ),
        UniqueConstraint(
            "bar_id",
            "order_id",
            "id",
            name="uq_order_suborders_order_id",
        ),
        CheckConstraint("sequence_no > 0", name="ck_order_suborders_sequence"),
        CheckConstraint(
            "status IN ('PENDING_VALIDATION','VALIDATED','REJECTED','CANCELLED')",
            name="ck_order_suborders_status",
        ),
        CheckConstraint(
            "delivery_status IN ('PENDING','DELIVERED')",
            name="ck_order_suborders_delivery_status",
        ),
        CheckConstraint(
            "subtotal_amount >= 0 AND discount_amount >= 0 AND tax_amount >= 0 "
            "AND discount_amount <= subtotal_amount "
            "AND total_amount = subtotal_amount-discount_amount+tax_amount",
            name="ck_order_suborders_amounts",
        ),
        Index(
            "ix_order_suborders_order_status",
            "bar_id",
            "order_id",
            "status",
            "sequence_no",
        ),
    )


class OrderSuborderLine(Tenant, db.Model):
    __tablename__ = "order_suborder_lines"

    id = db.Column(ID, primary_key=True)
    order_suborder_id = db.Column(ID, nullable=False)
    order_id = db.Column(ID, nullable=False)
    product_id = db.Column(ID, nullable=False)
    line_no = db.Column(integer(unsigned=True), nullable=False)
    product_name_snapshot = db.Column(db.String(160), nullable=False)
    unit_snapshot = db.Column(db.String(16), nullable=False)
    quantity = db.Column(QTY, nullable=False)
    note = db.Column(db.String(500))
    unit_sale_price_snapshot = db.Column(MONEY, nullable=False)
    unit_cost_snapshot = db.Column(MONEY, nullable=False)
    subtotal_amount = db.Column(MONEY, nullable=False)
    discount_amount = db.Column(MONEY, nullable=False, default=0)
    tax_amount = db.Column(MONEY, nullable=False, default=0)
    total_amount = db.Column(MONEY, nullable=False)

    __table_args__ = tenant_args(
        "order_suborder_lines",
        ForeignKeyConstraint(
            ["bar_id", "order_id", "order_suborder_id"],
            ["order_suborders.bar_id", "order_suborders.order_id", "order_suborders.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "product_id"],
            ["products.bar_id", "products.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "bar_id",
            "order_suborder_id",
            "line_no",
            name="uq_order_suborder_lines_no",
        ),
        UniqueConstraint(
            "bar_id",
            "id",
            "product_id",
            name="uq_order_suborder_lines_id_product",
        ),
        CheckConstraint(
            "line_no > 0 AND quantity > 0 AND unit_sale_price_snapshot >= 0 "
            "AND unit_cost_snapshot >= 0 AND subtotal_amount >= 0 "
            "AND discount_amount >= 0 AND tax_amount >= 0 "
            "AND discount_amount <= subtotal_amount "
            "AND total_amount = subtotal_amount-discount_amount+tax_amount",
            name="ck_order_suborder_lines_amounts",
        ),
    )
