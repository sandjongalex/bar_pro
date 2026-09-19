"""Read effective order lines after posted returns without mutating history."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select

from app.extensions import db
from app.models import OrderLine, OrderReturn, OrderReturnLine


def effective_lines_by_order(bar_id: int, order_ids: list[int]) -> dict[int, list[dict]]:
    """Return the currently effective lines for each order.

    Delivered-order reductions are stored as posted returns. This helper subtracts
    those returned quantities from the original line while preserving the immutable
    historical row in the database.
    """
    result = {order_id: [] for order_id in order_ids}
    if not order_ids:
        return result

    returned_rows = db.session.execute(
        select(
            OrderReturnLine.order_line_id,
            func.coalesce(func.sum(OrderReturnLine.quantity), 0),
        )
        .join(OrderReturn, OrderReturn.id == OrderReturnLine.order_return_id)
        .where(
            OrderReturnLine.bar_id == bar_id,
            OrderReturnLine.order_id.in_(order_ids),
            OrderReturn.status == "POSTED",
        )
        .group_by(OrderReturnLine.order_line_id)
    ).all()
    returned_by_line = {
        line_id: Decimal(quantity or 0)
        for line_id, quantity in returned_rows
    }

    for line in db.session.scalars(
        select(OrderLine)
        .where(OrderLine.bar_id == bar_id, OrderLine.order_id.in_(order_ids))
        .order_by(OrderLine.order_id, OrderLine.line_no, OrderLine.id)
    ):
        quantity = Decimal(line.quantity) - returned_by_line.get(line.id, Decimal("0"))
        if quantity <= 0:
            continue
        unit_price = Decimal(line.unit_sale_price_snapshot)
        result.setdefault(line.order_id, []).append(
            {
                "id": line.id,
                "order_id": line.order_id,
                "product_id": line.product_id,
                "product_name_snapshot": line.product_name_snapshot,
                "unit_snapshot": line.unit_snapshot,
                "quantity": quantity,
                "unit_sale_price_snapshot": unit_price,
                "total_amount": quantity * unit_price,
            }
        )
    return result
