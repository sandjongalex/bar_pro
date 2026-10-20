"""Real two-connection InnoDB isolation tests (NEVER production).

Only the dedicated ephemeral loopback MySQL CI database is accepted. Unlike
SQLite's ignored FOR UPDATE, these tests force the same-bar lock to block a
second transaction and assert the persisted ledger after both commits.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from time import sleep
from threading import Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.inventory_approval import decide, submit
from app.inventory_services import inventory_service
from app.inventory_reconciliation import inventory_live_reconciliation
from app.models import (
    Bar, Inventory, Product, ProductCategory, StaffAssignment, StockBalance,
    StockMovement, User, utcnow,
)
from app.order_services import order_service
from app.permissions import permissions
from app.shift_service import start_shift
from app.stock_service import stock_service


URI = os.getenv("BAR_PRO_MYSQL_CONCURRENCY_URI", "")
RUN = os.getenv("BAR_PRO_MYSQL_CONCURRENCY_TEST") == "1"
pytestmark = pytest.mark.skipif(not RUN, reason="Only ephemeral MySQL concurrency CI")


@pytest.fixture(scope="module")
def mysql_app():
    if not RUN:
        pytest.skip("MySQL concurrency suite not enabled")
    url = make_url(URI)
    if not (url.get_backend_name() == "mysql"
            and url.host in {"127.0.0.1", "localhost"}
            and url.database == "barpro_inventory_concurrency_ci"
            and os.getenv("CI") == "true"):
        raise RuntimeError("REFUSED: concurrency integration tests require isolated local CI MySQL")
    app = create_app("testing", {
        "SQLALCHEMY_DATABASE_URI": URI,
        "SECRET_KEY": "ephemeral-ci-test-only-secret",
        "SQLALCHEMY_ENGINE_OPTIONS": {
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 5,
        },
    })
    with app.app_context():
        assert db.engine.dialect.name == "mysql"
        assert db.session.scalar(select(func.count()).select_from(Bar)) == 0, (
            "REFUSED: CI database was not empty; never reuse a populated database"
        )
        db.session.rollback()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def scenario(mysql_app):
    """Seed committed rows so every concurrent worker can use its own session."""
    marker = uuid4().hex[:10]
    with mysql_app.app_context():
        owner = User(
            email=f"concurrency-owner-{marker}@example.invalid",
            display_name="CI inventory owner", category="OWNER",
        )
        cashier = User(
            email=f"concurrency-cashier-{marker}@example.invalid",
            display_name="CI inventory cashier", category="EMPLOYEE",
        )
        for user in (owner, cashier):
            user.set_password("ci-test-only")
        db.session.add_all((owner, cashier))
        db.session.flush()

        bar = Bar(owner_id=owner.id, name="CI Inventory " + marker,
                  timezone="Africa/Douala", currency="XAF")
        db.session.add(bar)
        db.session.flush()
        category = ProductCategory(bar_id=bar.id, name="CI Drinks")
        db.session.add(category)
        db.session.flush()
        product = Product(
            bar_id=bar.id, category_id=category.id,
            name="CI water", sku="CI-WATER", base_unit="bottle",
            sale_price=100, valuation_unit_cost=40,
        )
        assignment = StaffAssignment(
            bar_id=bar.id, user_id=cashier.id, role="CASHIER",
            started_at=utcnow(),
        )
        db.session.add_all((product, assignment))
        db.session.flush()
        start_shift(owner, bar.id, assignment.id)
        stock_service.move(owner, bar.id, product.id, "INITIAL", "10", "Stock initial CI")
        db.session.commit()

        inv = inventory_service.create(
            cashier, bar.id, "CI-INV-"+marker,
            [product.id], "Inventaire simultané CI",
        )
        db.session.commit()
        inventory_service.count_v2_entries(cashier, bar.id, inv.id, [{
            "product_id": product.id, "units": "9", "cases": "",
            "note": "Une bouteille cassée et vérifiée", "revision": 0,
        }])
        db.session.commit()
        submit(cashier, bar.id, inv.id)
        db.session.commit()
        result = (owner.id, bar.id, product.id, inv.id)
        db.session.remove()
    return result


def _work(app, operation, *, ready=None, release=None):
    with app.app_context():
        try:
            owner_id, bar_id, product_id, inv_id = operation["ids"]
            owner = db.session.get(User, owner_id)
            if operation.get("read_before_lock"):
                # Deliberately create an older REPEATABLE READ snapshot.
                db.session.scalar(select(func.count()).select_from(Product))
            if ready is not None:
                permissions.require(owner, operation["permission"], bar_id)
                ready.set()
                assert release is not None and release.wait(15), (
                    "Timed out releasing first InnoDB transaction"
                )
            fn = operation["kind"]
            if fn == "sale":
                order = order_service.create(
                    owner, bar_id, "CI-ORDER-" + uuid4().hex[:16],
                    [{"product_id": product_id, "quantity": 2}],
                )
                order_service.confirm(owner, bar_id, order.id)
            elif fn == "approval":
                decide(owner, bar_id, inv_id, decision="approve",
                       reason="Inventaire contrôlé sur MySQL CI")
            else:
                raise AssertionError(fn)
            db.session.commit()
            return "ok"
        except Exception:
            db.session.rollback()
            raise
        finally:
            db.session.remove()


def _interleave(app, ids, first_kind, second_kind):
    gates = {"sale": "orders.edit", "approval": "inventory.approve"}
    first_ready, release_first = Event(), Event()
    second_started = Event()

    def first():
        return _work(app, {
            "ids": ids, "kind": first_kind, "permission": gates[first_kind],
        }, ready=first_ready, release=release_first)

    def second():
        second_started.set()
        return _work(app, {
            "ids": ids, "kind": second_kind,
            "permission": gates[second_kind],
            "read_before_lock": True,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(first)
        assert first_ready.wait(10), "First transaction did not acquire bar lock"
        fb = pool.submit(second)
        try:
            assert second_started.wait(10), "Concurrent worker did not start"
            assert not fb.done(), "Second worker committed before bar lock release"
            # A real InnoDB row lock must hold the second writer beyond the
            # time needed for thread setup, not just ordinary thread queuing.
            sleep(0.3)
            assert not fb.done(), "Concurrent transaction bypassed bar lock"
        finally:
            release_first.set()
        assert fa.result(timeout=20) == "ok"
        assert fb.result(timeout=20) == "ok"


def _verify(app, ids):
    with app.app_context():
        _, bar_id, product_id, inv_id = ids
        balance = db.session.scalar(select(StockBalance).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id == product_id,
        ))
        inv = db.session.get(Inventory, inv_id)
        all_movements = db.session.scalars(select(StockMovement).where(
            StockMovement.bar_id == bar_id,
            StockMovement.product_id == product_id,
        )).all()
        assert balance.quantity == Decimal("7.000000")
        assert balance.version == 3  # initial, sale, one inventory correction
        assert inv.status == "POSTED"
        assert sum(m.movement_type == "SALE" for m in all_movements) == 1
        adjustments = [m for m in all_movements if m.movement_type == "INVENTORY_ADJUSTMENT"]
        assert len(adjustments) == 1, "An inventory cannot be adjusted twice"
        assert adjustments[0].quantity_delta == Decimal("-1.000000")
        assert db.session.scalar(
            select(func.coalesce(func.sum(StockMovement.quantity_delta), 0)).where(
                StockMovement.bar_id == bar_id, StockMovement.product_id == product_id,
            )
        ) == balance.quantity
        db.session.rollback()


def test_sale_finishes_before_owner_approval(mysql_app, scenario):
    _interleave(mysql_app, scenario, "sale", "approval")
    _verify(mysql_app, scenario)


def test_owner_approval_finishes_before_sale(mysql_app, scenario):
    _interleave(mysql_app, scenario, "approval", "sale")
    _verify(mysql_app, scenario)


def test_double_approval_is_serialized_and_never_double_posts(mysql_app, scenario):
    owner_id, bar_id, product_id, inv_id = scenario
    first_ready, release = Event(), Event()
    competitor_started = Event()

    def approved_first():
        return _work(mysql_app, {
            "ids": scenario, "kind": "approval", "permission": "inventory.approve",
        }, ready=first_ready, release=release)

    def approve_second():
        competitor_started.set()
        return _work(mysql_app, {
            "ids": scenario, "kind": "approval", "permission": "inventory.approve",
            "read_before_lock": True,
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(approved_first)
        assert first_ready.wait(10)
        fb = pool.submit(approve_second)
        try:
            assert competitor_started.wait(10)
            assert not fb.done()
        finally:
            release.set()
        assert fa.result(timeout=20) == "ok"
        with pytest.raises(ValueError, match="INVENTORY_REVIEW_STATE"):
            fb.result(timeout=20)

    with mysql_app.app_context():
        balance = db.session.scalar(select(StockBalance.quantity).where(
            StockBalance.bar_id == bar_id, StockBalance.product_id == product_id
        ))
        adjustments = db.session.scalars(select(StockMovement).where(
            StockMovement.bar_id == bar_id,
            StockMovement.movement_type == "INVENTORY_ADJUSTMENT",
        )).all()
        assert balance == Decimal("9.000000")
        assert len(adjustments) == 1
        db.session.rollback()
