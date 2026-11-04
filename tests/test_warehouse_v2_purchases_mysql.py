"""Live MySQL 8 Alembic upgrade over existing supplier purchase and staff data."""
import os

import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect, select

from app import create_app
from app.extensions import db
from app.models import (
    Bar, Product, ProductCategory, Purchase, PurchaseLine, StaffAssignment,
    Supplier, User, utcnow,
)
from app.purchase_services import purchase_service
from app.warehouse_models import WarehouseStockMovement
from app.warehouse_purchase_models import WarehousePurchaseReceiptLine


@pytest.mark.skipif(not os.getenv("WAREHOUSE_MYSQL_TEST_URI"), reason="MySQL not configured")
def test_mysql_purchase_upgrade_preserves_existing_supplier_invoice_and_roles(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URI", os.environ["WAREHOUSE_MYSQL_TEST_URI"])
    app = create_app("testing")
    with app.app_context():
        upgrade(revision="d7e3a5b9c2f1", directory=app.config["MIGRATIONS_DIRECTORY"])
        owner = User(email="mysql-buy-owner@example.invalid", display_name="Owner", category="OWNER")
        owner.set_password("test-password")
        cashier = User(email="mysql-buy-cashier@example.invalid", display_name="Cashier", category="EMPLOYEE")
        cashier.set_password("test-password")
        db.session.add_all([owner, cashier])
        db.session.flush()
        bar = Bar(owner_id=owner.id, name="MySQL Purchase Warehouse", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        category = ProductCategory(bar_id=bar.id, name="Drinks")
        db.session.add(category)
        db.session.flush()
        product = Product(
            bar_id=bar.id, category_id=category.id, sku="MYPUR", name="Bière",
            base_unit="bouteille", sale_price=800, valuation_unit_cost=0,
            units_per_case=24, is_active=True,
        )
        supplier = Supplier(bar_id=bar.id, name="Fournisseur existant", is_active=True)
        shift = StaffAssignment(bar_id=bar.id, user_id=cashier.id, role="CASHIER", started_at=utcnow())
        db.session.add_all([product, supplier, shift])
        db.session.flush()
        purchase = Purchase(
            bar_id=bar.id, supplier_id=supplier.id,
            reference="EXISTING-DRAFT", status="DRAFT", currency="XAF",
            supplier_name_snapshot=supplier.name,
            purchase_date=utcnow().date(), subtotal_amount=4800,
            discount_amount=0, tax_amount=0, total_amount=4800, created_by_id=owner.id,
        )
        db.session.add(purchase)
        db.session.flush()
        line = PurchaseLine(
            bar_id=bar.id, purchase_id=purchase.id, product_id=product.id,
            line_no=1, product_name_snapshot=product.name, unit_snapshot=product.base_unit,
            quantity=24, unit_cost_snapshot=200, purchase_unit="CASE", purchase_quantity=1,
            units_per_case_snapshot=24, purchase_unit_price_snapshot=4800,
            subtotal_amount=4800, discount_amount=0, tax_amount=0, total_amount=4800,
        )
        db.session.add(line)
        db.session.flush()
        ids = owner.id, cashier.id, bar.id, supplier.id, purchase.id, line.id, shift.id
        db.session.commit()
        db.session.remove()

        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        owner_id, cashier_id, bar_id, supplier_id, purchase_id, line_id, shift_id = ids
        assert db.session.get(User, cashier_id).category == "EMPLOYEE"
        assert db.session.get(StaffAssignment, shift_id).role == "CASHIER"
        assert db.session.get(Supplier, supplier_id).name == "Fournisseur existant"
        assert db.session.get(Purchase, purchase_id).status == "DRAFT"
        assert db.session.get(PurchaseLine, line_id).quantity == 24
        assert "warehouse_purchase_receipt_lines" in inspect(db.engine).get_table_names()
        names = {x["name"] for x in inspect(db.engine).get_check_constraints("warehouse_stock_movements")}
        assert "ck_warehouse_movement_bucket" in names
        purchase_service.receive(db.session.get(User, owner_id), bar_id, purchase_id)
        db.session.commit()
        receipts = db.session.scalars(select(WarehousePurchaseReceiptLine).where(
            WarehousePurchaseReceiptLine.bar_id == bar_id
        )).all()
        assert len(receipts) == 1
        assert receipts[0].mode == "PRE_CUTOVER"
        assert receipts[0].quantity == 24
        assert db.session.scalar(select(WarehouseStockMovement.id).where(
            WarehouseStockMovement.bar_id == bar_id,
            WarehouseStockMovement.movement_type == "PURCHASE_IN",
        )) is None
        db.session.remove()
        db.engine.dispose()
