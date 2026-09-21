from decimal import Decimal

import pytest
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


def test_assigned_server_validates_suborder_and_it_stays_separate(env):
    _, _, bar, _, product, server, _, _ = env

    cashier = User(
        email="suborder-validation-cashier@example.invalid",
        display_name="Validation Cashier",
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
        "SUBORDER-VALIDATION",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, parent.id)
    db.session.commit()

    original_line = db.session.scalar(
        select(OrderLine).where(OrderLine.bar_id == bar.id, OrderLine.order_id == parent.id)
    )
    suborder = order_suborder_service.create_cashier_addition(
        cashier,
        bar.id,
        parent.id,
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.commit()

    validated = order_suborder_service.validate_by_server(
        server,
        bar.id,
        parent.id,
        suborder.id,
    )
    db.session.commit()

    suborder_line = db.session.scalar(
        select(OrderSuborderLine).where(
            OrderSuborderLine.bar_id == bar.id,
            OrderSuborderLine.order_suborder_id == suborder.id,
        )
    )
    db.session.refresh(parent)
    db.session.refresh(original_line)

    assert validated.status == "VALIDATED"
    assert validated.validated_by_id == server.id
    assert validated.validated_at is not None
    assert validated.delivery_status == "DELIVERED"

    # The invoice total now includes the validated addition, while the historical
    # product blocks stay separate instead of becoming quantity 4 on one line.
    assert parent.total_amount == Decimal("400")
    assert original_line.quantity == Decimal("2")
    assert suborder_line.quantity == Decimal("2")
    assert suborder_line.order_suborder_id == suborder.id

    # Validation does not serve the products a second time.
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    ) == Decimal("6")

    with pytest.raises(ValueError, match="SUBORDER_NOT_PENDING"):
        order_suborder_service.validate_by_server(
            server,
            bar.id,
            parent.id,
            suborder.id,
        )
    db.session.rollback()
    db.session.refresh(parent)
    assert parent.total_amount == Decimal("400")


def test_server_creates_multiple_suborders_that_wait_for_cashier_delivery(env):
    _, _, bar, _, product, server, _, _ = env

    cashier = User(
        email="server-suborder-cashier@example.invalid",
        display_name="Server Addition Cashier",
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
        "SERVER-SUBORDER-PARENT",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.flush()
    order_service.confirm(cashier, bar.id, parent.id)
    db.session.commit()

    original_line = db.session.scalar(
        select(OrderLine).where(OrderLine.bar_id == bar.id, OrderLine.order_id == parent.id)
    )
    stock_before_additions = db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    )
    assert stock_before_additions == Decimal("8")
    assert parent.total_amount == Decimal("200")

    first = order_suborder_service.create_server_addition(
        server,
        bar.id,
        parent.id,
        [{"product_id": product.id, "quantity": 1}],
        note="Premier ajout demandé par la table",
    )
    db.session.commit()

    second = order_suborder_service.create_server_addition(
        server,
        bar.id,
        parent.id,
        [{"product_id": product.id, "quantity": 2}],
        note="Deuxième ajout demandé par la table",
    )
    db.session.commit()

    first_line = db.session.scalar(
        select(OrderSuborderLine).where(
            OrderSuborderLine.bar_id == bar.id,
            OrderSuborderLine.order_suborder_id == first.id,
        )
    )
    second_line = db.session.scalar(
        select(OrderSuborderLine).where(
            OrderSuborderLine.bar_id == bar.id,
            OrderSuborderLine.order_suborder_id == second.id,
        )
    )
    db.session.refresh(parent)
    db.session.refresh(original_line)

    assert first.sequence_no == 1
    assert second.sequence_no == 2
    assert first.status == second.status == "VALIDATED"
    assert first.delivery_status == second.delivery_status == "PENDING"
    assert first.created_by_id == second.created_by_id == server.id
    assert first.validated_by_id == second.validated_by_id == server.id
    assert first.validated_at is not None
    assert second.validated_at is not None

    # Each round remains a separate block; the original order line is untouched.
    assert original_line.quantity == Decimal("2")
    assert first_line.quantity == Decimal("1")
    assert second_line.quantity == Decimal("2")
    assert first_line.order_suborder_id != second_line.order_suborder_id

    # The invoice records all ordered rounds immediately, but the cashier has not
    # delivered these two additions yet, so their stock is still untouched.
    assert parent.total_amount == Decimal("500")
    assert db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    ) == Decimal("8")
