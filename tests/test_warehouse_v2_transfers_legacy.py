"""Existing pre-PROMPT-4 transfer rows survive the additive voucher migration."""
from decimal import Decimal

from flask_migrate import upgrade
from sqlalchemy import MetaData, Table, select

from app import create_app
from app.extensions import db
from app.models import Bar, Product, ProductCategory, User, utcnow
from app.warehouse_models import WarehouseTransfer, WarehouseTransferLine


def test_old_partial_transfer_retains_quantities_and_actor(tmp_path):
    app = create_app("testing", {
        "SECRET_KEY": "legacy-transfer-test-only",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'legacy_transfer.sqlite'}",
    })
    with app.app_context():
        upgrade(revision="e8f4b6c0d3a2", directory=app.config["MIGRATIONS_DIRECTORY"])
        owner = User(email="legacy-transfer@example.invalid", display_name="Owner", category="OWNER")
        owner.set_password("test")
        db.session.add(owner)
        db.session.flush()
        bar = Bar(owner_id=owner.id, name="Legacy transfer", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        category = ProductCategory(bar_id=bar.id, name="Drinks")
        db.session.add(category)
        db.session.flush()
        product = Product(
            bar_id=bar.id, category_id=category.id, name="Legacy bottle",
            sku="LEGACY-TRANSFER", base_unit="bottle", sale_price=800,
            valuation_unit_cost=500,
        )
        db.session.add(product)
        db.session.flush()
        metadata = MetaData()
        old_transfers = Table("warehouse_transfers", metadata, autoload_with=db.session.connection())
        old_lines = Table("warehouse_transfer_lines", metadata, autoload_with=db.session.connection())
        now = utcnow()
        transfer_id = db.session.execute(old_transfers.insert().values(
            bar_id=bar.id, reference="PRE-V2-TR",
            status="PARTIAL", dispatched_by_id=owner.id, dispatched_at=now,
            created_at=now, updated_at=now,
        )).inserted_primary_key[0]
        db.session.execute(old_lines.insert().values(
            bar_id=bar.id, transfer_id=transfer_id, product_id=product.id,
            quantity_dispatched=Decimal("10"), quantity_received=Decimal("4"),
            quantity_disputed=Decimal("2"), unit_cost_snapshot=Decimal("500"),
            created_at=now, updated_at=now,
        ))
        db.session.commit()
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        voucher = db.session.get(WarehouseTransfer, transfer_id)
        line = db.session.scalar(select(WarehouseTransferLine).where(
            WarehouseTransferLine.bar_id == bar.id,
            WarehouseTransferLine.transfer_id == transfer_id,
        ))
        assert voucher.status == "PARTIALLY_RECEIVED"
        assert voucher.requested_by_id == owner.id
        assert voucher.prepared_by_id == owner.id
        assert voucher.dispatched_by_id == owner.id
        assert voucher.recipient_user_id is None  # unknown; never fabricated
        assert line.quantity_requested == Decimal("10")
        assert line.quantity_prepared == Decimal("10")
        assert line.quantity_dispatched == Decimal("10")
        assert line.quantity_received == Decimal("4")
        assert line.quantity_disputed == Decimal("2")
        assert line.quantity_lost == 0
        assert line.unit_cost_snapshot == Decimal("500")
        db.session.remove()
        db.engine.dispose()
