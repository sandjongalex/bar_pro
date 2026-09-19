import re

from app.extensions import db
from app.order_services import order_service
from app.payment_services import payment_service

from test_workflows import env


def _login(client, email, password="test-password"):
    page = client.get("/login")
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": match.group(1)},
        follow_redirects=False,
    )


def _create_order(owner, bar, product, reference):
    return order_service.create(
        owner,
        bar.id,
        reference,
        [{"product_id": product.id, "quantity": 1}],
    )


def test_checkout_groups_waiting_payable_and_paid_orders(env):
    app, owner, bar, _, product, _, _, _ = env

    waiting = _create_order(owner, bar, product, "QUEUE-WAIT")
    payable = _create_order(owner, bar, product, "QUEUE-PAY")
    order_service.confirm(owner, bar.id, payable.id)
    paid = _create_order(owner, bar, product, "QUEUE-PAID")
    order_service.confirm(owner, bar.id, paid.id)
    payment_service.record(owner, bar.id, paid.id, "QUEUE-PAYMENT", "CARD", 100, 100)
    db.session.commit()

    client = app.test_client()
    assert _login(client, owner.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/checkout?order_id={paid.id}")

    assert page.status_code == 200
    assert "À livrer → À payer → Payée" in page.text
    assert "QUEUE-WAIT" in page.text
    assert "QUEUE-PAY" in page.text
    assert "QUEUE-PAID" in page.text
    assert "Payées récemment" in page.text
    assert "Commande soldée" in page.text
    assert 'id="checkoutForm"' not in page.text


def test_partial_payment_stays_in_payable_stage(env):
    app, owner, bar, _, product, _, _, _ = env

    order = _create_order(owner, bar, product, "QUEUE-PARTIAL")
    order_service.confirm(owner, bar.id, order.id)
    payment_service.record(owner, bar.id, order.id, "QUEUE-PARTIAL-PAY", "CARD", 40, 40)
    db.session.commit()

    client = app.test_client()
    _login(client, owner.email)
    page = client.get(f"/bars/{bar.id}/checkout?order_id={order.id}")

    assert page.status_code == 200
    assert "QUEUE-PARTIAL" in page.text
    assert "Partiel" in page.text
    assert "60 XAF" in page.text
    assert 'id="checkoutForm"' in page.text
