"""Regression tests for differential invoice/unpaid snapshots and financial safety."""
from decimal import Decimal

from sqlalchemy import func, select

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Order, OrderLine, Payment
from app.order_services import order_service
from app.payment_services import payment_service
from test_cashier_workspace_async import _async_headers, _cashier, _csrf, _login
from test_workflows import env


def _ready_cashier(env, *, count=1):
    app, owner, bar, _, product, _, _, _ = env
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "ASYNC-INVOICE-SESSION", 1000)
    orders = []
    for index in range(count):
        order = order_service.create(
            owner, bar.id, f"ASYNC-INVOICE-{index}",
            [{"product_id": product.id, "quantity": 1}],
        )
        order_service.confirm(cashier, bar.id, order.id)
        orders.append(order)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    return client, owner, bar, cashier, orders


def test_invoice_filter_snapshots_are_conditional_and_tenant_scoped(env):
    client, _, bar, _, orders = _ready_cashier(env)
    base = f"/bars/{bar.id}/cashier/invoices"
    url = base + "?snapshot=1&delivery=delivered&payment=unpaid"
    first = client.get(url)
    assert first.status_code == 200
    assert first.is_json
    assert first.headers["Cache-Control"].find("no-store") >= 0
    assert str(orders[0].id) in first.get_json()["html"]
    assert first.get_json()["stats"]["visible"] == 1
    assert "invoice-shell" not in first.get_json()["html"]
    assert client.get(url, headers={"If-None-Match": first.headers["ETag"]}).status_code == 304

    paid_filter = client.get(base + "?snapshot=1&payment=paid")
    assert paid_filter.status_code == 200
    assert 'data-invoice-id="' not in paid_filter.get_json()["html"]


def test_invoice_async_cash_payment_is_atomic_and_replay_returns_conflict(env):
    client, _, bar, _, orders = _ready_cashier(env)
    order = orders[0]
    url = f"/bars/{bar.id}/cashier/invoices"
    token = _csrf(client.get(url))
    amount = Decimal(order_balance(order)["amount_due"])
    assert amount > 0
    payload = {
        "csrf_token": token,
        "action": "pay_cash",
        "order_id": str(order.id),
        "idempotency_key": "ASYNC-INVOICE-INTENT-001",
        "delivery": "all",
        "payment": "open",
        "origin": "all",
    }
    response = client.post(url, data=payload, headers=_async_headers(token), follow_redirects=False)
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert response.get_json()["data"]["order_id"] == order.id
    assert response.get_json()["data"]["refresh_url"].startswith(url)

    duplicate = client.post(url, data=payload, headers=_async_headers(token), follow_redirects=False)
    assert duplicate.status_code == 409
    assert duplicate.get_json()["success"] is False
    assert db.session.scalar(select(func.count(Payment.id)).where(Payment.order_id == order.id)) == 1
    db.session.refresh(order)
    assert order.payment_status == "PAID"
    assert Decimal(order_balance(order)["amount_due"]) == 0

    snapshot = client.get(url + "?snapshot=1&payment=open").get_json()
    assert f'data-invoice-id="{order.id}"' not in snapshot["html"]
    assert snapshot["stats"]["paid"] == 1


def test_invoice_async_payment_requires_idempotency_key(env):
    client, _, bar, _, orders = _ready_cashier(env)
    url = f"/bars/{bar.id}/cashier/invoices"
    token = _csrf(client.get(url))
    response = client.post(url, data={
        "csrf_token": token, "action": "pay_cash", "order_id": str(orders[0].id),
    }, headers=_async_headers(token))
    assert response.status_code == 422
    assert db.session.scalar(select(func.count(Payment.id)).where(Payment.order_id == orders[0].id)) == 0


def test_unpaid_etag_changes_on_partial_payment_and_order_removal(env):
    client, owner, bar, _, orders = _ready_cashier(env)
    order = orders[0]
    url = f"/bars/{bar.id}/unpaid-orders/data"
    first = client.get(url)
    assert first.status_code == 200
    first_order = first.get_json()["orders"][0]
    assert first_order["signature"]
    assert first_order["payment_status"] == "UNPAID"
    assert client.get(url, headers={"If-None-Match": first.headers["ETag"]}).status_code == 304

    due = Decimal(order_balance(order)["amount_due"])
    payment_service.record(owner, bar.id, order.id, "INVOICE-PARTIAL-001", "CARD", due / 2, due / 2)
    db.session.commit()
    second = client.get(url, headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 200
    updated = second.get_json()["orders"][0]
    assert updated["signature"] != first_order["signature"]
    assert updated["payment_status"] == "PARTIAL"
    assert Decimal(updated["amount_due"]) == due / 2

    payment_service.record(owner, bar.id, order.id, "INVOICE-FINAL-001", "CARD", due / 2, due / 2)
    db.session.commit()
    final = client.get(url, headers={"If-None-Match": second.headers["ETag"]})
    assert final.status_code == 200
    assert final.get_json()["orders"] == []
    assert final.get_json()["stats"]["count"] == 0


def test_invoice_snapshot_reflects_new_order_and_filter_changes(env):
    client, owner, bar, _, orders = _ready_cashier(env)
    base = f"/bars/{bar.id}/cashier/invoices"
    initial = client.get(base + "?snapshot=1&delivery=waiting&payment=all")
    assert initial.status_code == 200
    assert initial.get_json()["stats"]["visible"] == 0

    product_id = db.session.scalar(
        select(OrderLine.product_id).where(OrderLine.order_id == orders[0].id)
    )
    new_order = order_service.create(
        owner, bar.id, "INVOICE-NEW-WAITING",
        [{"product_id": product_id, "quantity": 1}],
    )
    db.session.commit()
    changed = client.get(
        base + "?snapshot=1&delivery=waiting&payment=all",
        headers={"If-None-Match": initial.headers["ETag"]},
    )
    assert changed.status_code == 200
    assert f'data-invoice-id="{new_order.id}"' in changed.get_json()["html"]
    assert changed.get_json()["stats"]["visible"] == 1

    delivered = client.get(base + "?snapshot=1&delivery=delivered&payment=all")
    assert delivered.status_code == 200
    assert f'data-invoice-id="{new_order.id}"' not in delivered.get_json()["html"]


def test_server_cannot_access_cashier_invoice_snapshot(env):
    app, _, bar, _, _, server, _, _ = env
    client = app.test_client()
    assert _login(client, server.email).status_code == 302
    response = client.get(f"/bars/{bar.id}/cashier/invoices?snapshot=1")
    assert response.status_code in (403, 404)
