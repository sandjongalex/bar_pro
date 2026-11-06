"""Waitress cash tender: own-order custody, exact change and replay safety."""
import re
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.extensions import db
from app.finance_totals import order_balance
from app.models import CashMovement, Order, Payment, StaffAssignment, StaffCashLedger
from app.order_services import order_service
from app.payment_services import payment_service
from test_server_async_orders import _headers, _order_payload, _session
from test_workflows import env


def _prepare(env):
    app, owner, bar, _, product, server, _, _ = env
    client, cashier, csrf, key = _session(env)
    create = client.post(
        f"/bars/{bar.id}/orders/new",
        data=_order_payload(product, key, quantity="2", name="Espèces client"),
        headers=_headers(csrf),
    )
    assert create.status_code == 201, create.text
    order_id = create.json["data"]["id"]
    order_service.confirm(cashier, bar.id, order_id)
    db.session.commit()
    return client, owner, bar, server, order_id, csrf


def _cash_form(client, bar_id, order_id):
    page = client.get(f"/bars/{bar_id}/orders/{order_id}/detail")
    assert page.status_code == 200
    assert 'data-server-cash-form' in page.text
    assert 'data-server-cash-change' in page.text
    assert 'server_cash_ui.js' in page.text
    token = re.search(r'name="idempotency_key" value="([^"]+)"', page.text)
    due = re.search(r'name="expected_due" value="([^"]+)"', page.text)
    assert token and due
    return {"idempotency_key": token.group(1), "expected_due": due.group(1)}


def test_waitress_full_cash_change_is_in_own_staff_ledger(env):
    client, owner, bar, server, order_id, csrf = _prepare(env)
    fields = _cash_form(client, bar.id, order_id)
    due = Decimal(fields["expected_due"])
    tender = due + 300
    posted = client.post(
        f"/bars/{bar.id}/orders/{order_id}/cash",
        data={**fields, "amount_presented": str(tender), "csrf_token": csrf},
        headers=_headers(csrf),
    )
    assert posted.status_code == 200, posted.text
    assert posted.json["success"]
    payment = db.session.scalar(
        select(Payment).where(Payment.bar_id == bar.id, Payment.order_id == order_id)
    )
    assert payment is not None
    assert payment.method == "CASH"
    assert payment.cash_holder == "STAFF"
    assert payment.cash_session_id is None
    assert Decimal(payment.amount_presented) == tender
    assert Decimal(payment.amount_applied) == due
    assert Decimal(payment.change_given) == Decimal("300")
    server_staff = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id, StaffAssignment.user_id == server.id
        )
    )
    assert payment.staff_assignment_id == server_staff.id
    balance = db.session.scalar(
        select(func.sum(StaffCashLedger.amount_delta)).where(
            StaffCashLedger.bar_id == bar.id, StaffCashLedger.payment_id == payment.id,
        )
    )
    assert Decimal(balance) == due
    assert db.session.scalar(select(func.count()).select_from(CashMovement).where(
        CashMovement.bar_id == bar.id, CashMovement.payment_id == payment.id
    )) == 0
    assert db.session.get(Order, order_id).payment_status == "PAID"
    detail = client.get(f"/bars/{bar.id}/orders/{order_id}/detail")
    assert "Montant remis" in detail.text
    assert "Monnaie rendue" in detail.text
    assert "300" in detail.text

    replay = client.post(
        f"/bars/{bar.id}/orders/{order_id}/cash",
        data={**fields, "amount_presented": str(tender), "csrf_token": csrf},
        headers=_headers(csrf),
    )
    assert replay.status_code in (409, 422)
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order_id
    )) == 1


def test_waitress_partial_payment_and_stale_balance_are_safe(env):
    client, _, bar, _, order_id, csrf = _prepare(env)
    initial = _cash_form(client, bar.id, order_id)
    due = Decimal(initial["expected_due"])
    partial_amount = due / 2
    first = client.post(
        f"/bars/{bar.id}/orders/{order_id}/cash",
        data={**initial, "amount_presented": str(partial_amount),
              "csrf_token": csrf},
    )
    assert first.status_code == 302
    assert db.session.get(Order, order_id).payment_status == "PARTIAL"
    assert Decimal(order_balance(db.session.get(Order, order_id))["amount_due"]) == partial_amount

    stale = client.post(
        f"/bars/{bar.id}/orders/{order_id}/cash",
        data={**initial, "amount_presented": str(partial_amount),
              "csrf_token": csrf},
        headers=_headers(csrf),
    )
    assert stale.status_code == 422
    fresh = _cash_form(client, bar.id, order_id)
    assert Decimal(fresh["expected_due"]) == partial_amount
    second = client.post(
        f"/bars/{bar.id}/orders/{order_id}/cash",
        data={**fresh, "amount_presented": str(partial_amount + 100),
              "csrf_token": csrf},
        headers=_headers(csrf),
    )
    assert second.status_code == 200
    assert db.session.get(Order, order_id).payment_status == "PAID"
    payments = list(db.session.scalars(
        select(Payment).where(Payment.bar_id == bar.id, Payment.order_id == order_id)
    ))
    assert len(payments) == 2
    assert sum((Decimal(item.amount_applied) for item in payments), Decimal("0")) == due


def test_waitress_cannot_collect_on_unowned_or_undelivered_order_or_non_cash(env):
    client, owner, bar, server, order_id, csrf = _prepare(env)
    own = _cash_form(client, bar.id, order_id)
    other = order_service.create(
        owner, bar.id, "DIFFERENT-STAFF-CASH-ORDER",
        [{"product_id": env[4].id, "quantity": "1"}],
    )
    order_service.confirm(owner, bar.id, other.id)
    draft = order_service.create(
        server, bar.id, "UNDELIVERED-SERVER-CASH-ORDER",
        [{"product_id": env[4].id, "quantity": "1"}],
    )
    db.session.commit()
    wrong = client.post(
        f"/bars/{bar.id}/orders/{other.id}/cash",
        data={**own, "amount_presented": "100", "csrf_token": csrf},
        headers=_headers(csrf),
    )
    assert wrong.status_code == 404
    pending = client.post(
        f"/bars/{bar.id}/orders/{draft.id}/cash",
        data={**own, "amount_presented": "100", "csrf_token": csrf},
        headers=_headers(csrf),
    )
    assert pending.status_code == 422
    with pytest.raises(PermissionError):
        payment_service.record(
            server, bar.id, order_id, "FORBIDDEN-WAITRESS-CARD",
            "CARD", 100, 100
        )
    db.session.rollback()
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id
    )) == 0


def test_server_cash_requires_csrf_and_correct_form_data(env):
    client, _, bar, _, order_id, csrf = _prepare(env)
    fields = _cash_form(client, bar.id, order_id)
    url = f"/bars/{bar.id}/orders/{order_id}/cash"
    response = client.post(
        url, data={**fields, "amount_presented": "100"},
        headers=_headers("invalid"),
    )
    assert response.status_code == 400
    negative = client.post(
        url, data={**fields, "amount_presented": "-10", "csrf_token": csrf},
        headers=_headers(csrf),
    )
    assert negative.status_code == 422
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order_id
    )) == 0
