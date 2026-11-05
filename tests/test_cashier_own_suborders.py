"""Cashier sub-orders must work on the cashier's own invoices, not a colleague's."""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.cash_services import cash_service
from app.extensions import db
from app.models import (
    OrderLine, Payment, StaffAssignment, StockBalance, StockMovement, User, utcnow,
)
from app.order_services import order_service
from app.order_suborder_models import OrderSuborder, OrderSuborderLine
from app.order_suborder_service import order_suborder_service
from app.payment_services import payment_service
from app.shift_service import start_shift
from test_cashier_workspace_async import _cashier, _csrf, _login
from test_workflows import env


def test_cashier_can_add_suborders_to_own_counter_invoice_but_not_colleagues(env):
    app, owner, bar, _, product, _, _, _ = env
    cashier_a = _cashier(bar)
    cash_session = cash_service.open(cashier_a, bar.id, "OWN-SUB-CASH", 0)
    parent = order_service.create(
        cashier_a, bar.id, "OWN-SUB-PARENT",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Ma facture",
    )
    order_service.confirm(cashier_a, bar.id, parent.id)
    db.session.commit()

    cashier_b = User(
        email="other-own-sub-cashier@example.invalid",
        display_name="Autre caisse", category="EMPLOYEE",
    )
    cashier_b.set_password("test-password")
    db.session.add(cashier_b)
    db.session.flush()
    assignment_b = StaffAssignment(
        bar_id=bar.id, user_id=cashier_b.id,
        role="CASHIER", started_at=utcnow(),
    )
    db.session.add(assignment_b)
    db.session.flush()
    start_shift(owner, bar.id, assignment_b.id)
    db.session.commit()

    url = f"/bars/{bar.id}/orders/{parent.id}/suborders/new"
    workspace_url = f"/bars/{bar.id}/cashier/workspace?order_id={parent.id}"
    client_a = app.test_client()
    assert _login(client_a, cashier_a.email).status_code == 302

    workspace = client_a.get(workspace_url)
    assert workspace.status_code == 200
    assert url in workspace.text
    assert "Ajouter une sous-commande" in workspace.text
    detail = client_a.get(f"/bars/{bar.id}/orders/{parent.id}/detail")
    assert detail.status_code == 200
    assert url in detail.text

    builder = client_a.get(url)
    assert builder.status_code == 200
    assert "Caissière · ma facture" in builder.text
    assert "Ajout direct sur votre facture" in builder.text
    token = _csrf(builder)
    created = client_a.post(
        url, data={
            "csrf_token": token,
            "product_id": str(product.id),
            "quantity": "2",
            "note": "Ajout comptoir",
        }, follow_redirects=False,
    )
    assert created.status_code == 302

    suborder = db.session.scalar(
        select(OrderSuborder).where(
            OrderSuborder.bar_id == bar.id, OrderSuborder.order_id == parent.id,
        )
    )
    assert suborder is not None
    assert suborder.status == "VALIDATED"
    assert suborder.delivery_status == "DELIVERED"
    assert suborder.assigned_staff_id is None
    assert suborder.created_by_id == cashier_a.id
    assert suborder.validated_by_id == cashier_a.id
    assert suborder.delivered_by_id == cashier_a.id
    assert suborder.validated_at is not None
    assert suborder.delivered_at is not None
    assert suborder.total_amount == Decimal("200")

    db.session.refresh(parent)
    assert parent.total_amount == Decimal("300")
    assert parent.payment_status == "UNPAID"
    original_line = db.session.scalar(
        select(OrderLine).where(OrderLine.bar_id == bar.id, OrderLine.order_id == parent.id)
    )
    assert original_line.quantity == Decimal("1")
    sub_line = db.session.scalar(
        select(OrderSuborderLine).where(
            OrderSuborderLine.bar_id == bar.id,
            OrderSuborderLine.order_suborder_id == suborder.id,
        )
    )
    assert sub_line.quantity == Decimal("2")
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id, StockBalance.product_id == product.id,
        )
    ) == Decimal("7")
    assert db.session.scalar(
        select(func.count(StockMovement.id)).where(
            StockMovement.bar_id == bar.id,
            StockMovement.product_id == product.id,
            StockMovement.movement_type == "SALE",
        )
    ) == 2

    live = client_a.get(
        f"/bars/{bar.id}/orders/{parent.id}/suborders/data"
    )
    assert live.status_code == 200
    after = client_a.get(workspace_url)
    assert after.status_code == 200
    assert "300 XAF" in after.text
    assert "Sous-commande n°1" in after.text
    assert client_a.get(
        f"/bars/{bar.id}/orders/{parent.id}/detail"
    ).status_code == 200

    client_b = app.test_client()
    assert _login(client_b, cashier_b.email).status_code == 302
    assert client_b.get(url).status_code == 404
    assert client_b.get(
        f"/bars/{bar.id}/orders/{parent.id}/suborders/data"
    ).status_code == 404
    forbidden = client_b.post(
        url, data={
            "csrf_token": _csrf(client_b.get(f"/bars/{bar.id}/cashier/workspace")),
            "product_id": str(product.id), "quantity": "1",
        }
    )
    assert forbidden.status_code == 404
    assert db.session.scalar(
        select(func.count(OrderSuborder.id)).where(
            OrderSuborder.bar_id == bar.id, OrderSuborder.order_id == parent.id,
        )
    ) == 1

    # Paid invoices are immutable: no further addition through the UI or service.
    payment_service.record(
        cashier_a, bar.id, parent.id, "OWN-SUB-PAID",
        "CASH", 300, 300, cash_session.id,
    )
    db.session.commit()
    db.session.refresh(parent)
    assert parent.payment_status == "PAID"
    assert client_a.get(url).status_code == 404
    with pytest.raises(ValueError, match="ORDER_PAID"):
        order_suborder_service.create_cashier_addition(
            cashier_a, bar.id, parent.id,
            [{"product_id": product.id, "quantity": 1}],
        )
    db.session.rollback()
