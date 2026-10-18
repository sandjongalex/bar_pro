import re

from sqlalchemy import func, select

from app.cash_services import cash_service
from app.extensions import db
from app.models import Order, Payment, StaffAssignment, User, utcnow
from app.order_services import order_service
from app.shift_service import start_shift
from test_workflows import env


def _csrf(page):
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def _cashier(bar):
    user = User(email="workspace-async-cashier@example.invalid", display_name="Caisse Async", category="EMPLOYEE")
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
    owner = db.session.get(User, bar.owner_id)
    start_shift(owner, bar.id, assignment.id)
    db.session.commit()
    return user


def _async_headers(token):
    return {
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "X-BarPro-Async": "1",
        "X-CSRFToken": token,
    }


def test_counter_sale_async_returns_refresh_target_and_same_reference_prevents_duplicate(env):
    app, _, bar, _, product, _, _, _ = env
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "ASYNC-SALE-CASH", 5000)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    url = f"/bars/{bar.id}/cashier/workspace"
    page = client.get(url + "?sale=1")
    token = _csrf(page)
    reference = "CMD-ASYNC-COUNTER-001"
    data = {
        "csrf_token": token,
        "action": "create_sale",
        "reference": reference,
        "product_id": str(product.id),
        "quantity": "1",
        "invoice_name": "Client comptoir",
        "notes": "Commande asynchrone",
    }

    response = client.post(url, data=data, headers=_async_headers(token), follow_redirects=False)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["data"]["reset_cart"] is True
    assert payload["data"]["close_sale_panel"] is True
    assert "order_id=" in payload["data"]["refresh_url"]
    assert "focus=payment" in payload["data"]["refresh_url"]

    order = db.session.scalar(select(Order).where(Order.bar_id == bar.id, Order.reference == reference))
    assert order is not None
    assert order.status == "CONFIRMED"
    assert order.payment_status == "UNPAID"
    assert order.customer_name_snapshot == "Client comptoir"

    duplicate = client.post(url, data=data, headers=_async_headers(token), follow_redirects=False)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["success"] is False
    assert db.session.scalar(
        select(func.count(Order.id)).where(Order.bar_id == bar.id, Order.reference == reference)
    ) == 1


def test_delivery_async_returns_json_without_redirect_and_keeps_order_transition(env):
    app, owner, bar, _, product, _, _, _ = env
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "ASYNC-DELIVERY-CASH", 0)
    order = order_service.create(owner, bar.id, "ASYNC-DELIVERY-001", [{"product_id": product.id, "quantity": 1}])
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    url = f"/bars/{bar.id}/cashier/workspace"
    page = client.get(url)
    token = _csrf(page)
    response = client.post(
        url,
        data={"csrf_token": token, "action": "deliver", "order_id": str(order.id)},
        headers=_async_headers(token),
        follow_redirects=False,
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert f"order_id={order.id}" in payload["data"]["refresh_url"]
    assert "focus=payment" in payload["data"]["refresh_url"]
    db.session.refresh(order)
    assert order.status == "CONFIRMED"
    assert order.payment_status == "UNPAID"


def test_partial_cash_payment_async_uses_stable_reference_and_prevents_duplicate(env):
    app, owner, bar, _, product, _, _, _ = env
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "ASYNC-PARTIAL-CASH", 1000)
    order = order_service.create(owner, bar.id, "ASYNC-PARTIAL-ORDER", [{"product_id": product.id, "quantity": 1}])
    order_service.confirm(cashier, bar.id, order.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    url = f"/bars/{bar.id}/cashier/workspace"
    page = client.get(f"{url}?order_id={order.id}")
    token = _csrf(page)
    reference = "PAY-ASYNC-PARTIAL-001"
    data = {
        "csrf_token": token,
        "action": "payment",
        "order_id": str(order.id),
        "method": "CASH",
        "amount_applied": "40",
        "amount_presented": "40",
        "cash_holder_mode": "DRAWER",
        "reference": reference,
    }

    response = client.post(url, data=data, headers=_async_headers(token), follow_redirects=False)

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert f"order_id={order.id}" in payload["data"]["refresh_url"]
    assert "focus=payment" in payload["data"]["refresh_url"]
    payment = db.session.scalar(select(Payment).where(Payment.bar_id == bar.id, Payment.reference == reference))
    assert payment is not None
    db.session.refresh(order)
    assert order.payment_status == "PARTIAL"
    assert cash_service.expected(session) == 1040

    duplicate = client.post(url, data=data, headers=_async_headers(token), follow_redirects=False)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["success"] is False
    assert db.session.scalar(
        select(func.count(Payment.id)).where(Payment.bar_id == bar.id, Payment.reference == reference)
    ) == 1
    db.session.refresh(order)
    assert order.payment_status == "PARTIAL"
    assert cash_service.expected(session) == 1040
