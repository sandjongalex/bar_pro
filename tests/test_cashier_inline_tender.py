"""Cashier-created invoices: inline cash from client or waitress, with no double count."""
from decimal import Decimal

from sqlalchemy import func, select

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import CashMovement, Order, Payment, StaffAssignment, StaffCashLedger, StockBalance
from app.change_voucher_models import ChangeVoucher, ChangeVoucherTransaction
from app.order_suborder_service import order_suborder_service
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
    assert data["cash_summary"]["amount_presented"] == "1000"
    assert data["cash_summary"]["amount_applied"] == "600"
    assert data["cash_summary"]["change_given"] == "0"
    assert data["cash_summary"]["advance_balance"] == "400"
    assert data["cash_summary"]["order_open"] is True
    assert data["cash_summary"]["advance_code"]
    assert data["payment_status"] == "PAID"  # Financial balance only: tab stays open.
    assert "focus=payment" not in data["refresh_url"]
    assert f"order_id=" in data["refresh_url"]
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
    assert Decimal(payment.change_given) == 0
    cash_line = db.session.scalar(select(CashMovement).where(
        CashMovement.bar_id == bar.id, CashMovement.payment_id == payment.id
    ))
    assert Decimal(cash_line.amount_delta) == 600
    assert "remis par client" in cash_line.reason
    voucher = db.session.scalar(select(ChangeVoucher).where(
        ChangeVoucher.bar_id == bar.id, ChangeVoucher.origin_order_id == order.id,
    ))
    assert voucher is not None
    assert Decimal(voucher.balance_amount) == Decimal("400")
    from app.change_voucher_service import change_voucher_liability
    assert change_voucher_liability(bar.id)["amount"] == Decimal("0")  # AV- is not a portable BM-
    assert Decimal(cash_service.expected(session)) == 2000

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
    assert response.json["data"]["payment_status"] == "PARTIAL"
    assert response.json["data"]["cash_summary"]["payment_status"] == "PARTIAL"
    assert "focus=payment" in response.json["data"]["refresh_url"]
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
    assert result.json["data"]["payment_status"] == "UNPAID"
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
    assert Decimal(payment.change_given) == Decimal("0")
    assert payment.cash_session_id == session.id
    voucher = db.session.scalar(select(ChangeVoucher).where(
        ChangeVoucher.bar_id == bar.id, ChangeVoucher.origin_order_id == order.id,
    ))
    assert voucher is not None
    assert Decimal(voucher.balance_amount) == Decimal("500")
    assert payment.staff_assignment_id is None
    movement = db.session.scalar(select(CashMovement).where(
        CashMovement.bar_id == bar.id, CashMovement.payment_id == payment.id
    ))
    assert "serveuse #" + str(staff.id) in movement.reason



def test_cashier_full_tender_is_not_in_payable_queue(env):
    """A paid-at-creation invoice must not be offered for collection again."""
    app, _, bar, _, product, *_ = env
    client, _, csrf = _cashier_client(env)
    product.sale_price = Decimal("900")
    db.session.commit()
    url = f"/bars/{bar.id}/cashier/workspace"
    response = client.post(url, data={
        "csrf_token": csrf,
        "action": "create_sale",
        "reference": "INLINE-CASH-PAID-NOT-DUE",
        "product_id": str(product.id),
        "quantity": "2",
        "cash_presented": "2000",
        "cash_receipt_source": "CLIENT",
    }, headers=_async_headers(csrf))
    assert response.status_code == 200, response.text
    assert response.json["data"]["payment_status"] == "PAID"
    assert "focus=payment" not in response.json["data"]["refresh_url"]
    assert "order_id=" in response.json["data"]["refresh_url"]

    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "INLINE-CASH-PAID-NOT-DUE"
    ))
    assert order.payment_status == "PAID"
    assert order.status == "CONFIRMED"
    assert Decimal(order_balance(order)["amount_due"]) == Decimal("0")
    page = client.get(url)
    assert page.status_code == 200
    payable_section = page.text.split('id="queuePayable"', 1)[1].split('id="queuePrepaid"', 1)[0]
    assert "INLINE-CASH-PAID-NOT-DUE" not in payable_section
    assert "INLINE-CASH-PAID-NOT-DUE" in page.text
    assert "EN COURS · AVANCES CLIENTS" in page.text
    # A paid-for-products tab with retained customer money must stay printable.
    receipt_path = f"/bars/{bar.id}/checkout/orders/{order.id}/receipt"
    assert f'data-prepaid-print="{order.id}"' in page.text
    assert f'data-selected-print="{order.id}"' in page.text
    assert receipt_path in page.text
    receipt = client.get(receipt_path)
    assert receipt.status_code == 200
    assert "COMMANDE EN COURS" in receipt.text
    assert "Avance disponible" in receipt.text



def test_cashier_keeps_advance_for_new_rounds_and_returns_only_remaining_cash(env):
    """3 000 sold + 10 000 received: keep 7 000, add 2 000, refund 5 000 at close."""
    app, _, bar, _, product, *_ = env
    client, session, csrf = _cashier_client(env)
    product.sale_price = Decimal("1000")
    db.session.commit()
    cashier = _cashier(bar)
    url = f"/bars/{bar.id}/cashier/workspace"
    sale = client.post(url, data={
        "csrf_token": csrf, "action": "create_sale",
        "reference": "ADVANCE-OPEN-3000",
        "product_id": str(product.id), "quantity": "3",
        "cash_presented": "10000", "cash_receipt_source": "CLIENT",
    }, headers=_async_headers(csrf))
    assert sale.status_code == 200, sale.text
    assert sale.json["data"]["cash_summary"]["advance_balance"] == "7000"
    assert sale.json["data"]["cash_summary"]["change_given"] == "0"
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "ADVANCE-OPEN-3000",
    ))
    voucher = db.session.scalar(select(ChangeVoucher).where(
        ChangeVoucher.bar_id == bar.id, ChangeVoucher.origin_order_id == order.id,
    ))
    assert order.closed_at is None
    assert order.payment_status == "PAID"  # Current due = 0; operational tab is still open.
    assert Decimal(voucher.balance_amount) == 7000
    assert Decimal(cash_service.expected(session)) == 11000  # Opening 1000 + held 10000.

    suborder = order_suborder_service.create_cashier_addition(
        cashier, bar.id, order.id, [{"product_id": product.id, "quantity": "2"}],
    )
    db.session.commit()
    assert suborder.status == "VALIDATED"
    assert Decimal(order.total_amount) == 5000
    assert order.closed_at is None
    assert Decimal(order_balance(order)["amount_due"]) == 0
    db.session.refresh(voucher)
    assert Decimal(voucher.balance_amount) == 5000
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id,
    )) == 1  # Redeeming the advance did not collect more physical cash.
    assert db.session.scalar(select(func.sum(ChangeVoucherTransaction.amount)).where(
        ChangeVoucherTransaction.bar_id == bar.id,
        ChangeVoucherTransaction.voucher_id == voucher.id,
        ChangeVoucherTransaction.kind == "REDEEM",
    )) == Decimal("2000")
    assert Decimal(cash_service.expected(session)) == 11000

    finish = client.post(url, data={
        "csrf_token": csrf, "action": "close_prepaid", "order_id": str(order.id),
    }, headers=_async_headers(csrf))
    assert finish.status_code == 200, finish.text
    assert finish.json["data"]["prepaid_closed"] is True
    db.session.refresh(order)
    db.session.refresh(voucher)
    assert order.closed_at is not None
    assert voucher.status == "SETTLED"
    assert Decimal(voucher.balance_amount) == 0
    assert Decimal(cash_service.expected(session)) == 6000  # 1000 float + 5000 sold.
    paid_invoices = client.get(f"/bars/{bar.id}/cashier/invoices?payment=paid")
    assert paid_invoices.status_code == 200
    assert f'data-invoice-print="{order.id}"' in paid_invoices.text
    assert f"/bars/{bar.id}/checkout/orders/{order.id}/receipt" in paid_invoices.text
    after_closing = client.get(url)
    assert after_closing.status_code == 200
    assert f'data-prepaid-order-id="{order.id}"' not in after_closing.text

    duplicate_close = client.post(url, data={
        "csrf_token": csrf, "action": "close_prepaid", "order_id": str(order.id),
    }, headers=_async_headers(csrf))
    assert duplicate_close.status_code == 422
    assert Decimal(cash_service.expected(session)) == 6000

    import pytest
    with pytest.raises(ValueError, match="ORDER_PAID"):
        order_suborder_service.create_cashier_addition(
            cashier, bar.id, order.id, [{"product_id": product.id, "quantity": "1"}],
        )
    db.session.rollback()


def test_cashier_cannot_close_open_advance_with_outstanding_products(env):
    app, _, bar, _, product, *_ = env
    client, session, csrf = _cashier_client(env)
    product.sale_price = Decimal("1000")
    db.session.commit()
    cashier = _cashier(bar)
    url = f"/bars/{bar.id}/cashier/workspace"
    sale = client.post(url, data={
        "csrf_token": csrf, "action": "create_sale",
        "reference": "ADVANCE-INSUFFICIENT",
        "product_id": str(product.id), "quantity": "3",
        "cash_presented": "10000", "cash_receipt_source": "CLIENT",
    }, headers=_async_headers(csrf))
    assert sale.status_code == 200
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "ADVANCE-INSUFFICIENT",
    ))
    # 8 extra products cost 8 000, while only 7 000 advance remains.
    order_suborder_service.create_cashier_addition(
        cashier, bar.id, order.id, [{"product_id": product.id, "quantity": "8"}],
    )
    db.session.commit()
    assert Decimal(order_balance(order)["amount_due"]) == 1000
    assert order.payment_status == "PARTIAL"
    assert Decimal(cash_service.expected(session)) == 11000
    close = client.post(url, data={
        "csrf_token": csrf, "action": "close_prepaid", "order_id": str(order.id),
    }, headers=_async_headers(csrf))
    assert close.status_code == 422
    assert order.closed_at is None
    assert Decimal(cash_service.expected(session)) == 11000
    voucher = db.session.scalar(select(ChangeVoucher).where(
        ChangeVoucher.bar_id == bar.id, ChangeVoucher.origin_order_id == order.id,
    ))
    assert Decimal(voucher.balance_amount) == 0


def test_cashier_can_issue_printable_change_voucher_without_double_payment(env):
    """Cash shortage: 5 000 tendered for 3 800, 200 returned, 1 000 owed."""
    from app.change_voucher_service import change_voucher_liability

    app, _, bar, _, product, *_ = env
    client, session, csrf = _cashier_client(env)
    product.sale_price = Decimal("3800")
    db.session.commit()
    url = f"/bars/{bar.id}/cashier/workspace"
    create = client.post(url, data={
        "csrf_token": csrf, "action": "create_sale",
        "reference": "BON-MONNAIE-3800",
        "product_id": str(product.id), "quantity": "1",
    }, headers=_async_headers(csrf))
    assert create.status_code == 200, create.text
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "BON-MONNAIE-3800"
    ))
    assert order.payment_status == "UNPAID"

    payment_data = {
        "csrf_token": csrf,
        "idempotency_key": "BON-MONNAIE-TEST-KEY-1",
        "reference": "BON-MONNAIE-CASH-1",
        "order_id": str(order.id),
        "method": "CASH",
        "amount_presented": "5000",
        "amount_applied": "3800",
        "actual_change_given": "200",
    }
    post_url = f"/bars/{bar.id}/cashier/change-vouchers/pay"
    received = client.post(post_url, data=payment_data)
    assert received.status_code == 302
    assert "/print" in received.headers["Location"]
    voucher = db.session.scalar(select(ChangeVoucher).where(
        ChangeVoucher.bar_id == bar.id, ChangeVoucher.origin_order_id == order.id
    ))
    assert voucher.code.startswith("BM-")
    assert voucher.initial_amount == Decimal("1000")
    assert change_voucher_liability(bar.id)["amount"] == Decimal("1000")
    assert order.payment_status == "PAID"
    # Opening float 1000 + money received 5000 - change returned 200.
    assert cash_service.expected(session) == Decimal("5800")
    print_page = client.get(received.headers["Location"])
    assert print_page.status_code == 200
    assert "BON DE MONNAIE" in print_page.text
    assert voucher.code in print_page.text
    assert 'data-rawbt-intent="intent:base64,' in print_page.text

    repeated = client.post(post_url, data=payment_data)
    assert repeated.status_code == 302
    assert db.session.scalar(select(func.count()).select_from(Payment).where(
        Payment.bar_id == bar.id, Payment.order_id == order.id
    )) == 1
    assert change_voucher_liability(bar.id)["amount"] == Decimal("1000")
    assert cash_service.expected(session) == Decimal("5800")


def test_cashier_counter_sale_can_finish_with_portable_change_voucher(env):
    app, _, bar, _, product, *_ = env
    client, session, csrf = _cashier_client(env)
    product.sale_price = Decimal("3800")
    db.session.commit()
    url = f"/bars/{bar.id}/cashier/workspace"
    saved = client.post(url, data={
        "csrf_token": csrf, "action": "create_sale",
        "reference": "COUNTER-BM-FINAL",
        "product_id": str(product.id), "quantity": "1",
        "cash_presented": "5000", "cash_receipt_source": "CLIENT",
        "cash_surplus_action": "CHANGE_VOUCHER",
        "cash_actual_change": "200",
        "voucher_customer_name": "Client Test",
    }, headers=_async_headers(csrf))
    assert saved.status_code == 200, saved.text
    summary = saved.json["data"]["cash_summary"]
    assert summary["order_open"] is False
    assert summary["advance_code"] is None
    assert summary["voucher_code"].startswith("BM-")
    assert summary["voucher_balance"] == "1000"
    assert summary["voucher_url"].endswith("/print")
    assert summary["change_given"] == "200"
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == "COUNTER-BM-FINAL"
    ))
    assert order.payment_status == "PAID"
    assert order.closed_at is not None
    voucher = db.session.scalar(select(ChangeVoucher).where(
        ChangeVoucher.bar_id == bar.id, ChangeVoucher.origin_order_id == order.id,
    ))
    assert voucher.code.startswith("BM-")
    assert voucher.customer_name == "Client Test"
    assert voucher.balance_amount == Decimal("1000")
    assert cash_service.expected(session) == Decimal("5800")
    response = client.get(summary["voucher_url"])
    assert response.status_code == 200
    assert "BON DE MONNAIE" in response.text


def test_counter_sale_invalid_change_never_creates_payment_or_voucher(env):
    app, _, bar, _, product, *_ = env
    client, session, csrf = _cashier_client(env)
    product.sale_price = Decimal("3800")
    db.session.commit()
    result = client.post(f"/bars/{bar.id}/cashier/workspace", data={
        "csrf_token": csrf, "action": "create_sale",
        "reference": "COUNTER-BM-INVALID",
        "product_id": str(product.id), "quantity": "1",
        "cash_presented": "5000", "cash_receipt_source": "CLIENT",
        "cash_surplus_action": "CHANGE_VOUCHER",
        "cash_actual_change": "1300",
    }, headers=_async_headers(csrf))
    assert result.status_code == 422
    assert db.session.scalar(select(Order.id).where(
        Order.bar_id == bar.id, Order.reference == "COUNTER-BM-INVALID"
    )) is None
    assert cash_service.expected(session) == Decimal("1000")
