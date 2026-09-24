"""Standalone sold-product refunds from the cashier exchange workspace."""
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

REFUND_METHODS = {"CASH", "MOBILE_MONEY", "CARD", "BANK_TRANSFER"}
RETURN_DISPOSITIONS = {"RESTOCK", "LOSS"}


@dataclass(frozen=True)
class StandaloneRefundResult:
    reference: str
    product_name: str
    quantity: Decimal
    refund_amount: Decimal
    refund_method: str
    returned_disposition: str


class StandaloneRefundService:
    def _reference(self) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        return f"RMB-BIER-{stamp}-{secrets.token_hex(2).upper()}"

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
        product_id: int,
        quantity,
        returned_disposition: str,
        reason: str,
        refund_method: str,
        provider_transaction_id: str | None = None,
    ) -> StandaloneRefundResult:
        """Refund a returned sold product without requiring the original invoice.

        The refund amount is the current catalogue sale price multiplied by the
        returned quantity. A resellable bottle returns to stock; a non-resellable
        bottle does not. Cash refunds debit the currently open cash session.
        The caller owns the transaction and must commit or roll back.
        """
        permissions.require(actor, "refunds.record", bar_id)
        reason = required_text(reason, 500)

        disposition = (returned_disposition or "").strip().upper()
        if disposition not in RETURN_DISPOSITIONS:
            raise ValueError("INVALID_DISPOSITION")

        qty = number(quantity, 6, positive=True)
        product = self._product(bar_id, product_id)
        refund_amount = number(qty * product.sale_price)
        if refund_amount <= 0:
            raise ValueError("INVALID_REFUND_AMOUNT")

        method = (refund_method or "").strip().upper()
        if method not in REFUND_METHODS:
            raise ValueError("INVALID_METHOD")

        provider_reference = (provider_transaction_id or "").strip() or None
        if method == "CASH" and provider_reference is not None:
            raise ValueError("INVALID_CASH_PROVIDER")
        if provider_reference is not None:
            provider_reference = required_text(provider_reference, 128)

        bar = db.session.get(Bar, bar_id)
        if not bar:
            raise LookupError("NOT_FOUND")

        reference = self._reference()
        operation_reason = f"Remboursement bière {reference}: {reason}"

        returned_movement = None
        if disposition == "RESTOCK":
            returned_movement = stock_service.move(
                actor,
                bar_id,
                product.id,
                "RETURN",
                qty,
                operation_reason,
            )

        cash_movement = None
        if method == "CASH":
            session = self._open_cash_session(bar_id)
            cash_movement = cash_service.entry(
                actor,
                bar_id,
                -refund_amount,
                bar.currency,
                operation_reason,
                session_id=session.id,
                manual_kind="EXCHANGE",
            )

        db.session.flush()

        db.session.add(
            AuditLog(
                scope="BAR",
                bar_id=bar_id,
                actor_id=actor.id,
                action="exchanges.refund",
                target_table="products",
                target_id=product.id,
                outcome="SUCCESS",
                reason=reason,
                request_id=uuid.uuid4().bytes,
                changes={
                    "reference": reference,
                    "operation_type": "REFUND",
                    "returned_product_id": product.id,
                    "returned_product_name": product.name,
                    "returned_quantity": str(qty),
                    "returned_unit_price": str(product.sale_price),
                    "returned_value": str(refund_amount),
                    "returned_disposition": disposition,
                    "replacement_product_id": None,
                    "replacement_product_name": None,
                    "replacement_quantity": None,
                    "replacement_unit_price": None,
                    "replacement_value": "0",
                    "difference_amount": str(-refund_amount),
                    "settlement_direction": "REFUND",
                    "settlement_amount": str(refund_amount),
                    "settlement_method": method,
                    "provider_transaction_id": provider_reference,
                    "returned_stock_movement_id": returned_movement.id if returned_movement else None,
                    "replacement_stock_movement_id": None,
                    "cash_movement_id": cash_movement.id if cash_movement else None,
                },
                occurred_at=utcnow(),
            )
        )

        return StandaloneRefundResult(
            reference=reference,
            product_name=product.name,
            quantity=qty,
            refund_amount=refund_amount,
            refund_method=method,
            returned_disposition=disposition,
        )


standalone_refund_service = StandaloneRefundService()
