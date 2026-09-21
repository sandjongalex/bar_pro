from decimal import Decimal

from sqlalchemy import select

from app.extensions import db
from app.models import OrderLine, StaffAssignment, StockBalance, User, utcnow
from app.order_services import order_service
from app.order_suborder_models import OrderSuborderLine
from app.order_suborder_service import order_suborder_service
from test_workflows import env


def test_cashier_creates_delivered_pending_suborder_without_merging_parent_lines(env):
    _, _, bar, _, product, server, _, _ = env

    cashier = User(
        email="suborder-cashier@example.invalid",
        display_name="Suborder Cashier",
        category="EMPLOYEE",
    )
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
    db.session.flush()

    parent = order_service.create(
        server,
        bar.id,
        "SUBORDER-PARENT",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, parent.id)
    db.session.commit()

    original_line = db.session.scalar(
        select(OrderLine).where(OrderLine.bar_id == bar.id, OrderLine.order_id == parent.id)
    )
    server_assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == server.id,
            StaffAssignment.ended_at.is_(None),
        )
    )
    assert parent.total_amount == Decimal("200")
    assert original_line.quantity == Decimal("2")
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    ) == Decimal("8")

    suborder = order_suborder_service.create_cashier_addition(
        cashier,
        bar.id,
        parent.id,
        [{"product_id": product.id, "quantity": 2}],
        note="Deux bouteilles ajoutées à la table",
    )
    db.session.commit()

    line = db.session.scalar(
        select(OrderSuborderLine).where(
            OrderSuborderLine.bar_id == bar.id,
            OrderSuborderLine.order_suborder_id == suborder.id,
        )
    )
    db.session.refresh(parent)
    db.session.refresh(original_line)

    assert suborder.sequence_no == 1
    assert suborder.status == "PENDING_VALIDATION"
    assert suborder.delivery_status == "DELIVERED"
    assert suborder.assigned_staff_id == server_assignment.id
    assert suborder.created_by_id == cashier.id
    assert suborder.delivered_by_id == cashier.id
    assert suborder.total_amount == Decimal("200")

    # The historical parent lines stay untouched: this is a real sub-order,
    # not a quantity merge into the original invoice block.
    assert parent.total_amount == Decimal("200")
    assert original_line.quantity == Decimal("2")
    assert line.product_id == product.id
    assert line.quantity == Decimal("2")
    assert line.total_amount == Decimal("200")

    # The cashier has physically served the addition, so stock moves now even
    # though financial validation by the assigned server is still pending.
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    ) == Decimal("6")
