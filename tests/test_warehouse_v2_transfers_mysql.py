"""Live MySQL 8 warehouse vouchers, constrained transit and source history."""
import os
from decimal import Decimal

import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect, select, text

from app import create_app
from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance, User
from app.warehouse_models import (
    WarehouseTransferEvent, WarehouseTransferLine,
    WarehouseStockMovement,
)
from app.warehouse_service import warehouse_service
from app.warehouse_transfer_service import warehouse_transfer_service as service


@pytest.mark.skipif(not os.getenv("WAREHOUSE_MYSQL_TEST_URI"), reason="MySQL not configured")
def test_mysql_transfer_migration_stock_integrity_and_legacy_upgrade(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URI", os.environ["WAREHOUSE_MYSQL_TEST_URI"])
    app = create_app("testing")
    with app.app_context():
        # Exercising the exact production starting revision a9b0.
        upgrade(revision="a9b0c1d2e3f4", directory=app.config["MIGRATIONS_DIRECTORY"])
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        assert db.session.execute(text("SELECT version_num FROM alembic_version")).scalar() == "f2c9a4d1b8e7"
        schema = inspect(db.engine)
        assert "warehouse_transfer_events" in schema.get_table_names()
        assert any(x["name"] == "ck_warehouse_transfer_line_values"
                   for x in schema.get_check_constraints("warehouse_transfer_lines"))
        owner = User(email="mysql-warehouse-transfer-owner@example.invalid",
                     display_name="Transfer Owner", category="OWNER")
        owner.set_password("test-only")
        db.session.add(owner)
        db.session.flush()
        bar = Bar(owner_id=owner.id, name="Transfer CI", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        category = ProductCategory(bar_id=bar.id, name="Boissons")
        db.session.add(category)
        db.session.flush()
        product = Product(bar_id=bar.id, category_id=category.id, name="33 Export",
                          sku="33-TRANSFER", base_unit="bottle", sale_price=800,
                          valuation_unit_cost=500, units_per_case=4)
        db.session.add(product)
        db.session.flush()
        db.session.add(StockBalance(bar_id=bar.id, product_id=product.id, quantity=10, version=0))
        db.session.commit()
        allocation = warehouse_service.stage_allocation(
            owner, bar.id, {product.id: {"MAIN": 8, "CASHIER": 2}},
        )
        db.session.commit()
        allocation.status = "LIVE"  # Only on this isolated disposable CI DB.
        db.session.commit()

        transfer = service.create_request(owner, bar.id, "MYSQL-TR-1", {
            product.id: {"unit": "CASE", "quantity": "2"},
        })
        service.prepare(owner, bar.id, transfer.id, {product.id: 6})
        service.ship(owner, bar.id, transfer.id)
        db.session.commit()
        assert warehouse_service.totals(bar.id)[product.id] == {
            "MAIN": 2, "CASHIER": 2, "TRANSIT": 6, "TOTAL": 10,
        }
        service.receive(owner, bar.id, transfer.id, {product.id: 4}, "mysql-receipt-0001")
        service.report_dispute(owner, bar.id, transfer.id, {product.id: 2}, "Deux bouteilles absentes du bon")
        db.session.commit()
        assert warehouse_service.totals(bar.id)[product.id]["TRANSIT"] == 2
        service.resolve_dispute(owner, bar.id, transfer.id, product.id, 2,
                                "LOSS_APPROVED", "Déclaration perte validée par propriétaire")
        service.close(owner, bar.id, transfer.id, "Bon soldé après déclaration de perte")
        db.session.commit()
        assert warehouse_service.totals(bar.id)[product.id] == {
            "MAIN": 2, "CASHIER": 6, "TRANSIT": 0, "TOTAL": 8,
        }
        assert db.session.scalar(select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id, StockBalance.product_id == product.id,
        )) == 10  # Legacy sales stock is unaffected by this isolated V2 ledger.
        assert product.valuation_unit_cost == Decimal("500")
        assert len(db.session.scalars(select(WarehouseTransferEvent).where(
            WarehouseTransferEvent.bar_id == bar.id,
        )).all()) >= 5
        assert len(db.session.scalars(select(WarehouseStockMovement).where(
            WarehouseStockMovement.bar_id == bar.id,
        )).all()) >= 6
        db.session.remove()
        db.engine.dispose()
