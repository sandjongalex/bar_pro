"""PROMPT 1 - additive warehouse foundations, conservation and tenant isolation."""
from __future__ import annotations

import os
from decimal import Decimal

import pytest
from flask_migrate import upgrade
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import (
    Bar, CashMovement, Product, ProductCategory, StockBalance, StockMovement, User, utcnow,
)
from app.stock_service import stock_service
from app.warehouse_models import (
    WarehouseAllocation, WarehouseLocation, WarehouseStockBalance,
    WarehouseStockMovement, WarehouseTransfer, WarehouseTransferReceipt,
    WarehouseTransitBalance,
)
from app.warehouse_service import WarehouseError, warehouse_service
from test_workflows import env


def _stage(env, main="7", cashier="3"):
    owner, bar, product = env[1], env[2], env[4]
    return warehouse_service.stage_allocation(
        owner, bar.id, {product.id: {"MAIN": main, "CASHIER": cashier}},
    )


def test_two_fixed_locations_are_idempotent_and_scoped(env):
    a = warehouse_service.ensure_locations(env[2].id)
    b = warehouse_service.ensure_locations(env[2].id)
    foreign = warehouse_service.ensure_locations(env[3].id)
    db.session.commit()
    assert set(a) == {"MAIN", "CASHIER"}
    assert {k: v.id for k, v in a.items()} == {k: v.id for k, v in b.items()}
    assert {k: v.bar_id for k, v in a.items()} == {"MAIN": env[2].id, "CASHIER": env[2].id}
    assert set(foreign) == {"MAIN", "CASHIER"}
    assert all(foreign[k].id != a[k].id for k in a)
    assert db.session.scalar(select(func.count()).select_from(WarehouseLocation)) == 4


def test_owner_approved_staged_split_is_conserved_and_never_double_counts(env):
    before = db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == env[2].id, StockBalance.product_id == env[4].id,
    ))
    before_count = db.session.scalar(select(func.count()).select_from(StockMovement))
    staged = _stage(env)
    db.session.commit()
    total = warehouse_service.totals(env[2].id)[env[4].id]
    assert total == {"MAIN": 7, "CASHIER": 3, "TRANSIT": 0, "TOTAL": 10}
    assert staged.status == "STAGED"
    assert warehouse_service.verify_staged(env[2].id)
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == env[2].id, StockBalance.product_id == env[4].id,
    )) == before
    assert db.session.scalar(select(func.count()).select_from(StockMovement)) == before_count
    assert db.session.scalar(select(func.count()).select_from(CashMovement)) == 0
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement)) == 2


def test_staging_repeat_does_not_create_duplicate_balances_or_movements(env):
    first = _stage(env)
    db.session.commit()
    count = db.session.scalar(select(func.count()).select_from(WarehouseStockMovement))
    second = _stage(env)
    db.session.commit()
    assert first.id == second.id
    assert db.session.scalar(select(func.count()).select_from(WarehouseAllocation)) == 1
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement)) == count
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockBalance)) == 2


@pytest.mark.parametrize("main,cashier", [("11", "0"), ("9", "0"), ("-1", "11"), ("5.0000001", "4.9999999")])
def test_staging_never_silently_corrects_a_stock_difference(env, main, cashier):
    with pytest.raises((WarehouseError, ValueError)):
        _stage(env, main, cashier)
    db.session.rollback()
    assert db.session.scalar(select(func.count()).select_from(WarehouseAllocation)) == 0


def test_owner_only_and_no_cross_bar_products(env):
    other = env[3]
    with pytest.raises(PermissionError):
        warehouse_service.stage_allocation(env[5], env[2].id, {env[4].id: {"MAIN": 10, "CASHIER": 0}})
    with pytest.raises(PermissionError):
        warehouse_service.stage_allocation(env[1], other.id, {})
    with pytest.raises(LookupError):
        warehouse_service.stage_allocation(env[1], env[2].id, {999999: {"MAIN": 10, "CASHIER": 0}})
    db.session.rollback()


def test_foreign_product_fk_cannot_be_placed_in_another_bars_location(env):
    foreign_locations = warehouse_service.ensure_locations(env[3].id)
    db.session.flush()
    db.session.add(WarehouseStockBalance(
        bar_id=env[3].id, location_id=foreign_locations["CASHIER"].id,
        product_id=env[4].id, quantity=1, version=0,
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_negative_balances_rejected_by_db_and_by_transit_service(env):
    _stage(env)
    db.session.commit()
    main = warehouse_service.ensure_locations(env[2].id)["MAIN"]
    row = db.session.scalar(select(WarehouseStockBalance).where(
        WarehouseStockBalance.bar_id == env[2].id,
        WarehouseStockBalance.location_id == main.id,
        WarehouseStockBalance.product_id == env[4].id,
    ))
    row.quantity = -1
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()
    with pytest.raises(WarehouseError, match="WAREHOUSE_NOT_LIVE"):
        warehouse_service.dispatch(env[1], env[2].id, "TR-1", {env[4].id: 1})


def test_legacy_change_after_staging_must_trigger_new_reconciliation(env):
    _stage(env)
    db.session.commit()
    stock_service.move(env[1], env[2].id, env[4].id, "LOSS", -1, "Sold/removed in legacy")
    db.session.commit()
    with pytest.raises(WarehouseError, match="ALLOCATION_STALE"):
        warehouse_service.verify_staged(env[2].id)


def test_open_cash_session_blocks_initial_staging(env):
    from app.cash_services import cash_service
    item = cash_service.open(env[1], env[2].id, "STAGING-GUARD", 0)
    db.session.commit()
    with pytest.raises(WarehouseError, match="OPEN_CASH_SESSION"):
        _stage(env)
    db.session.rollback()
    cash_service.close(env[1], env[2].id, item.id, 0)
    db.session.commit()
    assert _stage(env).status == "STAGED"


def test_transfer_ledger_conserves_quantities_and_receipt_retries(env):
    # Only simulate LIVE in an isolated test database. PROMPT 1 intentionally
    # exposes no operation for enabling this state in a running bar.
    state = _stage(env)
    db.session.commit()
    state.status = "LIVE"
    db.session.commit()
    transfer = warehouse_service.dispatch(env[1], env[2].id, "TR-001", {env[4].id: 4})
    db.session.commit()
    data = warehouse_service.totals(env[2].id)[env[4].id]
    assert data == {"MAIN": 3, "CASHIER": 3, "TRANSIT": 4, "TOTAL": 10}
    first = warehouse_service.receive(env[1], env[2].id, transfer.id, {env[4].id: 2}, "receipt-first-001")
    db.session.commit()
    count = db.session.scalar(select(func.count()).select_from(WarehouseStockMovement))
    again = warehouse_service.receive(env[1], env[2].id, transfer.id, {env[4].id: 2}, "receipt-first-001")
    db.session.commit()
    assert again.id == first.id
    assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement)) == count
    assert warehouse_service.totals(env[2].id)[env[4].id] == {
        "MAIN": 3, "CASHIER": 5, "TRANSIT": 2, "TOTAL": 10,
    }
    with pytest.raises(WarehouseError, match="IDEMPOTENCY_CONFLICT"):
        warehouse_service.receive(env[1], env[2].id, transfer.id, {env[4].id: 1}, "receipt-first-001")
    db.session.rollback()
    warehouse_service.receive(env[1], env[2].id, transfer.id, {env[4].id: 2}, "receipt-second-001")
    db.session.commit()
    assert warehouse_service.totals(env[2].id)[env[4].id] == {
        "MAIN": 3, "CASHIER": 7, "TRANSIT": 0, "TOTAL": 10,
    }
    assert db.session.get(WarehouseTransfer, transfer.id).status == "RECEIVED"
    assert db.session.scalar(select(func.count()).select_from(WarehouseTransferReceipt)) == 2
    assert db.session.scalar(select(func.count()).select_from(CashMovement)) == 0


def test_transfer_cannot_overdraw_main(env):
    state = _stage(env, main="2", cashier="8")
    db.session.commit()
    state.status = "LIVE"
    db.session.commit()
    with pytest.raises(WarehouseError, match="INSUFFICIENT_WAREHOUSE_STOCK"):
        warehouse_service.dispatch(env[1], env[2].id, "NO-NEGATIVE", {env[4].id: 3})
    db.session.rollback()
    assert warehouse_service.totals(env[2].id)[env[4].id]["TOTAL"] == 10
    assert db.session.scalar(select(func.count()).select_from(WarehouseTransfer)) == 0


def test_migration_backfills_only_locations_and_preserves_old_stock(tmp_path, monkeypatch):
    uri = f"sqlite:///{tmp_path / 'pre-warehouse.sqlite'}"
    monkeypatch.setenv("TEST_DATABASE_URI", uri)
    app = create_app("testing")
    with app.app_context():
        upgrade(revision="b5c6d7e8f901", directory=app.config["MIGRATIONS_DIRECTORY"])
        owner = User(email="prewarehouse@example.invalid", display_name="Owner", category="OWNER")
        owner.set_password("test")
        db.session.add(owner)
        db.session.flush()
        bar = Bar(owner_id=owner.id, name="Prior bar", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        category = ProductCategory(bar_id=bar.id, name="Beverages")
        db.session.add(category)
        db.session.flush()
        product = Product(bar_id=bar.id, category_id=category.id, sku="ITEM", name="Drink",
                          base_unit="bottle", sale_price=800, valuation_unit_cost=500)
        db.session.add(product)
        db.session.flush()
        db.session.add(StockBalance(bar_id=bar.id, product_id=product.id, quantity=23, version=4))
        db.session.commit()
        bar_id, product_id = bar.id, product.id
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        assert db.session.scalar(select(StockBalance.quantity).where(
            StockBalance.bar_id == bar_id, StockBalance.product_id == product_id,
        )) == 23
        assert db.session.scalar(select(func.count()).select_from(WarehouseLocation).where(
            WarehouseLocation.bar_id == bar_id,
        )) == 2
        assert db.session.scalar(select(func.count()).select_from(WarehouseStockMovement)) == 0
        assert db.session.scalar(select(func.count()).select_from(WarehouseStockBalance)) == 0
        db.session.remove()
        db.engine.dispose()


@pytest.mark.skipif(not os.getenv("WAREHOUSE_MYSQL_TEST_URI"), reason="MySQL CI service not configured")
def test_mysql_migration_backfills_existing_bar_without_touching_stock(monkeypatch):
    monkeypatch.setenv("TEST_DATABASE_URI", os.environ["WAREHOUSE_MYSQL_TEST_URI"])
    app = create_app("testing")
    with app.app_context():
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        # InnoDB creates implicit foreign-key indexes, so compare_metadata can
        # report spurious index diffs. Validate real tables and Alembic head.
        from alembic.config import Config
        from alembic.script import ScriptDirectory
        from sqlalchemy import inspect, text
        cfg = Config()
        cfg.set_main_option("script_location", app.config["MIGRATIONS_DIRECTORY"])
        head = ScriptDirectory.from_config(cfg).get_current_head()
        with db.engine.connect() as conn:
            assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == head
            inspector = inspect(conn)
            required = {"warehouse_locations", "warehouse_stock_balances",
                        "warehouse_transit_balances", "warehouse_stock_movements",
                        "warehouse_allocations", "warehouse_allocation_lines",
                        "warehouse_transfers", "warehouse_transfer_lines",
                        "warehouse_transfer_receipts"}
            assert required <= set(inspector.get_table_names())
            assert any(x["name"] == "ck_warehouse_stock_nonnegative"
                       for x in inspector.get_check_constraints("warehouse_stock_balances"))
        db.session.remove()
        db.engine.dispose()
