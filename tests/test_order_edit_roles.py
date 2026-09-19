from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.finance_totals import order_balance
from app.models import OrderLine, OrderReturnLine, Product, StaffAssignment, StockBalance, User, utcnow
from app.order_edit_service import order_edit_service
from app.order_services import order_service
from app.payment_services import payment_service
from app.stock_service import stock_service
from test_workflows import env


def _cashier(bar):
    user = User(email="edit-cashier@example.invalid", display_name="Cashier Edit", category="EMPLOYEE")
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=user.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.commit()
    return user


def _second_product(env, quantity=5):
    _, owner, bar, _, product, _, _, _ = env
    second = Product(
        bar_id=bar.id,
        category_id=product.category_id,
        name="Juice",
        sku="JUICE-EDIT",
        base_unit="bottle",
        sale_price=250,
        valuation_unit_cost=80,
    )
    db.session.add(second)
    db.session.flush()
    stock_service.move(owner, bar.id, second.id, "INITIAL", quantity, "Edit test stock")
    db.session.commit()
    return second


def _stock(bar_id, product_id):
    value = db.session.scalar(
        select(StockBalance.quantity).where(
            StockBalance.bar_id == bar_id,
            StockBalance.product_id == product_id,
        )
    )
    return Decimal(value or 0)


def test_server_can_replace_own_draft_products(env):
    _, _, bar, _, product, server, _, _ = env
    second = _second_product(env)
    order = order_service.create(
        server,
        bar.id,
        "SERVER-EDIT-DRAFT",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.commit()

    order_edit_service.edit(
        server,
        bar.id,
        order.id,
        [{"product_id": second.id, "quantity": 1}],
    )
    db.session.commit()

    lines = list(db.session.scalars(select(OrderLine).where(OrderLine.order_id == order.id)))
    assert len(lines) == 1
    assert lines[0].product_id == second.id
    assert lines[0].quantity == 1
    assert _stock(bar.id, product.id) == 10
    assert _stock(bar.id, second.id) == 5


def test_server_cannot_edit_after_delivery(env):
    _, owner, bar, _, product, server, _, _ = env
    order = order_service.create(
        server,
        bar.id,
        "SERVER-EDIT-DELIVERED",
        [{"product_id": product.id, "quantity": 2}],
    )
    order_service.confirm(owner, bar.id, order.id)
    db.session.commit()

    with pytest.raises(PermissionError):
        order_edit_service.edit(
            server,
            bar.id,
            order.id,
            [{"product_id": product.id, "quantity": 1}],
        )
    db.session.rollback()
    assert _stock(bar.id, product.id) == 8


def test_cashier_can_replace_products_on_delivered_unpaid_order(env):
    _, _, bar, _, product, server, _, _ = env
    cashier = _cashier(bar)
    second = _second_product(env)
    order = order_service.create(
        server,
        bar.id,
        "CASHIER-EDIT-DELIVERED",
        [{"product_id": product.id, "quantity": 2}],
    )
    order_service.confirm(cashier, bar.id, order.id)
    db.session.commit()

    order_edit_service.edit(
        cashier,
        bar.id,
        order.id,
        [{"product_id": second.id, "quantity": 1}],
        "Le client remplace les eaux par un jus",
    )
    db.session.commit()

    assert _stock(bar.id, product.id) == 10
    assert _stock(bar.id, second.id) == 4
    assert db.session.scalar(select(OrderReturnLine).where(OrderReturnLine.order_id == order.id)) is not None
    assert order_balance(order)["net_sale"] == Decimal("250.0000")
    assert order.payment_status == "UNPAID"


def test_cashier_cannot_edit_fully_paid_order(env):
    _, _, bar, _, product, server, _, _ = env
    cashier = _cashier(bar)
    order = order_service.create(
        server,
        bar.id,
        "CASHIER-EDIT-PAID",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(cashier, bar.id, order.id)
    payment_service.record(cashier, bar.id, order.id, "EDIT-PAID-PAYMENT", "CARD", 100, 100)
    db.session.commit()
    assert order.payment_status == "PAID"

    with pytest.raises(ValueError, match="ORDER_PAID"):
        order_edit_service.edit(
            cashier,
            bar.id,
            order.id,
            [{"product_id": product.id, "quantity": 2}],
        )
    db.session.rollback()
    assert _stock(bar.id, product.id) == 9


def test_partial_payment_cannot_be_reduced_below_settled_amount(env):
    _, _, bar, _, product, server, _, _ = env
    cashier = _cashier(bar)
    second = _second_product(env)
    order = order_service.create(
        server,
        bar.id,
        "CASHIER-EDIT-PARTIAL",
        [{"product_id": product.id, "quantity": 2}],
    )
    order_service.confirm(cashier, bar.id, order.id)
    payment_service.record(cashier, bar.id, order.id, "EDIT-PARTIAL-PAYMENT", "CARD", 150, 150)
    db.session.commit()
    assert order.payment_status == "PARTIAL"

    with pytest.raises(ValueError, match="ORDER_TOTAL_BELOW_SETTLED"):
        order_edit_service.edit(
            cashier,
            bar.id,
            order.id,
            [{"product_id": product.id, "quantity": 1}],
        )
    db.session.rollback()

    order_edit_service.edit(
        cashier,
        bar.id,
        order.id,
        [{"product_id": second.id, "quantity": 1}],
        "Remplacement après acompte",
    )
    db.session.commit()
    assert order_balance(order)["net_sale"] == Decimal("250.0000")
    assert order.payment_status == "PARTIAL"
