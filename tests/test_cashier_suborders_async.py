import re

from sqlalchemy import select

from legacy_shift import activate_employee
from app.cash_services import cash_service
from app.extensions import db
from app.models import CashSession, Order, Product, StaffAssignment, StockBalance, User, utcnow
from app.order_services import order_service
from app.order_suborder_models import OrderSuborder
from app.order_suborder_service import order_suborder_service
from test_workflows import env


def _csrf(page):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    assert page.status_code == 200
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def _async_headers(token):
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-BarPro-Async": "1",
        "X-CSRFToken": token,
    }


def _cashier(bar):
    cashier = User(
        email="cashier-suborders-async@example.invalid",
        display_name="Caisse sous-commandes async",
        category="EMPLOYEE",
    )
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
    db.session.flush()
    activate_employee(bar, cashier)
    db.session.commit()
    return cashier


def test_cashier_delivers_suborder_async_and_duplicate_does_not_move_stock_twice(env):
    app, _, bar, _, product, server, _, _ = env
    activate_employee(bar, server)
    cashier = _cashier(bar)
    cash_session = cash_service.open(cashier, bar.id, "ASYNC-SUBORDER-CASH", 1000)
    parent = order_service.create(
        server,
        bar.id,
        "ASYNC-SUBORDER-PARENT",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, parent.id)
    db.session.commit()

    suborder = order_suborder_service.create_server_addition(
        server,
        bar.id,
        parent.id,
        [{"product_id": product.id, "quantity": 1}],
        note="Ajout à livrer",
    )
    db.session.commit()
    assert suborder.status == "VALIDATED"
    assert suborder.delivery_status == "PENDING"

    stock_before = db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    )

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    url = f"/bars/{bar.id}/cashier/suborders"
    page = client.get(url)
    assert page.status_code == 200
    assert 'data-bp-ajax="json" data-suborder-async="1"' in page.text
    assert f'data-suborder-id="{suborder.id}"' in page.text
    token = _csrf(page)
    data = {
        "csrf_token": token,
        "order_id": str(parent.id),
        "suborder_id": str(suborder.id),
    }

    response = client.post(url, data=data, headers=_async_headers(token), follow_redirects=False)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["refresh_url"] == url
    assert payload["data"]["suborder_id"] == suborder.id

    db.session.refresh(suborder)
    assert suborder.delivery_status == "DELIVERED"
    assert suborder.delivered_by_id == cashier.id
    stock_after_delivery = db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    )
    assert stock_after_delivery == stock_before - 1

    duplicate = client.post(url, data=data, headers=_async_headers(token), follow_redirects=False)
    assert duplicate.status_code == 422
    assert duplicate.get_json()["success"] is False
    db.session.refresh(suborder)
    assert suborder.delivery_status == "DELIVERED"
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    ) == stock_after_delivery
    assert db.session.scalar(
        select(CashSession).where(CashSession.id == cash_session.id, CashSession.status == "OPEN")
    ) is not None
