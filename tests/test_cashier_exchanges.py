import re
from decimal import Decimal

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import AuditLog, Product, ProductCategory, StaffAssignment, StockBalance, User, utcnow
from app.shift_service import start_shift
from app.stock_service import stock_service
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def _cashier(owner, bar):
    user = User(
        email="exchange-cashier@example.invalid",
        display_name="Alex Caisse",
        category="EMPLOYEE",
        is_active=True,
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id,
        user_id=user.id,
        role="CASHIER",
        started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    start_shift(owner, bar.id, assignment.id)
    db.session.commit()
    return user, assignment


def _quantity(bar_id, product_id):
    return Decimal(
        db.session.scalar(
            select(StockBalance.quantity).where(
                StockBalance.bar_id == bar_id,
                StockBalance.product_id == product_id,
            )
        )
        or 0
    )


def _replacement(owner, bar, source_product):
    product = Product(
        bar_id=bar.id,
        category_id=source_product.category_id,
        name="Castel",
        sku="CASTEL-EXCHANGE",
        base_unit="bottle",
        sale_price=150,
        valuation_unit_cost=60,
        is_active=True,
    )
    db.session.add(product)
    db.session.flush()
    stock_service.move(owner, bar.id, product.id, "INITIAL", 5, "Stock échange")
    db.session.commit()
    return product


def test_cashier_can_exchange_beer_without_invoice_and_collect_difference(env):
    app, owner, bar, _, returned_product, _, _, _ = env
    replacement = _replacement(owner, bar, returned_product)
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(cashier, bar.id, "EXCHANGE-CASH", 1000)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/cashier-exchanges")
    assert page.status_code == 200
    assert "Échange bière" in page.text
    assert "Changer une bière directement à la caisse" in page.text
    assert returned_product.name in page.text
    assert replacement.name in page.text

    response = client.post(
        f"/bars/{bar.id}/cashier-exchanges",
        data={
            "csrf_token": _csrf(page),
            "returned_product_id": str(returned_product.id),
            "replacement_product_id": str(replacement.id),
            "returned_quantity": "1",
            "replacement_quantity": "1",
            "returned_disposition": "RESTOCK",
            "settlement_method": "CASH",
            "provider_transaction_id": "",
            "reason": "Client change de boisson",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Supplément encaissé" in response.text
    assert "50" in response.text

    assert _quantity(bar.id, returned_product.id) == Decimal("11")
    assert _quantity(bar.id, replacement.id) == Decimal("4")
    assert cash_service.expected(session) == Decimal("1050")

    audit = db.session.scalar(
        select(AuditLog)
        .where(AuditLog.bar_id == bar.id, AuditLog.action == "exchanges.record")
        .order_by(AuditLog.id.desc())
    )
    assert audit is not None
    assert audit.changes["returned_product_id"] == returned_product.id
    assert audit.changes["replacement_product_id"] == replacement.id
    assert Decimal(audit.changes["settlement_amount"]) == Decimal("50")
    assert audit.changes["settlement_direction"] == "COLLECT"


def test_cashier_exchange_rejects_same_product_without_moving_stock_or_cash(env):
    app, owner, bar, _, product, _, _, _ = env
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(cashier, bar.id, "EXCHANGE-SAME", 1000)
    db.session.commit()
    before_stock = _quantity(bar.id, product.id)

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier-exchanges")
    response = client.post(
        f"/bars/{bar.id}/cashier-exchanges",
        data={
            "csrf_token": _csrf(page),
            "returned_product_id": str(product.id),
            "replacement_product_id": str(product.id),
            "returned_quantity": "1",
            "replacement_quantity": "1",
            "returned_disposition": "RESTOCK",
            "settlement_method": "",
            "reason": "Même produit",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Choisissez deux boissons différentes" in response.text
    assert _quantity(bar.id, product.id) == before_stock
    assert cash_service.expected(session) == Decimal("1000")
    assert db.session.scalar(
        select(AuditLog.id).where(
            AuditLog.bar_id == bar.id,
            AuditLog.action == "exchanges.record",
        )
    ) is None


def test_cashier_exchange_keeps_products_strictly_tenant_scoped(env):
    app, owner, bar, foreign, returned_product, _, _, _ = env
    replacement = _replacement(owner, bar, returned_product)
    cashier, _ = _cashier(owner, bar)
    cash_service.open(cashier, bar.id, "EXCHANGE-TENANT", 1000)
    db.session.commit()

    foreign_category = ProductCategory(bar_id=foreign.id, name="Foreign drinks")
    db.session.add(foreign_category)
    db.session.flush()
    foreign_product = Product(
        bar_id=foreign.id,
        category_id=foreign_category.id,
        name="Foreign Beer",
        sku="FOREIGN-BEER",
        base_unit="bottle",
        sale_price=200,
        valuation_unit_cost=80,
        is_active=True,
    )
    db.session.add(foreign_product)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier-exchanges")
    assert page.status_code == 200
    assert "Foreign Beer" not in page.text

    before_returned = _quantity(bar.id, returned_product.id)
    before_replacement = _quantity(bar.id, replacement.id)
    response = client.post(
        f"/bars/{bar.id}/cashier-exchanges",
        data={
            "csrf_token": _csrf(page),
            "returned_product_id": str(returned_product.id),
            "replacement_product_id": str(foreign_product.id),
            "returned_quantity": "1",
            "replacement_quantity": "1",
            "returned_disposition": "RESTOCK",
            "settlement_method": "CASH",
            "reason": "Tentative autre bar",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Produit introuvable ou indisponible" in response.text
    assert _quantity(bar.id, returned_product.id) == before_returned
    assert _quantity(bar.id, replacement.id) == before_replacement
