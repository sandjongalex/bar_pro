"""Grouped checkout for several already-delivered orders.

A merge is intentionally a payment view, not a mutation of historical order
lines. Stock has already moved when each order was delivered, so grouped
checkout only validates compatible orders and allocates one customer payment
across their existing balances.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from sqlalchemy import select

from app.extensions import db
from app.finance_totals import order_balance
from app.models import Order
from app.order_suborder_models import OrderSuborder
from app.payment_services import payment_service
from app.permissions import permissions
from app.validation import number


class OrderMergeService:
    def _reference(self, prefix="FUS") -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"

    def orders(self, actor, bar_id: int, order_ids) -> list[Order]:
        """Return a locked, oldest-first list of merge-compatible orders."""
        permissions.require(actor, "payments.read", bar_id)
        try:
            ids = [int(value) for value in order_ids]
        except (TypeError, ValueError):
            raise ValueError("INVALID_MERGE_SELECTION") from None
        ids = list(dict.fromkeys(ids))
        if len(ids) < 2:
            raise ValueError("MERGE_REQUIRES_MULTIPLE_ORDERS")

        orders = list(
            db.session.scalars(
                select(Order)
                .where(Order.bar_id == bar_id, Order.id.in_(ids))
                .order_by(Order.posted_at.asc(), Order.id.asc())
                .with_for_update()
            )
        )
        if len(orders) != len(ids):
            raise LookupError("NOT_FOUND")

        currencies = {order.currency for order in orders}
        if len(currencies) != 1:
            raise ValueError("MERGE_CURRENCY_MISMATCH")
        for order in orders:
            if order.status not in {"CONFIRMED", "SERVED"}:
                raise ValueError("ORDER_NOT_PAYABLE")
            if order.payment_status not in {"UNPAID", "PARTIAL"}:
                raise ValueError("ORDER_ALREADY_PAID")
            pending = db.session.scalar(
                select(OrderSuborder.id)
                .where(
                    OrderSuborder.bar_id == bar_id,
                    OrderSuborder.order_id == order.id,
                    OrderSuborder.status == "VALIDATED",
                    OrderSuborder.delivery_status == "PENDING",
                )
                .limit(1)
            )
            if pending is not None:
                raise ValueError("ORDER_NOT_PAYABLE")
        return orders

    def summary(self, actor, bar_id: int, order_ids) -> dict:
        orders = self.orders(actor, bar_id, order_ids)
        rows = []
        total_due = Decimal("0")
        for order in orders:
            due = Decimal(order_balance(order)["amount_due"] or 0)
            if due <= 0:
                raise ValueError("ORDER_ALREADY_PAID")
            total_due += due
            rows.append({"order": order, "amount_due": due})
        return {
            "orders": rows,
            "total_due": number(total_due),
            "currency": orders[0].currency,
        }

    def record_payment(
        self,
        actor,
        bar_id: int,
        order_ids,
        method: str,
        applied,
        *,
        presented=None,
        cash_session_id=None,
        provider_code=None,
        provider_transaction_id=None,
        reference=None,
    ) -> dict:
        """Allocate one grouped payment oldest-first without touching stock."""
        permissions.require(actor, "payments.record", bar_id)
        orders = self.orders(actor, bar_id, order_ids)
        amount = number(applied)
        if amount <= 0:
            raise ValueError("INVALID_PAYMENT_AMOUNTS")
        total_due = sum(
            (Decimal(order_balance(order)["amount_due"] or 0) for order in orders),
            Decimal("0"),
        )
        if amount > total_due:
            raise ValueError("PAYMENT_LIMIT_EXCEEDED")

        method = (method or "").strip().upper()
        if method not in {"CASH", "CARD", "MOBILE_MONEY", "BANK_TRANSFER"}:
            raise ValueError("INVALID_METHOD")
        if method == "MOBILE_MONEY" and (not provider_code or not provider_transaction_id):
            raise ValueError("PROVIDER_REFERENCE_REQUIRED")

        if method == "CASH":
            presented_amount = number(presented)
            if presented_amount < amount:
                raise ValueError("INVALID_PAYMENT_AMOUNTS")
            if not cash_session_id:
                raise ValueError("CASH_LOCATION_REQUIRED")
            overall_change = presented_amount - amount
        else:
            overall_change = Decimal("0")
            cash_session_id = None

        base_reference = (reference or "").strip() or self._reference()
        remaining = Decimal(amount)
        allocations = []
        payable = []
        for order in orders:
            due = Decimal(order_balance(order)["amount_due"] or 0)
            if due > 0:
                payable.append((order, due))

        for index, (order, due) in enumerate(payable, 1):
            if remaining <= 0:
                break
            chunk = min(remaining, due)
            is_last_chunk = chunk == remaining
            change = overall_change if method == "CASH" and is_last_chunk else Decimal("0")
            chunk_presented = chunk + change if method == "CASH" else chunk

            # The external Mobile Money transaction is one customer payment,
            # while our accounting allocation creates several Payment rows.
            # Provider identifiers are therefore stored once (on allocation 1)
            # to respect the unique provider transaction constraint.
            allocation_provider_code = provider_code if method == "MOBILE_MONEY" and index == 1 else None
            allocation_provider_transaction_id = (
                provider_transaction_id if method == "MOBILE_MONEY" and index == 1 else None
            )

            payment = payment_service.record(
                actor,
                bar_id,
                order.id,
                f"{base_reference[:56]}-{index:02d}",
                method,
                chunk_presented,
                chunk,
                change,
                cash_session_id=int(cash_session_id) if method == "CASH" else None,
                provider_code=allocation_provider_code,
                provider_transaction_id=allocation_provider_transaction_id,
            )
            allocations.append(
                {
                    "order_id": order.id,
                    "reference": order.reference,
                    "payment_id": payment.id,
                    "amount": chunk,
                }
            )
            remaining -= chunk

        if remaining != 0:
            raise ValueError("PAYMENT_ALLOCATION_FAILED")
        return {
            "reference": base_reference,
            "amount": number(amount),
            "change": number(overall_change),
            "currency": orders[0].currency,
            "allocations": allocations,
        }


order_merge_service = OrderMergeService()
