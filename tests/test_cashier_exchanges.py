import re

import pytest
from sqlalchemy import func, select

from app.cash_services import cash_service
from app.exchange_services import exchange_service
from app.extensions import db
from app.models import AuditLog, Order, Product, StaffAssignment, StockBalance, StockMovement, User, utcnow
from app.stock_service import stock_service
from test_workflows import env


def _cashier(bar, email="cashier-exchange@example.invalid"):
    cashier = User(email=email, display_name="Cashier Exchange", category="EMPLOYEE")
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=cashier.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.commit()
    return cashier


def _product(owner, bar, source, name, sku, price, stock):
    product = Product(
        bar_id=bar.id,
        category_id=source.category_id,
        name=name,
        sku=sku,
        base_unit="bottle",
        sale_price=price,
        valuation_unit_cost=40,
    )
    db.session.add(product)
    db.session.flush()
    if stock:
        stock_service.move(owner, bar.id, product.id, "INITIAL", stock, f"Stock {name}")
    db.session.commit()
    return product


def _stock(bar_id, product_id):
    return db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id == product_id,
        )
    ) or 0


def _csrf_from(page):
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": "test-password", "csrf_token": _csrf_from(page)},
        follow_redirects=False,
    )


def test_exchange_is_independent_from_orders_and_collects_only_difference(env):
    _, owner, bar, _, returned, _, _, _ = env
    replacement = _product(owner, bar, returned, "Castel", "CASTEL", 150, 5)
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "EXCHANGE-CASH", 1000)
    db.session.commit()

    result = exchange_service.create(
        cashier,
        bar.id,
        returned.id,
        replacement.id,
        1,
        1,
        "RESTOCK",
        "Changement demandé par la serveuse",
        settlement_method="CASH",
    )
    db.session.commit()

    assert result.difference_amount == 50
    assert result.settlement_amount == 50
    assert result.settlement_direction == "COLLECT"
    assert _stock(bar.id, returned.id) == 11
    assert _stock(bar.id, replacement.id) == 4
    assert cash_service.expected(session) == 1050
    assert db.session.scalar(select(func.count()).select_from(Order)) == 0

    audit = db.session.scalar(
        select(AuditLog).where(
            AuditLog.bar_id == bar.id,
            AuditLog.action == "exchanges.record",
        )
    )
    assert audit is not None
    assert audit.changes["reference"].startswith("ECH-")
    assert audit.changes["returned_product_name"] == returned.name
    assert audit.changes["replacement_product_name"] == "Castel"
    assert audit.changes["settlement_amount"] == "50.0000"


def test_equal_price_exchange_requires_no_payment(env):
    _, owner, bar, _, returned, _, _, _ = env
    replacement = _product(owner, bar, returned, "33 Export", "33-EXPORT", 100, 3)
    cashier = _cashier(bar)

    result = exchange_service.create(
        cashier,
        bar.id,
        returned.id,
        replacement.id,
        1,
        1,
        "RESTOCK",
        "Échange même valeur",
    )
    db.session.commit()

    assert result.difference_amount == 0
    assert result.settlement_direction == "NONE"
    assert result.settlement_method is None
    assert _stock(bar.id, returned.id) == 11
    assert _stock(bar.id, replacement.id) == 2


def test_cheaper_replacement_refunds_difference_from_open_cash_session(env):
    _, owner, bar, _, returned, _, _, _ = env
    returned.sale_price = 150
    replacement = _product(owner, bar, returned, "Petite Guinness", "GUINNESS-S", 100, 3)
    db.session.commit()
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "EXCHANGE-REFUND", 500)
    db.session.commit()

    result = exchange_service.create(
        cashier,
        bar.id,
        returned.id,
        replacement.id,
        1,
        1,
        "RESTOCK",
        "Remplacement moins cher",
        settlement_method="CASH",
    )
    db.session.commit()

    assert result.difference_amount == -50
    assert result.settlement_amount == 50
    assert result.settlement_direction == "REFUND"
    assert cash_service.expected(session) == 450


def test_failed_exchange_rolls_back_return_and_cash(env):
    _, owner, bar, _, returned, _, _, _ = env
    empty = _product(owner, bar, returned, "Rupture", "EMPTY-EXCHANGE", 150, 0)
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "EXCHANGE-ROLLBACK", 1000)
    db.session.commit()

    original_returned_stock = _stock(bar.id, returned.id)
    original_movements = db.session.scalar(select(func.count()).select_from(StockMovement))

    with pytest.raises(ValueError, match="INSUFFICIENT_STOCK"):
        exchange_service.create(
            cashier,
            bar.id,
            returned.id,
            empty.id,
            1,
            1,
            "RESTOCK",
            "Doit échouer",
            settlement_method="CASH",
        )
    db.session.rollback()

    assert _stock(bar.id, returned.id) == original_returned_stock
    assert _stock(bar.id, empty.id) == 0
    assert cash_service.expected(session) == 1000
    assert db.session.scalar(select(func.count()).select_from(StockMovement)) == original_movements
    assert db.session.scalar(
        select(func.count()).select_from(AuditLog).where(AuditLog.action == "exchanges.record")
    ) == 0


def test_cashier_returns_page_is_now_standalone_exchange_ui(env):
    app, _, bar, _, _, _, _, _ = env
    cashier = _cashier(bar, "cashier-exchange-ui@example.invalid")
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/cashier-returns")
    assert page.status_code == 200
    assert "Aucune facture nécessaire" in page.text
    assert "Boisson rendue" in page.text
    assert "Boisson de remplacement" in page.text
    assert "order_id" not in page.text
