"""Warehouse V2 foundations: physical locations, transit, immutable allocations.

These tables are additive. Legacy StockBalance/StockMovement remain authoritative
until a later, explicitly controlled integration and activation.
"""
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint

from app.extensions import db
from app.models import ID, MONEY, QTY, Tenant, datetime_type, tenant_args


class WarehouseLocation(Tenant, db.Model):
    __tablename__ = "warehouse_locations"
    id = db.Column(ID, primary_key=True)
    code = db.Column(db.String(16), nullable=False)
    label = db.Column(db.String(80), nullable=False)
    __table_args__ = tenant_args(
        "warehouse_locations",
        UniqueConstraint("bar_id", "code", name="uq_warehouse_locations_code"),
        CheckConstraint("code IN ('MAIN','CASHIER')", name="ck_warehouse_locations_code"),
    )


class WarehouseStockBalance(Tenant, db.Model):
    __tablename__ = "warehouse_stock_balances"
    id = db.Column(ID, primary_key=True)
    location_id = db.Column(ID, nullable=False)
    product_id = db.Column(ID, nullable=False)
    quantity = db.Column(QTY, nullable=False, default=Decimal("0"))
    version = db.Column(ID, nullable=False, default=0)
    __table_args__ = tenant_args(
        "warehouse_stock_balances",
        ForeignKeyConstraint(["bar_id", "location_id"], ["warehouse_locations.bar_id", "warehouse_locations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        UniqueConstraint("bar_id", "location_id", "product_id", name="uq_warehouse_stock_location_product"),
        CheckConstraint("quantity >= 0 AND version >= 0", name="ck_warehouse_stock_nonnegative"),
        Index("ix_warehouse_stock_product", "bar_id", "product_id", "location_id"),
    )


class WarehouseTransitBalance(Tenant, db.Model):
    __tablename__ = "warehouse_transit_balances"
    id = db.Column(ID, primary_key=True)
    product_id = db.Column(ID, nullable=False)
    quantity = db.Column(QTY, nullable=False, default=Decimal("0"))
    version = db.Column(ID, nullable=False, default=0)
    __table_args__ = tenant_args(
        "warehouse_transit_balances",
        ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        UniqueConstraint("bar_id", "product_id", name="uq_warehouse_transit_product"),
        CheckConstraint("quantity >= 0 AND version >= 0", name="ck_warehouse_transit_nonnegative"),
    )


class WarehouseAllocation(Tenant, db.Model):
    __tablename__ = "warehouse_allocations"
    id = db.Column(ID, primary_key=True)
    status = db.Column(db.String(16), nullable=False, default="STAGED")
    validated_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    validated_at = db.Column(datetime_type(fsp=6), nullable=False)
    legacy_movement_checkpoint_id = db.Column(ID)
    __table_args__ = tenant_args(
        "warehouse_allocations",
        UniqueConstraint("bar_id", name="uq_warehouse_allocations_one_bar"),
        CheckConstraint("status IN ('STAGED','LIVE')", name="ck_warehouse_allocations_status"),
    )


class WarehouseAllocationLine(Tenant, db.Model):
    __tablename__ = "warehouse_allocation_lines"
    id = db.Column(ID, primary_key=True)
    allocation_id = db.Column(ID, nullable=False)
    product_id = db.Column(ID, nullable=False)
    legacy_quantity_snapshot = db.Column(QTY, nullable=False)
    legacy_balance_version = db.Column(ID, nullable=False)
    main_quantity = db.Column(QTY, nullable=False)
    cashier_quantity = db.Column(QTY, nullable=False)
    unit_cost_snapshot = db.Column(MONEY, nullable=False)
    __table_args__ = tenant_args(
        "warehouse_allocation_lines",
        ForeignKeyConstraint(["bar_id", "allocation_id"], ["warehouse_allocations.bar_id", "warehouse_allocations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        UniqueConstraint("bar_id", "allocation_id", "product_id", name="uq_warehouse_allocation_product"),
        CheckConstraint(
            "legacy_quantity_snapshot >= 0 AND legacy_balance_version >= 0 AND main_quantity >= 0 AND cashier_quantity >= 0 AND unit_cost_snapshot >= 0 AND main_quantity + cashier_quantity = legacy_quantity_snapshot",
            name="ck_warehouse_allocation_conservation",
        ),
    )


class WarehouseTransfer(Tenant, db.Model):
    __tablename__ = "warehouse_transfers"
    id = db.Column(ID, primary_key=True)
    reference = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), nullable=False, default="IN_TRANSIT")
    dispatched_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    dispatched_at = db.Column(datetime_type(fsp=6), nullable=False)
    __table_args__ = tenant_args(
        "warehouse_transfers",
        UniqueConstraint("bar_id", "reference", name="uq_warehouse_transfers_reference"),
        CheckConstraint("status IN ('IN_TRANSIT','PARTIAL','DISPUTED','RECEIVED')", name="ck_warehouse_transfers_status"),
    )


class WarehouseTransferLine(Tenant, db.Model):
    __tablename__ = "warehouse_transfer_lines"
    id = db.Column(ID, primary_key=True)
    transfer_id = db.Column(ID, nullable=False)
    product_id = db.Column(ID, nullable=False)
    quantity_dispatched = db.Column(QTY, nullable=False)
    quantity_received = db.Column(QTY, nullable=False, default=Decimal("0"))
    quantity_disputed = db.Column(QTY, nullable=False, default=Decimal("0"))
    unit_cost_snapshot = db.Column(MONEY, nullable=False)
    __table_args__ = tenant_args(
        "warehouse_transfer_lines",
        ForeignKeyConstraint(["bar_id", "transfer_id"], ["warehouse_transfers.bar_id", "warehouse_transfers.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        UniqueConstraint("bar_id", "transfer_id", "product_id", name="uq_warehouse_transfer_product"),
        CheckConstraint(
            "quantity_dispatched > 0 AND quantity_received >= 0 AND quantity_disputed >= 0 AND quantity_received + quantity_disputed <= quantity_dispatched AND unit_cost_snapshot >= 0",
            name="ck_warehouse_transfer_line_values",
        ),
    )


class WarehouseTransferReceipt(Tenant, db.Model):
    __tablename__ = "warehouse_transfer_receipts"
    id = db.Column(ID, primary_key=True)
    transfer_id = db.Column(ID, nullable=False)
    idempotency_key = db.Column(db.String(80), nullable=False)
    payload_hash = db.Column(db.String(64), nullable=False)
    quantities = db.Column(db.JSON, nullable=False)
    received_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    received_at = db.Column(datetime_type(fsp=6), nullable=False)
    __table_args__ = tenant_args(
        "warehouse_transfer_receipts",
        ForeignKeyConstraint(["bar_id", "transfer_id"], ["warehouse_transfers.bar_id", "warehouse_transfers.id"], ondelete="RESTRICT"),
        UniqueConstraint("bar_id", "idempotency_key", name="uq_warehouse_receipt_idempotency"),
    )


class WarehouseStockMovement(Tenant, db.Model):
    __tablename__ = "warehouse_stock_movements"
    id = db.Column(ID, primary_key=True)
    location_id = db.Column(ID)
    product_id = db.Column(ID, nullable=False)
    movement_type = db.Column(db.String(20), nullable=False)
    quantity_delta = db.Column(QTY, nullable=False)
    unit_cost_snapshot = db.Column(MONEY, nullable=False)
    allocation_line_id = db.Column(ID)
    transfer_line_id = db.Column(ID)
    receipt_id = db.Column(ID)
    occurred_at = db.Column(datetime_type(fsp=6), nullable=False)
    recorded_by_id = db.Column(ID, db.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    reason = db.Column(db.String(500), nullable=False)
    __table_args__ = tenant_args(
        "warehouse_stock_movements",
        ForeignKeyConstraint(["bar_id", "location_id"], ["warehouse_locations.bar_id", "warehouse_locations.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "product_id"], ["products.bar_id", "products.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "allocation_line_id"], ["warehouse_allocation_lines.bar_id", "warehouse_allocation_lines.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "transfer_line_id"], ["warehouse_transfer_lines.bar_id", "warehouse_transfer_lines.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["bar_id", "receipt_id"], ["warehouse_transfer_receipts.bar_id", "warehouse_transfer_receipts.id"], ondelete="RESTRICT"),
        CheckConstraint(
            "(location_id IS NOT NULL AND movement_type IN ('OPENING','TRANSFER_OUT','TRANSFER_IN','ADJUSTMENT','LOSS','PURCHASE_IN','PURCHASE_REVERSAL')) OR (location_id IS NULL AND movement_type IN ('TRANSIT_IN','TRANSIT_OUT'))",
            name="ck_warehouse_movement_bucket",
        ),
        CheckConstraint("quantity_delta <> 0 AND unit_cost_snapshot >= 0", name="ck_warehouse_movement_values"),
        Index("ix_warehouse_movements_product_time", "bar_id", "product_id", "occurred_at", "id"),
    )
