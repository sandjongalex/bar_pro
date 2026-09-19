import re

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import OrderLine, Payment, Refund, StaffAssignment, User, utcnow
from app.order_services import order_service
from app.payment_services import payment_service
from test_workflows import env


def _csrf_from(page):
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf_from(page)},
        follow_redirects=False,
    )


def _cashier(bar):
    cashier = User(email="cashier-flow@example.invalid", display_name="Cashier Flow", category="EMPLOYEE")
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=cashier.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.commit()
    return cashier


def test_cashier_order_and_checkout_entries_use_workspace(env):
    app, _, bar, _, _, _, _, _ = env
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "ENTRY-CASH", 0)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    orders_entry = client.get(f"/bars/{bar.id}/orders/new", follow_redirects=False)
    assert orders_entry.status_code == 302
    assert f"/bars/{bar.id}/cashier/workspace" in orders_entry.headers["Location"]
    assert "sale=1" in orders_entry.headers["Location"]

    checkout_entry = client.get(f"/bars/{bar.id}/checkout", follow_redirects=False)
    assert checkout_entry.status_code == 302
    assert f"/bars/{bar.id}/cashier/workspace" in checkout_entry.headers["Location"]


def test_complete_cashier_flow_server_cash_handover_return_history_receipt_close(env):
    app, owner, bar, _, product, server_user, _, _ = env
    cashier = _cashier(bar)
    server_assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == server_user.id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    assert server_assignment is not None

    session = cash_service.open(cashier, bar.id, "FLOW-CASH", 5000)

    order = order_service.create(
        owner,
        bar.id,
        "FLOW-ORDER",
        [{"product_id": product.id, "quantity": 2}],
    )
    order.assigned_staff_id = server_assignment.id
    order_service.confirm(cashier, bar.id, order.id)
    db.session.commit()

    payment = payment_service.record(
        cashier,
        bar.id,
        order.id,
        "FLOW-PAY-CASH",
        "CASH",
        200,
        200,
        0,
        staff_assignment_id=server_assignment.id,
    )
    db.session.commit()

    assert payment.cash_holder == "STAFF"
    assert payment.staff_assignment_id == server_assignment.id
    assert cash_service.custody(bar.id, server_assignment.id) == 200
    assert cash_service.expected(session) == 5000

    handover = cash_service.handover(
        cashier,
        bar.id,
        server_assignment.id,
        session.id,
        "FLOW-HANDOVER",
        200,
    )
    db.session.flush()
    cash_service.transition_handover(cashier, bar.id, handover.id)
    db.session.commit()

    assert handover.status == "POSTED"
    assert cash_service.custody(bar.id, server_assignment.id) == 0
    assert cash_service.expected(session) == 5200

    line = db.session.scalar(
        select(OrderLine).where(OrderLine.bar_id == bar.id, OrderLine.order_id == order.id)
    )
    returned = order_service.return_lines(
        cashier,
        bar.id,
        order.id,
        [{"order_line_id": line.id, "quantity": 1, "disposition": "RESTOCK"}],
        "Produit retourné",
    )
    db.session.flush()

    refund = payment_service.refund(
        cashier,
        bar.id,
        payment.id,
        "FLOW-REFUND",
        100,
        "Remboursement retour",
        order_return_id=returned.id,
        cash_session_id=session.id,
    )
    db.session.commit()

    assert refund.cash_holder == "DRAWER"
    assert refund.amount == 100
    assert cash_service.expected(session) == 5100
    assert db.session.scalar(select(Payment).where(Payment.reference == "FLOW-PAY-CASH")) is not None
    assert db.session.scalar(select(Refund).where(Refund.reference == "FLOW-REFUND")) is not None

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    checkout_redirect = client.get(f"/bars/{bar.id}/checkout", follow_redirects=False)
    assert checkout_redirect.status_code == 302
    assert f"/bars/{bar.id}/cashier/workspace" in checkout_redirect.headers["Location"]
    workspace = client.get(checkout_redirect.headers["Location"])
    assert workspace.status_code == 200
    assert "Poste de caisse" in workspace.text

    handovers = client.get(f"/bars/{bar.id}/cashier-handovers")
    assert handovers.status_code == 200
    assert "FLOW-HANDOVER" in handovers.text
    assert "Reçue" in handovers.text

    history = client.get(
        f"/bars/{bar.id}/cashier-history?method=CASH&q=FLOW-ORDER"
    )
    assert history.status_code == 200
    assert "FLOW-PAY-CASH" in history.text
    assert "FLOW-REFUND" in history.text
    assert "100" in history.text

    receipt = client.get(f"/bars/{bar.id}/checkout/orders/{order.id}/receipt")
    assert receipt.status_code == 200
    assert "FLOW-ORDER" in receipt.text
    assert "FLOW-PAY-CASH" in receipt.text
    assert "FLOW-REFUND" in receipt.text

    cash_service.close(cashier, bar.id, session.id, 5100)
    db.session.commit()
    db.session.refresh(session)
    assert session.status == "CLOSED"
    assert session.expected_closing_amount == 5100
    assert session.closing_difference == 0
