"""Immutable, tenant-bound evidence that supplier receipts belong to MAIN.

PRE_CUTOVER receipts preserve compatibility with the legacy shared stock.
LIVE receipts additionally mutate the physical MAIN balance. Nothing here
allocates goods to CASHIER or changes supplier-payment accounting.
"""
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.extensions import db
from app.models import ID, MONEY, QTY, Tenant, datetime_type, tenant_args


class WarehousePurchaseReceiptLine(Tenant, db.Model):
    __tablename__ = "warehouse_purchase_receipt_lines"

    id = db.Column(ID, primary_key=True)
    purchase_line_id = db.Column(ID, nullable=False)
    location_id = db.Column(ID, nullable=False)
    product_id = db.Column(ID, nullable=False)
    quantity = db.Column(QTY, nullable=False)
    unit_cost_snapshot = db.Column(MONEY, nullable=False)
    mode = db.Column(db.String(16), nullable=False)
    warehouse_movement_id = db.Column(ID)
    reversal_movement_id = db.Column(ID)
    received_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    received_at = db.Column(datetime_type(fsp=6), nullable=False)
    reversed_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"))
    reversed_at = db.Column(datetime_type(fsp=6))

    __table_args__ = tenant_args(
        "warehouse_purchase_receipt_lines",
        ForeignKeyConstraint(
            ["bar_id", "purchase_line_id"], ["purchase_lines.bar_id", "purchase_lines.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "location_id"], ["warehouse_locations.bar_id", "warehouse_locations.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "product_id"], ["products.bar_id", "products.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "warehouse_movement_id"],
            ["warehouse_stock_movements.bar_id", "warehouse_stock_movements.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["bar_id", "reversal_movement_id"],
            ["warehouse_stock_movements.bar_id", "warehouse_stock_movements.id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("bar_id", "purchase_line_id", name="uq_warehouse_purchase_receipt_line"),
        UniqueConstraint("bar_id", "warehouse_movement_id", name="uq_warehouse_purchase_movement"),
        UniqueConstraint("bar_id", "reversal_movement_id", name="uq_warehouse_purchase_reversal"),
        CheckConstraint(
            "quantity > 0 AND unit_cost_snapshot >= 0",
            name="ck_warehouse_purchase_receipt_values",
        ),
        CheckConstraint(
            "(mode = 'PRE_CUTOVER' AND warehouse_movement_id IS NULL) OR "
            "(mode = 'LIVE' AND warehouse_movement_id IS NOT NULL)",
            name="ck_warehouse_purchase_receipt_mode",
        ),
        CheckConstraint(
            "(reversed_at IS NULL AND reversed_by_id IS NULL AND reversal_movement_id IS NULL) OR "
            "(reversed_at IS NOT NULL AND reversed_by_id IS NOT NULL AND "
            "((mode = 'PRE_CUTOVER' AND reversal_movement_id IS NULL) OR "
            "(mode = 'LIVE' AND reversal_movement_id IS NOT NULL)))",
            name="ck_warehouse_purchase_receipt_reversal",
        ),
        Index("ix_warehouse_purchase_main_receipts", "bar_id", "location_id", "received_at", "id"),
    )
