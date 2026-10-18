"""Cashier async safety regression tests (payments, credit, stock, tenancy)."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from sqlalchemy import func, select

from app.cash_services import cash_service
from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.models import Customer, IdempotencyRecord, Order, Payment, StockMovement, User
from app.order_services import order_service
from test_cashier_workspace_async import _async_headers, _cashier, _csrf, _login
from test_workflows import env


def _ready(env, reference="SAFE-ORDER-1", *, delivered=True):
    app, owner, bar, _, product, _, _, _ = env
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "SAFE-CASH-SESSION", 1000)
    order = order_service.create(owner, bar.id, reference, [{"product_id": product.id, "quantity": 1}])
    if delivered:
        order_service.confirm(cashier, bar.id, order.id)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    url = f"/bars/{bar.id}/cashier/workspace"
    token = _csrf(client.get(f"{url}?order_id={order.id}"))
    return client, cashier, session, order, url, token


def test_exact_payment_replay_and_payload_change_return_409_without_extra_cash(env):
    client, cashier, session, order, url, token = _ready(env)
    data = {"csrf_token": token, "action": "cash_exact", "order_id": str(order.id),
            "reference": "SAFE-EXACT-0001"}
    first = client.post(url, data=data, headers=_async_headers(token))
    assert first.status_code == 200
    for payload in (data, {**data, "reference_override": "changed"}):
        replay = client.post(url, data=payload, headers=_async_headers(token))
        assert replay.status_code == 409
    assert db.session.scalar(select(func.count(Payment.id)).where(Payment.order_id == order.id)) == 1
    assert db.session.scalar(select(func.count(IdempotencyRecord.id)).where(
        IdempotencyRecord.bar_id == order.bar_id, IdempotencyRecord.actor_id == cashier.id,
        IdempotencyRecord.operation == "web.cashier.cash_exact")) == 1
    assert Decimal(cash_service.expected(session)) == Decimal("1100")


def test_mixed_payment_is_atomic_and_replay_does_not_double_charge(env):
    client, _, session, order, url, token = _ready(env)
    data = {"csrf_token": token, "action": "payment", "order_id": str(order.id),
            "idempotency_key": "SAFE-MIXED-INTENT-0001", "reference": "SAFE-MIXED-0001",
            "method": "MIXED", "mixed_cash": "40", "mixed_mobile": "60",
            "amount_presented": "50", "provider_code": "MTN",
            "provider_transaction_id": "SAFE-MOMO-0001"}
    response = client.post(url, data=data, headers=_async_headers(token))
    assert response.status_code == 200
    assert client.post(url, data=data, headers=_async_headers(token)).status_code == 409
    rows = list(db.session.scalars(select(Payment).where(Payment.order_id == order.id)))
    assert len(rows) == 2
    assert {row.method for row in rows} == {"CASH", "MOBILE_MONEY"}
    assert sum(Decimal(row.amount_applied) for row in rows) == Decimal("100")
    assert Decimal(cash_service.expected(session)) == Decimal("1040")
    db.session.refresh(order)
    assert order.payment_status == "PAID"


def test_failed_mixed_payment_rolls_back_intent_and_can_retry_same_key(env):
    client, _, _, order, url, token = _ready(env)
    data = {"csrf_token": token, "action": "payment", "order_id": str(order.id),
            "idempotency_key": "SAFE-MIXED-RETRY-0001", "reference": "SAFE-MIXED-RETRY",
            "method": "MIXED", "mixed_cash": "40", "mixed_mobile": "60",
            "amount_presented": "40", "provider_code": "MTN",
            "provider_transaction_id": "SAFE-RETRY-0001"}
    failed = client.post(url, data={**data, "amount_presented": "20"}, headers=_async_headers(token))
    assert failed.status_code == 422
    assert db.session.scalar(select(func.count(IdempotencyRecord.id)).where(
        IdempotencyRecord.operation == "web.cashier.payment")) == 0
    assert client.post(url, data=data, headers=_async_headers(token)).status_code == 200


def test_credit_payment_is_idempotent_and_keeps_customer_ledger(env):
    app, owner, bar, *_ = env
    bar.credit_sales_enabled = True
    customer = Customer(bar_id=bar.id, display_name="Client crédit", is_active=True)
    db.session.add(customer)
    db.session.commit()
    client, _, _, order, url, token = _ready(env)
    data = {"csrf_token": token, "action": "payment", "order_id": str(order.id),
            "reference": "SAFE-CREDIT-INTENT-0001", "method": "CREDIT",
            "customer_id": str(customer.id)}
    assert client.post(url, data=data, headers=_async_headers(token)).status_code == 200
    assert client.post(url, data=data, headers=_async_headers(token)).status_code == 409
    assert db.session.scalar(select(func.count(CustomerLedgerEntry.id)).where(
        CustomerLedgerEntry.order_id == order.id, CustomerLedgerEntry.entry_kind == "CREDIT_SALE")) == 1
    db.session.refresh(order)
    assert order.payment_status == "PAID"


def test_delivery_twice_is_conflict_and_stock_is_deducted_once(env):
    client, _, _, order, url, token = _ready(env, delivered=False)
    data = {"csrf_token": token, "action": "deliver", "order_id": str(order.id)}
    assert client.post(url, data=data, headers=_async_headers(token)).status_code == 200
    assert client.post(url, data=data, headers=_async_headers(token)).status_code == 409
    assert db.session.scalar(select(func.count(StockMovement.id)).where(
        StockMovement.bar_id == order.bar_id, StockMovement.order_line_id.is_not(None))) == 1


def test_closed_session_and_foreign_bar_cannot_collect(env):
    client, cashier, session, order, url, token = _ready(env)
    foreign = env[3]
    forbidden = client.post(f"/bars/{foreign.id}/cashier/workspace",
                            data={"csrf_token": token, "action": "cash_exact",
                                  "order_id": str(order.id), "reference": "SAFE-FOREIGN"},
                            headers=_async_headers(token))
    assert forbidden.status_code in {403, 404}
    session.status = "CLOSED"
    db.session.commit()
    response = client.post(url, data={"csrf_token": token, "action": "cash_exact",
                           "order_id": str(order.id), "reference": "SAFE-CLOSED"},
                           headers=_async_headers(token))
    assert response.status_code == 409
    assert db.session.scalar(select(func.count(Payment.id)).where(Payment.order_id == order.id)) == 0


def test_simultaneous_same_payment_intent_is_applied_at_most_once(env):
    client1, cashier, session, order, url, token1 = _ready(env)
    client2 = env[0].test_client()
    assert _login(client2, cashier.email).status_code == 302
    token2 = _csrf(client2.get(f"{url}?order_id={order.id}"))
    barrier = Barrier(2)

    def send(client, token):
        data = {"csrf_token": token, "action": "cash_exact", "order_id": str(order.id),
                "idempotency_key": "SAFE-CONCURRENT-0001", "reference": "SAFE-CONCURRENT-PAY"}
        barrier.wait(timeout=10)
        return client.post(url, data=data, headers=_async_headers(token)).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(send, client1, token1)
        b = pool.submit(send, client2, token2)
        statuses = sorted((a.result(timeout=30), b.result(timeout=30)))
    assert statuses == [200, 409]
    db.session.expire_all()
    assert db.session.scalar(select(func.count(Payment.id)).where(Payment.order_id == order.id)) == 1
    assert Decimal(cash_service.expected(session)) == Decimal("1100")
