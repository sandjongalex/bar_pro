"""Optimistic concurrency helpers for active-order workflows.

Database row locks protect the transaction while a write is in progress.  The
revision token below protects the user *before* that transaction: if two devices
opened the same order and one of them changes it first, the second device cannot
silently overwrite the newer state with an old form.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import hmac

from app.finance_totals import order_balance
from app.order_line_views import effective_lines_by_order


def _decimal_text(value) -> str:
    number = Decimal(value or 0)
    if not number.is_finite():
        return str(number)
    return format(number.normalize(), "f")


def order_revision(order) -> str:
    """Return a stable fingerprint of the editable/financial state of an order."""
    lines = effective_lines_by_order(order.bar_id, [order.id]).get(order.id, [])
    balance = order_balance(order)

    parts = [
        "order-revision-v1",
        str(order.id),
        str(order.status or ""),
        str(order.payment_status or ""),
        _decimal_text(order.subtotal_amount),
        _decimal_text(order.discount_amount),
        _decimal_text(order.tax_amount),
        _decimal_text(order.total_amount),
        _decimal_text(balance["net_settled"]),
    ]
    for line in sorted(lines, key=lambda item: (int(item["product_id"]), int(item["id"]))):
        parts.extend(
            [
                str(line["id"]),
                str(line["product_id"]),
                _decimal_text(line["quantity"]),
                _decimal_text(line["unit_sale_price_snapshot"]),
            ]
        )

    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def revision_matches(order, expected_revision: str | None) -> bool:
    if not expected_revision:
        return False
    return hmac.compare_digest(order_revision(order), str(expected_revision).strip())
