"""Cashier Mobile Money: operator selection without an invented operator transaction ID."""
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.extensions import db
from app.models import CashMovement, Order, Payment
from test_cashier_inline_tender import _cashier_client
from test_cashier_workspace_async import _async_headers
from test_workflows import env


def _unpaid_counter_order(env, reference):
    app, _, bar, _, product, *_ = env
    client, session, csrf = _cashier_client(env)
    url = f"/bars/{bar.id}/cashier/workspace"
    create = client.post(url, data={
        "csrf_token": csrf, "action": "create_sale", "reference": reference,
        "product_id": str(product.id), "quantity": "2",
    }, headers=_async_headers(csrf))
    assert create.status_code == 200, create.text
    order = db.session.scalar(
        select(Order).where(Order.bar_id == bar.id, Order.reference == reference)
    )
    assert order is not None and order.payment_status == "UNPAID"
    return client, session, csrf, bar, order, url


def test_mobile_money_mtn_selection_without_operator_id_is_recorded_once(env):
    client, session, csrf, bar, order, url = _unpaid_counter_order(
        env, "MANUAL-MOMO-MTN-001"
    )
    page = client.get(f"{url}?order_id={order.id}")
    assert page.status_code == 200
    assert 'id="cashierMobileNetwork"' in page.text
    assert '<option value="MTN">MTN Mobile Money</option>' in page.text
    assert '<option value="ORANGE">Orange Money</option>' in page.text
    assert 'name="provider_transaction_id"' not in page.text

    due = Decimal(order.total_amount)
    payload = {
        "csrf_token": csrf, "action": "payment", "order_id": str(order.id),
        "method": "MOBILE_MONEY", "amount_applied": str(due),
        "provider_code": "MTN", "reference": "PAY-MANUAL-MOMO-MTN-001",
    }
    response = client.post(url, data=payload, headers=_async_headers(csrf))
    assert response.status_code == 200, response.text
    payment = db.session.scalar(select(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    ))
    assert payment is not None
    assert payment.provider_code == "MTN"
    assert payment.provider_transaction_id is None
    assert payment.reference == payload["reference"]
    assert payment.method == "MOBILE_MONEY"
    assert db.session.get(Order, order.id).payment_status == "PAID"
    assert db.session.scalar(select(func.count()).select_from(CashMovement).where(
        CashMovement.bar_id == bar.id, CashMovement.payment_id == payment.id,
    )) == 0

    duplicate = client.post(url, data=payload, headers=_async_headers(csrf))
    assert duplicate.status_code == 409
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id,
    )) == 1


def test_mixed_cash_and_orange_without_operator_transaction_id(env):
    client, session, csrf, bar, order, url = _unpaid_counter_order(
        env, "MANUAL-MOMO-ORANGE-MIXED"
    )
    due = Decimal(order.total_amount)
    cash_part = Decimal("50")
    mobile_part = due - cash_part
    response = client.post(url, data={
        "csrf_token": csrf, "action": "payment", "order_id": str(order.id),
        "method": "MIXED", "mixed_cash": str(cash_part),
        "mixed_mobile": str(mobile_part), "amount_presented": str(cash_part),
        "provider_code": "ORANGE", "reference": "PAY-MANUAL-ORANGE-MIXED",
    }, headers=_async_headers(csrf))
    assert response.status_code == 200, response.text
    payments = list(db.session.scalars(select(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    )))
    assert len(payments) == 2
    mobile = next(p for p in payments if p.method == "MOBILE_MONEY")
    cash = next(p for p in payments if p.method == "CASH")
    assert mobile.provider_code == "ORANGE"
    assert mobile.provider_transaction_id is None
    assert cash.provider_code is None
    assert Decimal(mobile.amount_applied) + Decimal(cash.amount_applied) == due
    assert db.session.get(Order, order.id).payment_status == "PAID"


def test_invalid_operator_rejected_without_charging(env):
    client, _, csrf, bar, order, url = _unpaid_counter_order(
        env, "MANUAL-MOMO-INVALID-OPERATOR"
    )
    response = client.post(url, data={
        "csrf_token": csrf, "action": "payment", "order_id": str(order.id),
        "method": "MOBILE_MONEY", "amount_applied": str(order.total_amount),
        "provider_code": "OTHER", "reference": "PAY-INVALID-NETWORK",
    }, headers=_async_headers(csrf))
    assert response.status_code == 422
    assert db.session.get(Order, order.id).payment_status == "UNPAID"
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id,
    )) == 0


def test_secondary_checkout_network_has_no_external_id_field():
    template = Path("app/templates/checkout.html").read_text(encoding="utf-8")
    assert 'name="provider_transaction_id"' not in template
    assert '<option value="MTN">MTN Mobile Money</option>' in template
    assert '<option value="ORANGE">Orange Money</option>' in template
    assert "providerCode.required = isMobile || isMixed" in template
