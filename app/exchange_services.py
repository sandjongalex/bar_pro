"""Standalone product exchanges, independent from orders and invoices."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import secrets
import uuid

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import AuditLog, Bar, CashSession, Product, utcnow
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text

SETTLEMENT_METHODS = {"CASH", "MOBILE_MONEY", "CARD", "BANK_TRANSFER"}
RETURN_DISPOSITIONS = {"RESTOCK", "LOSS"}


@dataclass(frozen=True)
class ExchangeResult:
    reference: str
    returned_product_name: str
    replacement_product_name: str
    returned_quantity: Decimal
    replacement_quantity: Decimal
    returned_value: Decimal
    replacement_value: Decimal
    difference_amount: Decimal
    settlement_amount: Decimal
    settlement_direction: str
    settlement_method: str | None


class ExchangeService:
    def _reference(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"ECH-{stamp}-{secrets.token_hex(2).upper()}"

    def _product(self, bar_id: int, product_id: int) -> Product:
        product = db.session.scalar(
            select(Product)
            .where(
                Product.bar_id == bar_id,
                Product.id == product_id,
                Product.is_active.is_(True),
            )
            .with_for_update()
        )
        if not product:
            raise LookupError("NOT_FOUND")
        return product

    def _open_cash_session(self, bar_id: int) -> CashSession:
        session = db.session.scalar(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
            .with_for_update()
        )
        if not session:
            raise ValueError("CASH_SESSION_REQUIRED")
        return session

    def create(
        self,
        actor,
        bar_id: int,
        returned_product_id: int,
        replacement_product_id: int,
        returned_quantity,
        replacement_quantity,
        returned_disposition: str,
        reason: str,
        settlement_method: str | None = None,
        provider_transaction_id: str | None = None,
    ) -> ExchangeResult:
        """Post one complete exchange without requiring an Order or invoice.

        The returned product can be restocked or treated as non-resellable. The
        replacement always leaves stock. Any price difference is settled
        independently from the original sale. The caller commits or rolls back.
        """
        permissions.require(actor, "payments.record", bar_id)
        reason = required_text(reason, 500)

        if returned_product_id == replacement_product_id:
            raise ValueError("SAME_PRODUCT")

        disposition = (returned_disposition or "").strip().upper()
        if disposition not in RETURN_DISPOSITIONS:
            raise ValueError("INVALID_DISPOSITION")

        returned_qty = number(returned_quantity, 6, positive=True)
        replacement_qty = number(replacement_quantity, 6, positive=True)
        returned_product = self._product(bar_id, returned_product_id)
        replacement_product = self._product(bar_id, replacement_product_id)

        returned_value = number(returned_qty * returned_product.sale_price)
        replacement_value = number(replacement_qty * replacement_product.sale_price)
        difference = number(replacement_value - returned_value)

        if difference > 0:
            direction = "COLLECT"
            settlement_amount = difference
        elif difference < 0:
            permissions.require(actor, "refunds.record", bar_id)
            direction = "REFUND"
            settlement_amount = -difference
        else:
            direction = "NONE"
            settlement_amount = Decimal("0")

        method = (settlement_method or "").strip().upper() or None
        provider_reference = (provider_transaction_id or "").strip() or None
        if direction == "NONE":
            method = None
            provider_reference = None
        else:
            if method not in SETTLEMENT_METHODS:
                raise ValueError("INVALID_METHOD")
            if method == "CASH" and provider_reference is not None:
                raise ValueError("INVALID_CASH_PROVIDER")
            if provider_reference is not None:
                provider_reference = required_text(provider_reference, 128)

        bar = db.session.get(Bar, bar_id)
        if not bar:
            raise LookupError("NOT_FOUND")

        reference = self._reference()
        operation_reason = f"Échange {reference}: {reason}"

        returned_movement = None
        if disposition == "RESTOCK":
            returned_movement = stock_service.move(
                actor,
                bar_id,
                returned_product.id,
                "RETURN",
                returned_qty,
                operation_reason,
            )

        replacement_movement = stock_service.move(
            actor,
            bar_id,
            replacement_product.id,
            "SALE",
            -replacement_qty,
            operation_reason,
        )

        cash_movement = None
        if method == "CASH" and settlement_amount > 0:
            session = self._open_cash_session(bar_id)
            amount_delta = settlement_amount if direction == "COLLECT" else -settlement_amount
            cash_movement = cash_service.entry(
                actor,
                bar_id,
                amount_delta,
                bar.currency,
                operation_reason,
                session_id=session.id,
                manual_kind="EXCHANGE",
            )

        db.session.flush()

        audit = AuditLog(
            scope="BAR",
            bar_id=bar_id,
            actor_id=actor.id,
            action="exchanges.record",
            target_table="stock_movements",
            target_id=replacement_movement.id,
            outcome="SUCCESS",
            reason=reason,
            request_id=uuid.uuid4().bytes,
            changes={
                "reference": reference,
                "returned_product_id": returned_product.id,
                "returned_product_name": returned_product.name,
                "returned_quantity": str(returned_qty),
                "returned_unit_price": str(returned_product.sale_price),
                "returned_value": str(returned_value),
                "returned_disposition": disposition,
                "replacement_product_id": replacement_product.id,
                "replacement_product_name": replacement_product.name,
                "replacement_quantity": str(replacement_qty),
                "replacement_unit_price": str(replacement_product.sale_price),
                "replacement_value": str(replacement_value),
                "difference_amount": str(difference),
                "settlement_direction": direction,
                "settlement_amount": str(settlement_amount),
                "settlement_method": method,
                "provider_transaction_id": provider_reference,
                "returned_stock_movement_id": returned_movement.id if returned_movement else None,
                "replacement_stock_movement_id": replacement_movement.id,
                "cash_movement_id": cash_movement.id if cash_movement else None,
            },
            occurred_at=utcnow(),
        )
        db.session.add(audit)

        return ExchangeResult(
            reference=reference,
            returned_product_name=returned_product.name,
            replacement_product_name=replacement_product.name,
            returned_quantity=returned_qty,
            replacement_quantity=replacement_qty,
            returned_value=returned_value,
            replacement_value=replacement_value,
            difference_amount=difference,
            settlement_amount=settlement_amount,
            settlement_direction=direction,
            settlement_method=method,
        )


exchange_service = ExchangeService()
