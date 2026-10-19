"""Read-only snapshots of validated additions for the cashier queue.

Keep original OrderLine and OrderSuborderLine rows separate. This read model
never records delivery, mutates stock or recomputes invoice totals.
"""
from __future__ import annotations

from sqlalchemy import select

from app.extensions import db
from app.order_suborder_models import OrderSuborder, OrderSuborderLine


def cashier_rounds_by_order(bar_id: int, order_ids: list[int]) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {order_id: [] for order_id in order_ids}
    if not order_ids:
        return result

    suborders = list(db.session.scalars(
        select(OrderSuborder)
        .where(
            OrderSuborder.bar_id == bar_id,
            OrderSuborder.order_id.in_(order_ids),
            OrderSuborder.status == "VALIDATED",
            OrderSuborder.delivery_status.in_(["PENDING", "DELIVERED"]),
        )
        .order_by(OrderSuborder.order_id, OrderSuborder.sequence_no, OrderSuborder.id)
    ))
    if not suborders:
        return result

    by_suborder = {item.id: [] for item in suborders}
    for line in db.session.scalars(
        select(OrderSuborderLine)
        .where(
            OrderSuborderLine.bar_id == bar_id,
            OrderSuborderLine.order_suborder_id.in_([item.id for item in suborders]),
        )
        .order_by(OrderSuborderLine.order_suborder_id, OrderSuborderLine.line_no, OrderSuborderLine.id)
    ):
        by_suborder[line.order_suborder_id].append({
            "name": line.product_name_snapshot,
            "quantity": line.quantity,
            "amount": line.total_amount,
        })

    for item in suborders:
        result[item.order_id].append({
            "id": item.id,
            "order_id": item.order_id,
            "sequence_no": item.sequence_no,
            "delivery_status": item.delivery_status,
            "total_amount": item.total_amount,
            "currency": item.currency,
            "note": item.note or "",
            "lines": by_suborder[item.id],
        })
    return result


def pending_rounds(rounds: list[dict]) -> bool:
    return any(round_item["delivery_status"] == "PENDING" for round_item in rounds)
