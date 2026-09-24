from decimal import Decimal
import re

import pytest
from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import Payment, StaffAssignment, StockBalance, User, utcnow
from app.order_services import order_service
from app.order_suborder_models import OrderSuborder
from app.order_suborder_service import order_suborder_service
from app.payment_services import payment_service
from app.shift_service import start_shift
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def _cashier(owner, bar, server_assignment):
    user = User(
        email="delivery-confirm-cashier@example.invalid",
        display_name="Alex Caisse",
        category="EMPLOYEE",
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id,
        user_id=user.id,
        role="CASHIER",
        started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    start_shift(owner, bar.id, assignment.id)
    start_shift(user, bar.id, server_assignment.id)
    db.session.commit()
    return user, assignment


def test_server_addition_requires_cashier_delivery_confirmation_before_payment(env):
    app, owner, bar, _, product, server, _, _ = env
    server_assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == server.id,
            StaffAssignment.role == "SERVER",
            StaffAssignment.ended_at.is_(None),
        )
    )
    assert server_assignment is not None
    cashier, _ = _cashier(owner, bar, server_assignment)

    order = order_service.create(
        server,
        bar.id,
        "DELIVERY-CONFIRM-PARENT",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, order.id)
    db.session.commit()

    stock_after_initial_delivery = db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    )
    assert stock_after_initial_delivery == Decimal("9")

    addition = order_suborder_service.create_server_addition(
        server,
        bar.id,
        order.id,
        [{"product_id": product.id, "quantity": 1}],
        note="Une bouteille ajoutée après la première livraison",
    )
    db.session.commit()
    db.session.refresh(order)

    assert addition.status == "VALIDATED"
    assert addition.delivery_status == "PENDING"
    assert order.total_amount == Decimal("200")
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    ) == Decimal("9")

    # Even a privileged caller cannot bypass the physical-delivery acknowledgement.
    with pytest.raises(ValueError, match="ORDER_NOT_PAYABLE"):
        payment_service.record(
            owner,
            bar.id,
            order.id,
            "PAY-BEFORE-DELIVERY",
            "CARD",
            200,
            200,
        )
    db.session.rollback()
    assert db.session.scalar(
        select(Payment.id).where(
            Payment.bar_id == bar.id,
            Payment.order_id == order.id,
        )
    ) is None

    cash_service.open(cashier, bar.id, "DELIVERY-CONFIRM-CASH", 0)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/unpaid-orders")
    assert page.status_code == 200
    assert "NOUVEL AJOUT DE LA SERVEUSE" in page.text
    assert "Confirmer que j&#39;ai livré" in page.text
    assert "Livraison à confirmer" in page.text
    assert "Une bouteille ajoutée après la première livraison" in page.text

    payload = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    row = next(item for item in payload["orders"] if item["id"] == order.id)
    assert payload["stats"]["pending_delivery"] == 1
    assert row["payment_blocked"] is True
    assert row["action_url"] is None
    assert len(row["pending_deliveries"]) == 1
    assert row["pending_deliveries"][0]["id"] == addition.id
    assert row["pending_deliveries"][0]["confirm_url"]

    response = client.post(
        f"/bars/{bar.id}/unpaid-orders/{order.id}/suborders/{addition.id}/confirm-delivery",
        data={"csrf_token": _csrf(page), "staff": "all"},
        follow_redirects=False,
    )
    assert response.status_code == 302

    db.session.refresh(addition)
    assert addition.delivery_status == "DELIVERED"
    assert addition.delivered_by_id == cashier.id
    assert addition.delivered_at is not None
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    ) == Decimal("8")

    refreshed = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    row = next(item for item in refreshed["orders"] if item["id"] == order.id)
    assert refreshed["stats"]["pending_delivery"] == 0
    assert row["payment_blocked"] is False
    assert row["pending_deliveries"] == []
    assert row["action_url"] and "cashier/workspace" in row["action_url"]
    assert len(row["lines"]) == 2

    payment_service.record(
        owner,
        bar.id,
        order.id,
        "PAY-AFTER-DELIVERY",
        "CARD",
        200,
        200,
    )
    db.session.commit()
    db.session.refresh(order)
    assert order.payment_status == "PAID"
