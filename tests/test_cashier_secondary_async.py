"""Secondary cashier AJAX: idempotency, permissions, accounting and native fallback."""
from decimal import Decimal

from sqlalchemy import func, select

from app.cash_services import cash_service
from app.change_voucher_models import ChangeVoucherTransaction
from app.customer_models import CustomerLedgerEntry
from app.customer_services import customer_service
from app.expense_services import expense_service
from app.extensions import db
from app.models import CashMovement, Customer, Expense, IdempotencyRecord
from test_cashier_expenses import _cashier, _csrf, _login
from test_change_vouchers import _issue_voucher
from test_workflows import env, order


AJAX = {
    "Accept": "application/json", "X-Requested-With": "XMLHttpRequest", "X-BarPro-Async": "1"
}


def post(client, url, csrf, **fields):
    return client.post(url, data={"csrf_token": csrf, **fields}, headers=AJAX)


def count(model, **fields):
    return db.session.scalar(select(func.count()).select_from(model).filter_by(**fields))


def test_cashier_expense_is_async_idempotent_and_atomic(env):
    app, owner, bar, foreign, *_ = env
    category = expense_service.create_category(owner, bar.id, "Transport")
    db.session.commit()
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(owner, bar.id, "CASH-EXP-SEC", 1000)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/expenses"
    page = client.get(path)
    assert page.status_code == 200
    assert 'data-bp-secondary="expenses"' in page.text
    assert 'name="idempotency_key"' in page.text
    fields = dict(
        action="expense_create", expense_category_id=str(category.id), amount="300",
        method="CASH", description="Livraison glaçons", cash_session_id=str(session.id),
        idempotency_key="secondary-expense-intent-01",
    )
    response = post(client, path, _csrf(page), **fields)
    assert response.status_code == 200, response.text
    assert response.json["success"] is True
    assert response.json["data"]["expense_id"]
    assert count(Expense, bar_id=bar.id, entry_kind="EXPENSE") == 1
    assert Decimal(cash_service.expected(session)) == 700
    assert post(client, path, _csrf(page), **fields).status_code == 409
    assert post(client, path, _csrf(page), **{**fields, "amount": "301"}).status_code == 409
    assert count(Expense, bar_id=bar.id, entry_kind="EXPENSE") == 1

    bad = {**fields, "amount": "800", "idempotency_key": "secondary-expense-insufficient-01"}
    response = post(client, path, _csrf(page), **bad)
    assert response.status_code == 409, response.text
    assert count(Expense, bar_id=bar.id, entry_kind="EXPENSE") == 1
    assert Decimal(cash_service.expected(session)) == 700
    assert count(IdempotencyRecord, bar_id=bar.id, operation="web.cashier.financial.secondary.expense.expense_create") == 1

    assert client.get(f"/bars/{foreign.id}/expenses").status_code in (403, 404)
    native_page = client.get(path)
    native = client.post(path, data={
        **{k: v for k, v in fields.items() if k != "idempotency_key"},
        "csrf_token": _csrf(native_page), "amount": "100",
    }, follow_redirects=False)
    assert native.status_code == 302
    assert count(Expense, bar_id=bar.id, entry_kind="EXPENSE") == 2


def test_customer_credit_payment_and_case_context_refresh(env):
    app, owner, bar, _, product, *_ = env
    bar.credit_sales_enabled = True
    customer = customer_service.create(owner, bar.id, {"display_name": "Client Crédit", "phone": "", "email": ""})
    db.session.commit()
    invoice = order(env)
    customer_service.credit_order(owner, bar.id, invoice.id, customer.id, "CREDIT-SECONDARY-1")
    db.session.commit()
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(owner, bar.id, "CASH-CLIENT-SEC", 100)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/customers"
    page = client.get(path)
    assert page.status_code == 200
    assert 'data-bp-secondary-section="customers-content"' in page.text
    assert 'data-bp-secondary="customers"' in page.text

    fields = dict(
        action="payment", customer_id=str(customer.id), amount="100", method="CASH",
        cash_session_id=str(session.id), reason="Règlement client",
        idempotency_key="secondary-client-payment-01",
    )
    response = post(client, path, _csrf(page), **fields)
    assert response.status_code == 200, response.text
    assert response.json["data"]["customer_id"] == customer.id
    assert customer_service.debt(bar.id, customer.id) == Decimal("100")
    assert cash_service.expected(session) == Decimal("200")
    assert post(client, path, _csrf(page), **fields).status_code == 409
    assert customer_service.debt(bar.id, customer.id) == Decimal("100")

    excessive = post(client, path, _csrf(page), **{
        **fields, "idempotency_key": "secondary-client-overpay", "amount": "150"
    })
    assert excessive.status_code == 409, excessive.text
    assert customer_service.debt(bar.id, customer.id) == Decimal("100")
    assert count(CustomerLedgerEntry, bar_id=bar.id, entry_kind="PAYMENT") == 1

    case = post(client, path, _csrf(page), action="case_out", customer_id=str(customer.id),
                product_id=str(product.id), quantity="2", reason="Deux casiers confiés",
                idempotency_key="secondary-case-out-01")
    assert case.status_code == 200, case.text
    assert customer_service.case_balance(bar.id, customer.id, product.id) == 2
    assert post(client, path, _csrf(page), action="case_out", customer_id=str(customer.id),
                product_id=str(product.id), quantity="2", reason="Deux casiers confiés",
                idempotency_key="secondary-case-out-01").status_code == 409
    assert customer_service.case_balance(bar.id, customer.id, product.id) == 2
    assert client.get(path).status_code == 200


def test_daily_cash_movement_replay_and_closed_session(env):
    app, owner, bar, *_ = env
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(owner, bar.id, "CASH-DAILY-SEC", 800)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/cashier-session/daily"
    page = client.get(path)
    assert page.status_code == 200
    assert 'data-bp-secondary="daily"' in page.text
    assert 'name="idempotency_key"' in page.text
    fields = {
        "action": "movement", "kind": "WITHDRAWAL", "amount": "250",
        "reason": "Achat urgent", "idempotency_key": "secondary-daily-cash-01",
    }
    response = post(client, path, _csrf(page), **fields)
    assert response.status_code == 200, response.text
    assert cash_service.expected(session) == Decimal("550")
    assert post(client, path, _csrf(page), **fields).status_code == 409
    assert cash_service.expected(session) == Decimal("550")

    fail = post(client, path, _csrf(page), **{
        **fields, "amount": "999", "idempotency_key": "secondary-daily-overdraft-01"
    })
    assert fail.status_code == 409, fail.text
    assert cash_service.expected(session) == Decimal("550")

    cash_service.close(owner, bar.id, session.id, 550)
    db.session.commit()
    closed = post(client, path, _csrf(page), **{
        **fields, "idempotency_key": "secondary-daily-closed-01"
    })
    assert closed.status_code == 409
    assert count(CashMovement, bar_id=bar.id, manual_kind="WITHDRAWAL") == 1


def test_voucher_cash_refund_replay_and_print_preserved(env):
    app, owner, bar, _, product, *_ = env
    cashier, _ = _cashier(owner, bar)
    session = cash_service.open(owner, bar.id, "CASH-VOUCHER-SEC", 0)
    db.session.commit()
    _, _, voucher = _issue_voucher(owner, bar, product, session)
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/cashier/change-vouchers/"
    page = client.get(path)
    assert page.status_code == 200
    assert 'data-bp-secondary="vouchers"' in page.text
    assert 'data-bp-secondary-section="vouchers-content"' in page.text
    assert f"/bars/{bar.id}/cashier/change-vouchers/{voucher.id}/print" in page.text
    fields = {
        "action": "refund", "code": voucher.code, "amount": "400",
        "idempotency_key": "secondary-voucher-refund-01",
    }
    before = Decimal(cash_service.expected(session))
    response = post(client, path, _csrf(page), **fields)
    assert response.status_code == 200, response.text
    assert response.json["data"]["voucher_id"] == voucher.id
    db.session.refresh(voucher)
    assert voucher.balance_amount == Decimal("1000")
    assert cash_service.expected(session) == before - 400
    assert post(client, path, _csrf(page), **fields).status_code == 409
    assert count(ChangeVoucherTransaction, bar_id=bar.id, kind="CASH_REFUND") == 1
    assert client.get(f"/bars/{bar.id}/cashier/change-vouchers/{voucher.id}/print").status_code == 200


def test_history_filter_is_get_and_preserves_print_links(env):
    app, owner, bar, *_ = env
    cashier, _ = _cashier(owner, bar)
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    path = f"/bars/{bar.id}/cashier-history"
    page = client.get(path + "?type=REFUND")
    assert page.status_code == 200
    assert 'data-bp-secondary-filter="history"' in page.text
    assert 'data-bp-secondary-section="history-results"' in page.text
    # GET and its query string are still the canonical source of filtering.
    assert client.get(path + "?type=PAYMENT&method=CASH").status_code == 200
    assert 'checkout_web.receipt' in open("app/templates/cashier_history.html", encoding="utf-8").read()
