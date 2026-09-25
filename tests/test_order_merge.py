from decimal import Decimal

import pytest
from sqlalchemy import select

from app.extensions import db
from app.models import Payment, StockBalance
from app.order_merge_service import order_merge_service
from app.order_services import order_service
from test_workflows import env


def test_grouped_payment_allocates_oldest_first_without_moving_stock(env):
    _, owner, bar, _, product, server, _, _ = env

    first = order_service.create(
        server,
        bar.id,
        "MERGE-001",
        [{"product_id": product.id, "quantity": 1}],
    )
    second = order_service.create(
        server,
        bar.id,
        "MERGE-002",
        [{"product_id": product.id, "quantity": 2}],
    )
    order_service.confirm(owner, bar.id, first.id)
    order_service.confirm(owner, bar.id, second.id)
    db.session.commit()

    stock = db.session.scalar(
        select(StockBalance).where(
            StockBalance.bar_id == bar.id,
            StockBalance.product_id == product.id,
        )
    )
    quantity_after_delivery = Decimal(stock.quantity)

    result = order_merge_service.record_payment(
        owner,
        bar.id,
        [second.id, first.id],
        "CARD",
        150,
        reference="FUS-TEST",
    )
    db.session.commit()

    assert result["amount"] == Decimal("150")
    assert [item["order_id"] for item in result["allocations"]] == [first.id, second.id]
    assert [item["amount"] for item in result["allocations"]] == [Decimal("100"), Decimal("50")]

    db.session.refresh(first)
    db.session.refresh(second)
    assert first.payment_status == "PAID"
    assert second.payment_status == "PARTIAL"

    payments = list(
        db.session.scalars(
            select(Payment)
            .where(Payment.bar_id == bar.id, Payment.reference.like("FUS-TEST-%"))
            .order_by(Payment.id)
        )
    )
    assert [item.amount_applied for item in payments] == [Decimal("100"), Decimal("50")]

    db.session.refresh(stock)
    assert Decimal(stock.quantity) == quantity_after_delivery


def test_grouped_payment_requires_two_distinct_orders(env):
    _, owner, bar, _, product, server, _, _ = env
    order = order_service.create(
        server,
        bar.id,
        "MERGE-SINGLE",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, order.id)
    db.session.commit()

    with pytest.raises(ValueError, match="MERGE_REQUIRES_MULTIPLE_ORDERS"):
        order_merge_service.summary(owner, bar.id, [order.id, order.id])
