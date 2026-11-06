"""Cashier-created invoices: inline cash from client or waitress, with no double count."""
from decimal import Decimal

from sqlalchemy import func, select

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import CashMovement, Order, Payment, StaffAssignment, StaffCashLedger, StockBalance
from test_cashier_workspace_async import _async_headers, _cashier, _csrf, _login
from test_workflows import env


def _cashier_client(env):
    app, _, bar, _, _, _, _, _ = env
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "INLINE-CASH-SESSION", 1000)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier/workspace?sale=1")
    assert page.status_code == 200
    assert 'name="cash_presented"' in page.text
    assert 'name="cash_receipt_source"' in page.text
    assert 'id="cashierTenderAmount"' in page.text
    assert 'data-cashier-tender-change' in page.text
    assert 'data-cashier-tender-remaining' in page.text
    return client, session, _csrf(page)


def test_inline_client_cash_records_actual_tender_change_and_drawer_only(env):
    app, _, bar, _, product, _, _, _ = env
    client, session, csrf = _cashier_client(env)
    product.sale_price = Decimal("600")
    db.session.commit()

    url = f"/bars/{bar.id}/cashier/workspace"
    payload = {
        "csrf_token": csrf, "action": "create_sale",
        "reference": "INLINE-CASH-CLIENT-1",
        "invoice_name": "Paul",
        "product_id": str(product.id), "quantity": "1",
        "cash_presented": "1000", "cash_receipt_source": "CLIENT",
    }
    response = client.post(url, data=payload, headers=_async_headers(csrf))
    assert response.status_code == 200, response.text
    data = response.json["data"]
    assert data["cash_summary"] == {
        "amount_presented": "1000", "amount_applied": "600",
        "change_given": "400", "remaining": "0",
        "source": "client", "currency": "XAF",
    }
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "INLINE-CASH-CLIENT-1"
    ))
    assert order.status == "CONFIRMED"
    assert order.payment_status == "PAID"
    payment = db.session.scalar(select(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    ))
    assert payment.cash_holder == "DRAWER"
    assert payment.cash_session_id == session.id
    assert Decimal(payment.amount_presented) == 1000
    assert Decimal(payment.amount_applied) == 600
    assert Decimal(payment.change_given) == 400
    cash_line = db.session.scalar(select(CashMovement).where(
        CashMovement.bar_id == bar.id, CashMovement.payment_id == payment.id
    ))
    assert Decimal(cash_line.amount_delta) == 600
    assert "remis par client" in cash_line.reason
    assert Decimal(cash_service.expected(session)) == 1600

    duplicate = client.post(url, data=payload, headers=_async_headers(csrf))
    assert duplicate.status_code == 409
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    )) == 1


def test_waitress_hands_drawer_cash_for_cashier_created_invoice(env):
    app, _, bar, _, product, server, _, _ = env
    client, session, csrf = _cashier_client(env)
    product.sale_price = Decimal("800")
    assignment = db.session.scalar(select(StaffAssignment).where(
        StaffAssignment.bar_id == bar.id, StaffAssignment.user_id == server.id
    ))
    db.session.commit()
    payload = {
        "csrf_token": csrf, "action": "create_sale",
        "reference": "INLINE-CASH-SERVER-1",
        "product_id": str(product.id), "quantity": "1",
        "cash_presented": "500", "cash_receipt_source": "SERVER",
        "cash_receipt_server_id": str(assignment.id),
    }
    url = f"/bars/{bar.id}/cashier/workspace"
    response = client.post(url, data=payload, headers=_async_headers(csrf))
    assert response.status_code == 200, response.text
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "INLINE-CASH-SERVER-1"
    ))
    assert order.assigned_staff_id is None  # Still a cashier-created counter invoice.
    assert order.payment_status == "PARTIAL"
    assert Decimal(order_balance(order)["amount_due"]) == Decimal("300")
    payment = db.session.scalar(select(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    ))
    assert payment.staff_assignment_id is None
    assert payment.cash_session_id == session.id
    assert Decimal(payment.change_given) == 0
    movement = db.session.scalar(select(CashMovement).where(
        CashMovement.bar_id == bar.id, CashMovement.payment_id == payment.id
    ))
    assert "serveuse #" + str(assignment.id) in movement.reason
    assert db.session.scalar(select(func.count()).select_from(StaffCashLedger).where(
        StaffCashLedger.bar_id == bar.id, StaffCashLedger.payment_id == payment.id
    )) == 0


def test_invalid_cash_source_rolls_back_counter_sale_and_stock(env):
    app, _, bar, _, product, _, _, _ = env
    client, _, csrf = _cashier_client(env)
    original = db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id
    ))
    payload = {
        "csrf_token": csrf, "action": "create_sale",
        "reference": "INLINE-CASH-INVALID-SOURCE",
        "product_id": str(product.id), "quantity": "1",
        "cash_presented": "300", "cash_receipt_source": "SERVER",
        "cash_receipt_server_id": "999999",
    }
    result = client.post(
        f"/bars/{bar.id}/cashier/workspace",
        data=payload, headers=_async_headers(csrf),
    )
    assert result.status_code == 422
    assert db.session.scalar(select(Order.id).where(
        Order.bar_id == bar.id, Order.reference == payload["reference"]
    )) is None
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id
    )) == original

    bad_amount = dict(payload, reference="INLINE-CASH-INVALID-AMOUNT", cash_receipt_source="CLIENT",
                      cash_presented="100.5")
    result = client.post(
        f"/bars/{bar.id}/cashier/workspace",
        data=bad_amount, headers=_async_headers(csrf),
    )
    assert result.status_code == 422
    assert db.session.scalar(select(Order.id).where(
        Order.bar_id == bar.id, Order.reference == bad_amount["reference"]
    )) is None


def test_empty_inline_amount_keeps_existing_unpaid_invoice_flow(env):
    app, _, bar, _, product, _, _, _ = env
    client, _, csrf = _cashier_client(env)
    payload = {
        "csrf_token": csrf, "action": "create_sale",
        "reference": "INLINE-CASH-NO-TENDER",
        "product_id": str(product.id), "quantity": "1",
        "cash_presented": "", "cash_receipt_source": "CLIENT",
    }
    result = client.post(
        f"/bars/{bar.id}/cashier/workspace",
        data=payload, headers=_async_headers(csrf),
    )
    assert result.status_code == 200
    assert result.json["data"]["cash_summary"] is None
    assert "focus=payment" in result.json["data"]["refresh_url"]
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == payload["reference"]
    ))
    assert order.payment_status == "UNPAID"
    assert db.session.scalar(select(Payment.id).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    )) is None


def test_cashier_can_receive_from_waitress_after_creating_own_invoice(env):
    app, _, bar, _, product, server, _, _ = env
    client, session, csrf = _cashier_client(env)
    staff = db.session.scalar(select(StaffAssignment).where(
        StaffAssignment.bar_id == bar.id, StaffAssignment.user_id == server.id
    ))
    db.session.commit()
    url = f"/bars/{bar.id}/cashier/workspace"
    create = client.post(url, data={
        "csrf_token": csrf, "action": "create_sale",
        "reference": "INLINE-CASH-LATER-STAFF",
        "product_id": str(product.id), "quantity": "1",
    }, headers=_async_headers(csrf))
    assert create.status_code == 200
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "INLINE-CASH-LATER-STAFF"
    ))
    assert order.payment_status == "UNPAID"
    page = client.get(f"{url}?order_id={order.id}")
    assert 'id="cashierPaymentReceiptSource"' in page.text
    assert 'id="cashierPaymentReceiptServer"' in page.text
    due = Decimal(order_balance(order)["amount_due"])
    amount = due + Decimal("500")
    paid = client.post(url, data={
        "csrf_token": csrf, "action": "payment",
        "order_id": str(order.id), "method": "CASH",
        "reference": "INLINE-CASH-LATER-PAY",
        "amount_applied": str(due),
        "amount_presented": str(amount),
        "cash_receipt_source": "SERVER",
        "cash_receipt_server_id": str(staff.id),
    }, headers=_async_headers(csrf))
    assert paid.status_code == 200, paid.text
    assert db.session.get(Order, order.id).payment_status == "PAID"
    payment = db.session.scalar(select(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    ))
    assert Decimal(payment.change_given) == Decimal("500")
    assert payment.cash_session_id == session.id
    assert payment.staff_assignment_id is None
    movement = db.session.scalar(select(CashMovement).where(
        CashMovement.bar_id == bar.id, CashMovement.payment_id == payment.id
    ))
    assert "serveuse #" + str(staff.id) in movement.reason
