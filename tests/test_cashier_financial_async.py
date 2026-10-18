"""Regression coverage for cookie-authenticated, atomic cashier financial AJAX."""
from decimal import Decimal

from sqlalchemy import func, select

from app.cash_services import cash_service
from app.extensions import db
from app.models import (
    AuditLog, CashHandover, CashMovement, IdempotencyRecord, OrderLine,
    OrderReturn, Payment, Refund, StaffAssignment, StaffCashLedger, StockBalance,
)
from app.payment_services import payment_service
from test_cashier_exchanges import _cashier, _csrf, _login, _replacement
from test_workflows import env, order

AJAX = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "X-BarPro-Async": "1",
}


def _post(client, path, data, csrf):
    return client.post(path, data={"csrf_token": csrf, **data}, headers=AJAX)


def _count(model, **filters):
    return db.session.scalar(select(func.count()).select_from(model).filter_by(**filters))


def _stock(bar, product):
    return Decimal(db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id,
    )) or 0)


def test_async_returns_refunds_duplicate_keys_and_balances(env):
    app, owner, bar, _, product, *_ = env
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(owner, bar.id, "RET-ASYNC", 1000)
    value = order(env, served=True)
    payment = payment_service.record(owner, bar.id, value.id, "PAID-RET", "CASH", 200, 200, cash_session_id=session.id)
    db.session.commit()
    line = db.session.scalar(select(OrderLine).where(OrderLine.order_id == value.id))

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/cashier-returns"
    page = client.get(path + f"?order_id={value.id}")
    assert page.status_code == 200
    assert 'data-bp-financial="returns"' in page.text
    assert 'name="idempotency_key"' in page.text
    original_stock = _stock(bar, product)
    data = {
        "action": "return_create", "order_id": str(value.id),
        "order_line_id": str(line.id), "quantity": "1", "disposition": "RESTOCK",
        "reason": "Bouteille intacte", "idempotency_key": "return-create-unique-01",
    }
    result = _post(client, path, data, _csrf(page))
    assert result.status_code == 200, result.text
    assert result.json["success"] is True
    returned_id = result.json["data"]["return_id"]
    assert _stock(bar, product) == original_stock + 1
    assert _count(OrderReturn, bar_id=bar.id) == 1
    assert _post(client, path, data, _csrf(page)).status_code == 409
    assert _post(client, path, {**data, "quantity": "2"}, _csrf(page)).status_code == 409
    assert _stock(bar, product) == original_stock + 1

    balance_page = client.get(path + f"?order_id={value.id}&return_id={returned_id}")
    assert balance_page.status_code == 200
    assert "Déjà retourné" in balance_page.text
    assert "Rembourser le client" in balance_page.text
    refund = {
        "action": "refund_return", "order_id": str(value.id),
        "return_id": str(returned_id), "payment_id": str(payment.id),
        "amount": "100", "reason": "Retour confirmé",
        "idempotency_key": "return-refund-unique-01",
    }
    response = _post(client, path, refund, _csrf(balance_page))
    assert response.status_code == 200, response.text
    assert response.json["data"]["refund_id"]
    assert _post(client, path, refund, _csrf(balance_page)).status_code == 409
    assert _count(Refund, bar_id=bar.id) == 1
    assert cash_service.expected(session) == 1100  # initial cash 1000 + payment 200 - refund 100
    assert _count(IdempotencyRecord, bar_id=bar.id, status="COMPLETED") >= 2
    assert client.get(path + f"?order_id={value.id}").status_code == 200


def test_exchange_async_atomic_stock_cash_and_replay(env):
    app, owner, bar, _, returned, *_ = env
    replacement = _replacement(owner, bar, returned)
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(owner, bar.id, "ECH-ASYNC", 1000)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/cashier-exchanges"
    page = client.get(path)
    assert page.status_code == 200
    assert 'data-bp-financial="exchanges"' in page.text
    before = (_stock(bar, returned), _stock(bar, replacement), cash_service.expected(session))
    data = {
        "action": "exchange", "returned_product_id": str(returned.id),
        "replacement_product_id": str(replacement.id), "returned_quantity": "1",
        "replacement_quantity": "1", "returned_disposition": "RESTOCK",
        "settlement_method": "CASH", "reason": "Client remplace",
        "idempotency_key": "exchange-intent-unique-01",
    }
    response = _post(client, path, data, _csrf(page))
    assert response.status_code == 200, response.text
    assert response.json["data"]["settlement_direction"] == "COLLECT"
    assert _post(client, path, data, _csrf(page)).status_code == 409
    assert _stock(bar, returned) == before[0] + 1
    assert _stock(bar, replacement) == before[1] - 1
    assert cash_service.expected(session) == before[2] + 50
    assert _count(AuditLog, bar_id=bar.id, action="exchanges.record") == 1

    failed = {**data, "idempotency_key": "exchange-failed-stock-01", "replacement_quantity": "999"}
    assert _post(client, path, failed, _csrf(page)).status_code == 409
    assert _stock(bar, returned) == before[0] + 1  # full rollback, including RESTOCK
    assert _stock(bar, replacement) == before[1] - 1
    assert cash_service.expected(session) == before[2] + 50
    assert _count(AuditLog, bar_id=bar.id, action="exchanges.record") == 1


def test_direct_refund_cash_failure_rolls_back_stock_and_intent(env):
    app, owner, bar, _, product, *_ = env
    cashier, _ = _cashier(owner, bar)
    cash_service.open(owner, bar.id, "EMPTY-REF", 0)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/cashier-exchanges"
    page = client.get(path + "?mode=refund")
    before = _stock(bar, product)
    data = {
        "action": "refund", "refund_product_id": str(product.id),
        "refund_quantity": "1", "refund_disposition": "RESTOCK",
        "refund_method": "CASH", "refund_reason": "Retour intact",
        "idempotency_key": "refund-cash-insufficient-01",
    }
    response = _post(client, path, data, _csrf(page))
    assert response.status_code == 409, response.text
    assert response.json["success"] is False
    assert _stock(bar, product) == before
    assert _count(AuditLog, bar_id=bar.id, action="exchanges.refund") == 0
    assert _count(IdempotencyRecord, bar_id=bar.id, operation="web.cashier.financial.exchange_refund") == 0


def test_handovers_async_double_confirm_cancel_and_stale_custody(env):
    app, owner, bar, _, product, server, *_ = env
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(owner, bar.id, "HANDO-ASYNC", 100)
    sold = order(env)
    assignment = db.session.scalar(select(StaffAssignment).where(
        StaffAssignment.bar_id == bar.id, StaffAssignment.user_id == server.id))
    payment_service.record(owner, bar.id, sold.id, "CUSTODY-100", "CASH", 200, 200, staff_assignment_id=assignment.id)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/cashier-handovers"
    page = client.get(path)
    assert page.status_code == 200
    assert 'data-bp-financial="handovers"' in page.text
    make = {
        "action": "create", "staff_assignment_id": str(assignment.id),
        "amount": "150", "idempotency_key": "handover-create-unique-01",
    }
    r = _post(client, path, make, _csrf(page))
    assert r.status_code == 200, r.text
    hid = r.json["data"]["handover_id"]
    assert _post(client, path, make, _csrf(page)).status_code == 409
    assert _count(CashHandover, bar_id=bar.id) == 1
    assert cash_service.custody(bar.id, assignment.id) == 200
    confirm = {"action": "post", "handover_id": str(hid), "idempotency_key": "handover-post-unique-01"}
    r = _post(client, path, confirm, _csrf(page))
    assert r.status_code == 200, r.text
    assert r.json["data"]["status"] == "POSTED"
    assert _post(client, path, confirm, _csrf(page)).status_code == 409
    assert _post(client, path, {"action": "cancel", "handover_id": str(hid),
                                "idempotency_key": "handover-cancel-too-late"}, _csrf(page)).status_code == 409
    assert cash_service.expected(session) == 250
    assert cash_service.custody(bar.id, assignment.id) == 50
    assert _count(CashMovement, bar_id=bar.id, cash_handover_id=hid) == 1
    assert _count(StaffCashLedger, bar_id=bar.id, cash_handover_id=hid) == 1

    page = client.get(path)
    second = _post(client, path, {"action": "create", "staff_assignment_id": str(assignment.id),
                                 "amount": "50", "idempotency_key": "handover-create-second"}, _csrf(page))
    assert second.status_code == 200, second.text
    hid2 = second.json["data"]["handover_id"]
    cancel = _post(client, path, {"action": "cancel", "handover_id": str(hid2),
                                 "idempotency_key": "handover-cancel-second"}, _csrf(page))
    assert cancel.status_code == 200, cancel.text
    assert _post(client, path, {"action": "post", "handover_id": str(hid2),
                                "idempotency_key": "handover-post-cancelled"}, _csrf(page)).status_code == 409
    assert cash_service.custody(bar.id, assignment.id) == 50


def test_async_csrf_logout_errors_and_legacy_html_fallback(env):
    app, owner, bar, _, product, *_ = env
    cashier, _ = _cashier(owner, bar)
    cash_service.open(owner, bar.id, "AUTH-ASYNC", 1000)
    db.session.commit()
    client = app.test_client()
    _login(client, cashier.email)
    path = f"/bars/{bar.id}/cashier-exchanges"
    page = client.get(path)
    req = {
        "action": "refund", "refund_product_id": str(product.id),
        "refund_quantity": "1", "refund_disposition": "LOSS",
        "refund_method": "CASH", "refund_reason": "Erreur client",
        "idempotency_key": "auth-protection-case-01",
    }
    assert client.post(path, data=req, headers=AJAX).status_code == 400
    missing_key = {k: v for k, v in req.items() if k != "idempotency_key"}
    assert _post(client, path, missing_key, _csrf(page)).status_code == 422
    html = client.post(path, data={"csrf_token": _csrf(page), **missing_key}, follow_redirects=False)
    assert html.status_code == 302
    assert html.headers["Location"].endswith("?mode=refund")
    # Logout/expired cookie must not process a financial request.
    unauth = app.test_client()
    response = _post(unauth, path, req, _csrf(page))
    assert response.status_code in (302, 400, 401, 403)  # CSRF can reject an unauthenticated request first
    assert response.status_code != 200
