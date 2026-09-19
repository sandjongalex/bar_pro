import re

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import Order, Payment, StaffAssignment, User, utcnow
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


def _cashier(bar):
    user = User(email="workspace-cashier@example.invalid", display_name="Caisse Rapide", category="EMPLOYEE")
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=user.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.commit()
    return user


def test_cashier_workspace_counter_sale_and_exact_cash(env):
    app, _, bar, _, product, _, _, _ = env
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "WORKSPACE-CASH", 5000)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    old_checkout = client.get(f"/bars/{bar.id}/checkout", follow_redirects=False)
    assert old_checkout.status_code == 302
    assert f"/bars/{bar.id}/cashier/workspace" in old_checkout.headers["Location"]

    old_order_page = client.get(f"/bars/{bar.id}/orders/new", follow_redirects=False)
    assert old_order_page.status_code == 302
    assert "cashier/workspace" in old_order_page.headers["Location"]
    assert "sale=1" in old_order_page.headers["Location"]

    workspace = client.get(f"/bars/{bar.id}/cashier/workspace?sale=1")
    assert workspace.status_code == 200
    assert "Poste de caisse" in workspace.text
    assert "Valider &amp; encaisser" in workspace.text or "Valider & encaisser" in workspace.text
    assert "cashier_live_ui.js" in workspace.text

    response = client.post(
        f"/bars/{bar.id}/cashier/workspace",
        data={
            "csrf_token": _csrf(workspace),
            "action": "create_sale",
            "product_id": str(product.id),
            "quantity": "1",
            "notes": "Client comptoir",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    order = db.session.scalar(
        select(Order)
        .where(Order.bar_id == bar.id, Order.created_by_id == cashier.id)
        .order_by(Order.id.desc())
    )
    assert order is not None
    assert order.status == "CONFIRMED"
    assert order.payment_status == "UNPAID"
    assert order.assigned_staff_id is None

    live = client.get(f"/bars/{bar.id}/live/orders")
    assert live.status_code == 200
    payload = live.get_json()
    assert payload["mode"] == "CASHIER"
    assert any(item["id"] == order.id and item["state"] == "to_pay" for item in payload["orders"])
    assert str(product.id) in payload["stock"]

    live_script = client.get("/static/cashier_live_ui.js")
    assert live_script.status_code == 200
    assert "setInterval(poll, 4000)" in live_script.text
    assert "Nouvelle commande reçue" in live_script.text

    pay_page = client.get(response.headers["Location"])
    assert pay_page.status_code == 200
    assert order.reference in pay_page.text
    assert "Paiement espèces exact" in pay_page.text
    assert "Encaisser 100 XAF" in pay_page.text

    paid = client.post(
        f"/bars/{bar.id}/cashier/workspace",
        data={
            "csrf_token": _csrf(pay_page),
            "action": "cash_exact",
            "order_id": str(order.id),
        },
        follow_redirects=False,
    )
    assert paid.status_code == 302
    assert "order_id" not in paid.headers["Location"]

    db.session.refresh(order)
    assert order.payment_status == "PAID"
    payment = db.session.scalar(select(Payment).where(Payment.order_id == order.id))
    assert payment is not None
    assert payment.method == "CASH"
    assert payment.cash_session_id == session.id
    assert cash_service.expected(session) == 5100
