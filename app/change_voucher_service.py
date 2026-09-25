"""Issue, redeem and cash-refund customer change vouchers.

The caller owns the SQLAlchemy transaction.  Voucher issuance keeps the physical
cash drawer correct: money not returned to the customer stays in the drawer and
is recorded as a liability, not as bar revenue.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import secrets

from sqlalchemy import select

from app.audit import record
from app.cash_services import cash_service
from app.change_voucher_models import ChangeVoucher, ChangeVoucherTransaction
from app.customer_services import notify_server
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Order, Payment, utcnow
from app.permissions import permissions
from app.validation import number


class ChangeVoucherService:
    @staticmethod
    def _code() -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"BM-{stamp}-{secrets.randbelow(1_000_000):06d}"

    @staticmethod
    def _reference(prefix: str) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"

    def _unique_code(self, bar_id: int) -> str:
        for _ in range(12):
            code = self._code()
            exists = db.session.scalar(
                select(ChangeVoucher.id).where(
                    ChangeVoucher.bar_id == bar_id,
                    ChangeVoucher.code == code,
                )
            )
            if not exists:
                return code
        raise ValueError("VOUCHER_CODE_UNAVAILABLE")

    @staticmethod
    def _refresh_status(voucher: ChangeVoucher) -> None:
        balance = Decimal(voucher.balance_amount or 0)
        if balance <= 0:
            voucher.balance_amount = Decimal("0")
            voucher.status = "SETTLED"
            voucher.settled_at = utcnow()
        elif balance < Decimal(voucher.initial_amount):
            voucher.status = "PARTIAL"
            voucher.settled_at = None
        else:
            voucher.status = "ACTIVE"
            voucher.settled_at = None

    def issue(
        self,
        actor,
        bar_id: int,
        payment_id: int,
        amount,
        *,
        customer_name: str | None = None,
        customer_phone: str | None = None,
    ) -> ChangeVoucher:
        permissions.require(actor, "payments.record", bar_id)
        amount = number(amount, positive=True)
        payment = db.session.scalar(
            select(Payment).where(
                Payment.id == payment_id,
                Payment.bar_id == bar_id,
            ).with_for_update()
        )
        if not payment:
            raise LookupError("NOT_FOUND")
        if payment.method != "CASH":
            raise ValueError("VOUCHER_CASH_ONLY")
        if payment.cash_session_id is None and payment.staff_assignment_id is None:
            raise ValueError("CASH_LOCATION_REQUIRED")
        if db.session.scalar(
            select(ChangeVoucher.id).where(
                ChangeVoucher.bar_id == bar_id,
                ChangeVoucher.origin_payment_id == payment.id,
            )
        ):
            raise ValueError("VOUCHER_ALREADY_ISSUED")

        retained = (
            Decimal(payment.amount_presented)
            - Decimal(payment.amount_applied)
            - Decimal(payment.change_given)
        )
        if retained != Decimal(amount):
            raise ValueError("INVALID_VOUCHER_AMOUNT")

        voucher = ChangeVoucher(
            bar_id=bar_id,
            code=self._unique_code(bar_id),
            origin_order_id=payment.order_id,
            origin_payment_id=payment.id,
            initial_amount=amount,
            balance_amount=amount,
            currency=payment.currency,
            status="ACTIVE",
            customer_name=(customer_name or "").strip()[:160] or None,
            customer_phone=(customer_phone or "").strip()[:32] or None,
            issued_at=utcnow(),
            issued_by_id=actor.id,
        )
        db.session.add(voucher)
        db.session.flush()

        cash_source = {"manual_kind": "VOUCHER_ISSUE"} if payment.cash_session_id is not None else {}
        cash_service.entry(
            actor,
            bar_id,
            amount,
            payment.currency,
            f"Bon de monnaie {voucher.code} · monnaie non rendue",
            session_id=payment.cash_session_id,
            staff_id=payment.staff_assignment_id,
            **cash_source,
        )
        record(
            actor,
            bar_id,
            "change_vouchers.issue",
            "change_vouchers",
            voucher.id,
            f"{voucher.code} · {amount} {payment.currency}",
        )
        return voucher

    def by_code(self, bar_id: int, code: str, *, lock: bool = False) -> ChangeVoucher:
        statement = select(ChangeVoucher).where(
            ChangeVoucher.bar_id == bar_id,
            ChangeVoucher.code == str(code or "").strip().upper(),
        )
        if lock:
            statement = statement.with_for_update()
        voucher = db.session.scalar(statement)
        if not voucher:
            raise LookupError("VOUCHER_NOT_FOUND")
        return voucher

    def redeem(
        self,
        actor,
        bar_id: int,
        code: str,
        order_id: int,
        amount=None,
    ) -> ChangeVoucherTransaction:
        permissions.require(actor, "payments.record", bar_id)
        voucher = self.by_code(bar_id, code, lock=True)
        if voucher.status not in {"ACTIVE", "PARTIAL"} or Decimal(voucher.balance_amount) <= 0:
            raise ValueError("VOUCHER_NOT_ACTIVE")

        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        if not order:
            raise LookupError("NOT_FOUND")
        if order.status not in {"CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_PAYABLE")
        if order.currency != voucher.currency:
            raise ValueError("CURRENCY_MISMATCH")

        due = Decimal(order_balance(order)["amount_due"] or 0)
        if due <= 0:
            raise ValueError("ORDER_NOT_PAYABLE")
        maximum = min(Decimal(voucher.balance_amount), due)
        applied = maximum if amount in (None, "", 0, "0") else Decimal(number(amount, positive=True))
        if applied > maximum:
            raise ValueError("VOUCHER_AMOUNT_EXCEEDED")

        was_paid = order.payment_status == "PAID"
        transaction = ChangeVoucherTransaction(
            bar_id=bar_id,
            voucher_id=voucher.id,
            reference=self._reference("BM-USE"),
            kind="REDEEM",
            amount=applied,
            currency=voucher.currency,
            target_order_id=order.id,
            cash_session_id=None,
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(transaction)
        voucher.balance_amount = Decimal(voucher.balance_amount) - applied
        self._refresh_status(voucher)
        db.session.flush()
        order_balance(order, update=True)
        if not was_paid and order.payment_status == "PAID":
            notify_server(
                order,
                "PAYMENT_VALIDATED",
                "Paiement validé",
                f"La commande {order.reference} a été entièrement réglée, bon de monnaie inclus.",
            )
        record(
            actor,
            bar_id,
            "change_vouchers.redeem",
            "change_voucher_transactions",
            transaction.id,
            f"{voucher.code} → {order.reference} · {applied} {voucher.currency}",
        )
        return transaction

    def refund_cash(
        self,
        actor,
        bar_id: int,
        code: str,
        cash_session_id: int,
        amount=None,
    ) -> ChangeVoucherTransaction:
        permissions.require(actor, "payments.record", bar_id)
        voucher = self.by_code(bar_id, code, lock=True)
        if voucher.status not in {"ACTIVE", "PARTIAL"} or Decimal(voucher.balance_amount) <= 0:
            raise ValueError("VOUCHER_NOT_ACTIVE")

        balance = Decimal(voucher.balance_amount)
        refunded = balance if amount in (None, "", 0, "0") else Decimal(number(amount, positive=True))
        if refunded > balance:
            raise ValueError("VOUCHER_AMOUNT_EXCEEDED")

        cash_service.entry(
            actor,
            bar_id,
            -refunded,
            voucher.currency,
            f"Remboursement bon de monnaie {voucher.code}",
            session_id=cash_session_id,
            manual_kind="VOUCHER_REFUND",
        )
        transaction = ChangeVoucherTransaction(
            bar_id=bar_id,
            voucher_id=voucher.id,
            reference=self._reference("BM-RMB"),
            kind="CASH_REFUND",
            amount=refunded,
            currency=voucher.currency,
            target_order_id=None,
            cash_session_id=cash_session_id,
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(transaction)
        voucher.balance_amount = balance - refunded
        self._refresh_status(voucher)
        db.session.flush()
        record(
            actor,
            bar_id,
            "change_vouchers.cash_refund",
            "change_voucher_transactions",
            transaction.id,
            f"{voucher.code} · {refunded} {voucher.currency}",
        )
        return transaction


change_voucher_service = ChangeVoucherService()
