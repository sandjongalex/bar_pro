"""Manual payments and traceable refunds share the caller transaction."""
from sqlalchemy import select

from app.audit import record
from app.cash_services import cash_service
from app.customer_services import notify_server
from app.extensions import db
from app.finance_totals import order_balance, total
from app.models import CashSession, Order, OrderReturn, Payment, Refund, StaffAssignment, utcnow
from app.order_suborder_models import OrderSuborder
from app.permissions import permissions
from app.validation import number, required_text


class PaymentService:
    def _require_cashier_session(self, actor, bar_id):
        if actor.category != "EMPLOYEE":
            return
        assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.user_id == actor.id,
                StaffAssignment.ended_at.is_(None),
            )
        )
        if assignment and assignment.role == "CASHIER":
            opened = db.session.scalar(
                select(CashSession.id).where(
                    CashSession.bar_id == bar_id,
                    CashSession.status == "OPEN",
                )
            )
            if not opened:
                raise ValueError("CASH_SESSION_REQUIRED")

    def record(self, actor, bar_id, order_id, reference, method, presented, applied, change=0, cash_session_id=None, staff_assignment_id=None, provider_code=None, provider_transaction_id=None, voucher_amount=0):
        permissions.require(actor, "payments.record", bar_id)
        self._require_cashier_session(actor, bar_id)
        order = db.session.scalar(select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update())
        if not order:
            raise LookupError("NOT_FOUND")
        if order.status not in {"CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_PAYABLE")
        pending_delivery = db.session.scalar(
            select(OrderSuborder.id)
            .where(
                OrderSuborder.bar_id == bar_id,
                OrderSuborder.order_id == order.id,
                OrderSuborder.status == "VALIDATED",
                OrderSuborder.delivery_status == "PENDING",
            )
            .limit(1)
        )
        if pending_delivery is not None:
            # The invoice already includes the server-added amount, but the stock
            # must not leave the bar until the cashier acknowledges physical delivery.
            raise ValueError("ORDER_NOT_PAYABLE")
        presented, applied, change, voucher_amount = map(number, (presented, applied, change, voucher_amount))
        if applied <= 0 or change < 0 or voucher_amount < 0 or presented != applied + change + voucher_amount:
            raise ValueError("INVALID_PAYMENT_AMOUNTS")
        if method not in {"CASH", "CARD", "MOBILE_MONEY", "BANK_TRANSFER"}:
            raise ValueError("INVALID_METHOD")
        if applied > order_balance(order)["amount_due"]:
            raise ValueError("PAYMENT_LIMIT_EXCEEDED")
        if (provider_code is None) != (provider_transaction_id is None):
            raise ValueError("PROVIDER_REFERENCE_REQUIRED")
        if provider_code is not None:
            provider_code = required_text(provider_code, 32)
            provider_transaction_id = required_text(provider_transaction_id, 128)
        if method == "CASH":
            if provider_code is not None:
                raise ValueError("INVALID_CASH_PROVIDER")
            if (cash_session_id is None) == (staff_assignment_id is None):
                raise ValueError("CASH_LOCATION_REQUIRED")
            if cash_session_id is not None and cash_service.session(bar_id, cash_session_id).currency != order.currency:
                raise ValueError("CURRENCY_MISMATCH")
            if staff_assignment_id is not None and cash_service.staff(bar_id, staff_assignment_id).ended_at is not None:
                raise ValueError("STAFF_ASSIGNMENT_ENDED")
        elif cash_session_id is not None or staff_assignment_id is not None or change != 0 or voucher_amount != 0:
            raise ValueError("INVALID_NONCASH_PAYMENT")

        was_paid = order.payment_status == "PAID"
        item = Payment(
            bar_id=bar_id,
            order_id=order_id,
            reference=required_text(reference, 64),
            amount=applied,
            amount_presented=presented,
            amount_applied=applied,
            change_given=change,
            currency=order.currency,
            method=method,
            cash_session_id=cash_session_id,
            staff_assignment_id=staff_assignment_id,
            cash_holder=("STAFF" if staff_assignment_id is not None else "DRAWER") if method == "CASH" else None,
            provider_code=provider_code,
            provider_transaction_id=provider_transaction_id,
            received_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(item)
        db.session.flush()
        if method == "CASH":
            cash_service.entry(
                actor,
                bar_id,
                applied,
                order.currency,
                f"Paiement {reference}",
                session_id=cash_session_id,
                staff_id=staff_assignment_id,
                payment_id=item.id,
            )
        order_balance(order, update=True)
        if not was_paid and order.payment_status == "PAID":
            notify_server(
                order,
                "PAYMENT_VALIDATED",
                "Paiement validé",
                f"La commande {order.reference} a été entièrement encaissée.",
            )
        record(actor, bar_id, "payments.record", "payments", item.id, item.reference)
        return item

    def refund(self, actor, bar_id, payment_id, reference, amount, reason, order_return_id=None, cash_session_id=None, staff_assignment_id=None):
        permissions.require(actor, "refunds.record", bar_id)
        self._require_cashier_session(actor, bar_id)
        reason = required_text(reason)
        reference = required_text(reference, 64)
        amount = number(amount, positive=True)
        payment = db.session.scalar(select(Payment).where(Payment.id == payment_id, Payment.bar_id == bar_id))
        if not payment:
            raise LookupError("NOT_FOUND")
        order = db.session.scalar(select(Order).where(Order.id == payment.order_id, Order.bar_id == bar_id).with_for_update())
        if order.status not in {"CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_REFUNDABLE")
        refunded = total(Refund, Refund.amount, bar_id=bar_id, payment_id=payment_id)
        if refunded + amount > payment.amount_applied:
            raise ValueError("REFUND_PAYMENT_LIMIT")
        if order_return_id is not None:
            returned = db.session.scalar(
                select(OrderReturn).where(
                    OrderReturn.id == order_return_id,
                    OrderReturn.bar_id == bar_id,
                    OrderReturn.order_id == order.id,
                    OrderReturn.status == "POSTED",
                )
            )
            if not returned:
                raise LookupError("NOT_FOUND")
            previous = total(Refund, Refund.amount, bar_id=bar_id, order_return_id=order_return_id)
            if previous + amount > returned.total_amount:
                raise ValueError("REFUND_RETURN_LIMIT")
            if amount > order_balance(order)["refundable_overpayment"]:
                raise ValueError("REFUND_OVERPAYMENT_LIMIT")
        if payment.method == "CASH":
            if (cash_session_id is None) == (staff_assignment_id is None):
                raise ValueError("CASH_LOCATION_REQUIRED")
        elif cash_session_id is not None or staff_assignment_id is not None:
            raise ValueError("INVALID_NONCASH_REFUND")
        item = Refund(
            bar_id=bar_id,
            payment_id=payment.id,
            order_id=order.id,
            order_return_id=order_return_id,
            reference=reference,
            amount=amount,
            currency=payment.currency,
            method=payment.method,
            cash_session_id=cash_session_id,
            staff_assignment_id=staff_assignment_id,
            cash_holder=("STAFF" if staff_assignment_id is not None else "DRAWER") if payment.method == "CASH" else None,
            reason=reason,
            refunded_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(item)
        db.session.flush()
        if payment.method == "CASH":
            cash_service.entry(
                actor,
                bar_id,
                -amount,
                payment.currency,
                reason,
                session_id=cash_session_id,
                staff_id=staff_assignment_id,
                refund_id=item.id,
            )
        order_balance(order, update=True)
        record(actor, bar_id, "refunds.record", "refunds", item.id, reason)
        return item

    def cancel_paid(self, actor, bar_id, order_id, reference, reason, cash_session_id=None, staff_assignment_id=None):
        from app.order_services import order_service

        permissions.require(actor, "refunds.record", bar_id)
        permissions.require(actor, "orders.edit", bar_id)
        self._require_cashier_session(actor, bar_id)
        order = db.session.scalar(select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update())
        if not order or order.status != "CONFIRMED":
            raise ValueError("ORDER_NOT_CANCELLABLE")
        reference = required_text(reference, 40)
        reason = required_text(reason)
        for payment in db.session.scalars(
            select(Payment).where(Payment.bar_id == bar_id, Payment.order_id == order_id).order_by(Payment.id)
        ):
            remaining = payment.amount_applied - total(Refund, Refund.amount, bar_id=bar_id, payment_id=payment.id)
            if remaining:
                self.refund(
                    actor,
                    bar_id,
                    payment.id,
                    f"{reference}-{payment.id}",
                    remaining,
                    reason,
                    cash_session_id=cash_session_id if payment.method == "CASH" else None,
                    staff_assignment_id=staff_assignment_id if payment.method == "CASH" else None,
                )
        return order_service.cancel(actor, bar_id, order_id, reason)


payment_service = PaymentService()
