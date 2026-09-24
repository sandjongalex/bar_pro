import re
from decimal import Decimal

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import AuditLog, StaffAssignment, StockBalance, User, utcnow
from app.shift_service import start_shift
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


def _cashier(owner, bar, suffix="refund"):
    user = User(
        email=f"{suffix}-cashier@example.invalid",
        display_name="Caissière Remboursement",
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
    return user


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


def test_exchange_workspace_shows_sold_beer_refund_mode(env):
    app, owner, bar, _, product, *_ = env
    cashier = _cashier(owner, bar, "ui-refund")
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/cashier-exchanges")
    assert page.status_code == 200
    assert "Rembourser une bière vendue" in page.text
    assert 'name="action" value="refund"' in page.text
    assert product.name in page.text


def test_cashier_can_refund_sold_beer_in_cash_and_restock(env):
    app, owner, bar, _, product, *_ = env
    cashier = _cashier(owner, bar, "cash-refund")
    session = cash_service.open(cashier, bar.id, "REFUND-CASH", 10000)
    db.session.commit()
    before_stock = _quantity(bar.id, product.id)
    expected_refund = Decimal(product.sale_price) * Decimal("2")

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier-exchanges?mode=refund")
    response = client.post(
        f"/bars/{bar.id}/cashier-exchanges",
        data={
            "csrf_token": _csrf(page),
            "action": "refund",
            "refund_product_id": str(product.id),
            "refund_quantity": "2",
            "refund_disposition": "RESTOCK",
            "refund_method": "CASH",
            "refund_provider_transaction_id": "",
            "refund_reason": "Deux bouteilles rendues",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Remboursement" in response.text
    assert "remboursés en Espèces" in response.text
    assert _quantity(bar.id, product.id) == before_stock + Decimal("2")
    assert cash_service.expected(session) == Decimal("10000") - expected_refund

    audit = db.session.scalar(
        select(AuditLog)
        .where(AuditLog.bar_id == bar.id, AuditLog.action == "exchanges.refund")
        .order_by(AuditLog.id.desc())
    )
    assert audit is not None
    assert audit.changes["returned_product_id"] == product.id
    assert audit.changes["returned_disposition"] == "RESTOCK"
    assert Decimal(audit.changes["settlement_amount"]) == expected_refund
    assert audit.changes["settlement_method"] == "CASH"


def test_non_resellable_refund_does_not_increase_stock(env):
    app, owner, bar, _, product, *_ = env
    cashier = _cashier(owner, bar, "loss-refund")
    session = cash_service.open(cashier, bar.id, "REFUND-LOSS", 10000)
    db.session.commit()
    before_stock = _quantity(bar.id, product.id)
    expected_refund = Decimal(product.sale_price)

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier-exchanges?mode=refund")
    response = client.post(
        f"/bars/{bar.id}/cashier-exchanges",
        data={
            "csrf_token": _csrf(page),
            "action": "refund",
            "refund_product_id": str(product.id),
            "refund_quantity": "1",
            "refund_disposition": "LOSS",
            "refund_method": "CASH",
            "refund_provider_transaction_id": "",
            "refund_reason": "Bouteille ouverte non revendable",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert _quantity(bar.id, product.id) == before_stock
    assert cash_service.expected(session) == Decimal("10000") - expected_refund


def test_insufficient_cash_rolls_back_refund_and_stock(env):
    app, owner, bar, _, product, *_ = env
    cashier = _cashier(owner, bar, "rollback-refund")
    session = cash_service.open(cashier, bar.id, "REFUND-EMPTY", 0)
    db.session.commit()
    before_stock = _quantity(bar.id, product.id)

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier-exchanges?mode=refund")
    response = client.post(
        f"/bars/{bar.id}/cashier-exchanges",
        data={
            "csrf_token": _csrf(page),
            "action": "refund",
            "refund_product_id": str(product.id),
            "refund_quantity": "1",
            "refund_disposition": "RESTOCK",
            "refund_method": "CASH",
            "refund_provider_transaction_id": "",
            "refund_reason": "Test caisse vide",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "pas assez d'espèces" in response.text
    assert _quantity(bar.id, product.id) == before_stock
    db.session.refresh(session)
    assert cash_service.expected(session) == Decimal("0")
    assert db.session.scalar(
        select(AuditLog.id).where(
            AuditLog.bar_id == bar.id,
            AuditLog.action == "exchanges.refund",
        )
    ) is None
