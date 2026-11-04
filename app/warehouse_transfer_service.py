"""V2 replenishment: MAIN -> TRANSIT -> CASHIER; no reverse movement or sales.

All public mutations require the caller to commit/rollback the SQLAlchemy
transaction. Permission checks lock Bar, serializing concurrent warehouse
writers within the same tenant. The voucher/line rows are locked as well.
No stock moves unless the owner-approved warehouse allocation is LIVE.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from sqlalchemy import select

from app.audit import record
from app.extensions import db
from app.models import Bar, Product, StaffAssignment, User, utcnow
from app.permissions import permissions
from app.validation import number, required_text
from app.warehouse_models import (
    WarehouseStockBalance, WarehouseTransfer, WarehouseTransferEvent,
    WarehouseTransferLine, WarehouseTransferReceipt,
)
from app.warehouse_service import WarehouseError, warehouse_service

STATUSES = frozenset({
    "REQUESTED", "PREPARING", "SHIPPED", "PARTIALLY_RECEIVED",
    "RECEIVED", "DISPUTED", "CLOSED",
})


def _id(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        raise WarehouseError("INVALID_ID")
    return int(value)


def _quantity(value, *, positive=False):
    quantity = number(value, 6)
    if quantity < 0 or (positive and quantity == 0):
        raise WarehouseError("INVALID_WAREHOUSE_QUANTITY")
    return Decimal(quantity)


def _note(value, *, required=False):
    if not isinstance(value, str):
        raise WarehouseError("INVALID_NOTE")
    result = value.strip()
    if len(result) > 500 or (required and len(result) < 5):
        raise WarehouseError("INVALID_NOTE")
    return result


def _unit_quantity(value, case_size):
    """Convert CASE/BOTTLE input into stock base units with exact Decimal."""
    unit = "BOTTLE"
    raw = value
    if isinstance(value, dict):
        if set(value) != {"quantity", "unit"}:
            raise WarehouseError("INVALID_TRANSFER_QUANTITY")
        raw, unit = value["quantity"], value["unit"]
    if unit not in {"CASE", "BOTTLE"}:
        raise WarehouseError("INVALID_TRANSFER_UNIT")
    quantity = _quantity(raw)
    return _quantity(quantity * (case_size if unit == "CASE" else 1)), unit


def _lines(payload, *, allow_empty=False):
    if not isinstance(payload, dict) or (not payload and not allow_empty):
        raise WarehouseError("INVALID_WAREHOUSE_LINES")
    result = {}
    for raw_id, value in payload.items():
        pid = _id(raw_id)
        if pid in result:
            raise WarehouseError("DUPLICATE_PRODUCT")
        result[pid] = value
    return result


class WarehouseTransferService:
    def _transfer(self, bar_id, transfer_id):
        transfer = db.session.scalar(
            select(WarehouseTransfer).where(
                WarehouseTransfer.bar_id == bar_id,
                WarehouseTransfer.id == _id(transfer_id),
            ).with_for_update()
        )
        if transfer is None:
            raise LookupError("NOT_FOUND")
        return transfer

    def _transfer_lines(self, bar_id, transfer_id):
        return {
            item.product_id: item for item in db.session.scalars(
                select(WarehouseTransferLine).where(
                    WarehouseTransferLine.bar_id == bar_id,
                    WarehouseTransferLine.transfer_id == transfer_id,
                ).order_by(WarehouseTransferLine.product_id).with_for_update()
            )
        }

    def _recipient(self, actor, bar_id, recipient_id):
        if recipient_id is None:
            recipient_id = actor.id
        user = db.session.get(User, _id(recipient_id))
        bar = db.session.get(Bar, bar_id)
        if not user or not user.is_active or not bar:
            raise LookupError("NOT_FOUND")
        if user.id == bar.owner_id and user.category == "OWNER":
            return user
        assignment = db.session.scalar(select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == user.id,
            StaffAssignment.ended_at.is_(None),
            StaffAssignment.role.in_(("CASHIER", "BAR_ADMIN")),
        ))
        if not assignment:
            raise WarehouseError("INVALID_TRANSFER_RECIPIENT")
        if assignment.role == "BAR_ADMIN" and not permissions.evaluate(
            user, "warehouse.transfers.receive", bar_id
        ).allowed:
            raise WarehouseError("INVALID_TRANSFER_RECIPIENT")
        return user

    @staticmethod
    def _event(actor, transfer, kind, note, *, line=None, quantity=Decimal("0")):
        db.session.add(WarehouseTransferEvent(
            bar_id=transfer.bar_id, transfer_id=transfer.id,
            transfer_line_id=line.id if line else None,
            event_kind=kind, quantity=quantity,
            unit_cost_snapshot=Decimal(line.unit_cost_snapshot) if line else Decimal("0"),
            actor_id=actor.id, note=note, occurred_at=utcnow(),
        ))

    @staticmethod
    def _status(transfer, lines):
        if any(Decimal(x.quantity_disputed) > 0 for x in lines.values()):
            transfer.status = "DISPUTED"
        elif all(
            Decimal(x.quantity_received) == Decimal(x.quantity_dispatched)
            for x in lines.values()
        ):
            transfer.status = "RECEIVED"
        elif any(Decimal(x.quantity_received) > 0 for x in lines.values()):
            transfer.status = "PARTIALLY_RECEIVED"
        else:
            transfer.status = "SHIPPED"

    @staticmethod
    def _receiver(actor, transfer, bar_id):
        permissions.require(actor, "warehouse.transfers.receive", bar_id)
        if transfer.recipient_user_id is not None and actor.id != transfer.recipient_user_id:
            bar = db.session.get(Bar, bar_id)
            if bar is None or bar.owner_id != actor.id or actor.category != "OWNER":
                raise PermissionError("TRANSFER_RECIPIENT_REQUIRED")

    def create_request(self, actor, bar_id, reference, quantities, recipient_user_id=None):
        # Storekeepers can initiate direct replenishment; cashiers can request it.
        if permissions.evaluate(actor, "warehouse.requests.create", bar_id).allowed:
            permissions.require(actor, "warehouse.requests.create", bar_id)
        else:
            permissions.require(actor, "warehouse.transfers.dispatch", bar_id)
        warehouse_service._bar(bar_id)
        ref = required_text(reference, 64)
        if db.session.scalar(select(WarehouseTransfer.id).where(
            WarehouseTransfer.bar_id == bar_id, WarehouseTransfer.reference == ref,
        )):
            raise WarehouseError("TRANSFER_REFERENCE_EXISTS")
        lines = _lines(quantities)
        recipient = self._recipient(actor, bar_id, recipient_user_id)
        products = {
            row.id: row for row in db.session.scalars(select(Product).where(
                Product.bar_id == bar_id, Product.id.in_(sorted(lines)),
            ).order_by(Product.id).with_for_update())
        }
        if set(products) != set(lines):
            raise LookupError("NOT_FOUND")
        prepared = {}
        for pid, raw in lines.items():
            product = products[pid]
            if not product.is_active:
                raise WarehouseError("INACTIVE_PRODUCT")
            case_size = Decimal(product.units_per_case or 0)
            if case_size <= 0:
                # Single-bottle products without a case definition remain valid.
                if isinstance(raw, dict) and raw.get("unit") == "CASE":
                    raise WarehouseError("CASE_SIZE_REQUIRED")
                case_size = Decimal("1")
            quantity, unit = _unit_quantity(raw, case_size)
            if quantity <= 0:
                raise WarehouseError("INVALID_WAREHOUSE_QUANTITY")
            prepared[pid] = (quantity, unit, case_size, Decimal(product.valuation_unit_cost))

        voucher = WarehouseTransfer(
            bar_id=bar_id, reference=ref, status="REQUESTED",
            requested_by_id=actor.id, requested_at=utcnow(),
            recipient_user_id=recipient.id, dispatched_by_id=None, dispatched_at=None,
        )
        db.session.add(voucher)
        db.session.flush()
        for pid in sorted(prepared):
            qty, unit, case_size, cost = prepared[pid]
            line = WarehouseTransferLine(
                bar_id=bar_id, transfer_id=voucher.id, product_id=pid,
                quantity_requested=qty, quantity_prepared=0,
                quantity_dispatched=0, quantity_received=0,
                quantity_disputed=0, quantity_lost=0,
                requested_unit=unit, units_per_case_snapshot=case_size,
                unit_cost_snapshot=cost,
            )
            db.session.add(line)
            db.session.flush()
            self._event(actor, voucher, "REQUESTED", "Demande de réapprovisionnement", line=line, quantity=qty)
        record(actor, bar_id, "warehouse.transfer.request", "warehouse_transfers", voucher.id, ref)
        return voucher

    def prepare(self, actor, bar_id, transfer_id, quantities):
        permissions.require(actor, "warehouse.requests.prepare", bar_id)
        warehouse_service._bar(bar_id)
        voucher = self._transfer(bar_id, transfer_id)
        if voucher.status not in {"REQUESTED", "PREPARING"}:
            raise WarehouseError("TRANSFER_IMMUTABLE_AFTER_DISPATCH")
        given = _lines(quantities)
        lines = self._transfer_lines(bar_id, voucher.id)
        if set(given) - set(lines):
            raise LookupError("NOT_FOUND")
        amounts = {}
        for pid, raw in given.items():
            line = lines[pid]
            qty, _ = _unit_quantity(raw, Decimal(line.units_per_case_snapshot))
            if qty > Decimal(line.quantity_requested):
                raise WarehouseError("PREPARED_EXCEEDS_REQUESTED")
            amounts[pid] = qty
        if not any(amount > 0 for amount in amounts.values()):
            raise WarehouseError("TRANSFER_EMPTY")
        for pid, qty in amounts.items():
            line = lines[pid]
            line.quantity_prepared = qty
            self._event(actor, voucher, "PREPARED", "Quantité préparée", line=line, quantity=qty)
        voucher.prepared_by_id = actor.id
        voucher.prepared_at = utcnow()
        voucher.status = "PREPARING"
        record(actor, bar_id, "warehouse.transfer.prepare", "warehouse_transfers", voucher.id, voucher.reference)
        return voucher

    def ship(self, actor, bar_id, transfer_id):
        permissions.require(actor, "warehouse.transfers.dispatch", bar_id)
        warehouse_service._bar(bar_id)
        warehouse_service._live(bar_id)
        voucher = self._transfer(bar_id, transfer_id)
        if voucher.status != "PREPARING":
            raise WarehouseError("TRANSFER_NOT_PREPARING")
        lines = self._transfer_lines(bar_id, voucher.id)
        if not any(Decimal(x.quantity_prepared) > 0 for x in lines.values()):
            raise WarehouseError("TRANSFER_EMPTY")
        locations = warehouse_service.ensure_locations(bar_id)
        main = locations["MAIN"]
        # Validate *all* items under locks before changing any physical balance.
        products = {
            p.id: p for p in db.session.scalars(select(Product).where(
                Product.bar_id == bar_id,
                Product.id.in_(sorted(lines)),
            ).order_by(Product.id).with_for_update())
        }
        for pid in sorted(lines):
            line = lines[pid]
            qty = Decimal(line.quantity_prepared)
            if qty == 0:
                continue
            stock = warehouse_service._stock(bar_id, main.id, pid)
            if Decimal(stock.quantity) < qty:
                raise WarehouseError("INSUFFICIENT_WAREHOUSE_STOCK")
            if pid not in products:
                raise LookupError("NOT_FOUND")
        for pid in sorted(lines):
            line = lines[pid]
            qty = Decimal(line.quantity_prepared)
            if qty == 0:
                continue
            cost = Decimal(products[pid].valuation_unit_cost)
            line.unit_cost_snapshot = cost
            warehouse_service._move(
                actor=actor, bar_id=bar_id, product_id=pid,
                location_id=main.id, kind="TRANSFER_OUT", delta=-qty,
                cost=cost, transfer_line_id=line.id,
                reason=f"Expédition MAIN : {voucher.reference}",
            )
            warehouse_service._move(
                actor=actor, bar_id=bar_id, product_id=pid,
                location_id=None, kind="TRANSIT_IN", delta=qty,
                cost=cost, transfer_line_id=line.id,
                reason=f"Mise en transit : {voucher.reference}",
            )
            line.quantity_dispatched = qty
            self._event(actor, voucher, "SHIPPED", "Expédié depuis MAIN", line=line, quantity=qty)
        voucher.status = "SHIPPED"
        voucher.dispatched_by_id = actor.id
        voucher.dispatched_at = utcnow()
        db.session.flush()
        record(actor, bar_id, "warehouse.transfer.ship", "warehouse_transfers", voucher.id, voucher.reference)
        return voucher

    def receive(self, actor, bar_id, transfer_id, quantities, idempotency_key):
        # Exactly-once acceptance for the same idempotency key, with a hard
        # per-line cap also protecting retries with *different* keys.
        permissions.require(actor, "warehouse.transfers.receive", bar_id)
        warehouse_service._bar(bar_id)
        warehouse_service._live(bar_id)
        key = required_text(idempotency_key, 80)
        if len(key) < 8:
            raise WarehouseError("INVALID_IDEMPOTENCY_KEY")
        given = _lines(quantities)
        accepted = {pid: _quantity(raw, positive=True) for pid, raw in given.items()}
        payload = {str(pid): str(qty) for pid, qty in sorted(accepted.items())}
        digest = hashlib.sha256(json.dumps(
            {"transfer_id": _id(transfer_id), "quantities": payload},
            sort_keys=True, separators=(",", ":"),
        ).encode()).hexdigest()
        previous = db.session.scalar(select(WarehouseTransferReceipt).where(
            WarehouseTransferReceipt.bar_id == bar_id,
            WarehouseTransferReceipt.idempotency_key == key,
        ).with_for_update())
        if previous:
            if previous.transfer_id != _id(transfer_id) or previous.payload_hash != digest:
                raise WarehouseError("IDEMPOTENCY_CONFLICT")
            return previous
        voucher = self._transfer(bar_id, transfer_id)
        self._receiver(actor, voucher, bar_id)
        if voucher.status not in {"SHIPPED", "PARTIALLY_RECEIVED", "DISPUTED"}:
            raise WarehouseError("TRANSFER_NOT_RECEIVABLE")
        lines = self._transfer_lines(bar_id, voucher.id)
        if set(accepted) - set(lines):
            raise LookupError("NOT_FOUND")
        for pid, qty in accepted.items():
            line = lines[pid]
            available = (
                Decimal(line.quantity_dispatched)
                - Decimal(line.quantity_received)
                - Decimal(line.quantity_disputed)
                - Decimal(line.quantity_lost)
            )
            if qty > available:
                raise WarehouseError("RECEIPT_EXCEEDS_TRANSIT")
        receipt = WarehouseTransferReceipt(
            bar_id=bar_id, transfer_id=voucher.id, idempotency_key=key,
            payload_hash=digest, quantities=payload,
            received_by_id=actor.id, received_at=utcnow(),
        )
        db.session.add(receipt)
        db.session.flush()
        cashier = warehouse_service.ensure_locations(bar_id)["CASHIER"]
        for pid in sorted(accepted):
            qty = accepted[pid]
            line = lines[pid]
            warehouse_service._move(
                actor=actor, bar_id=bar_id, product_id=pid,
                location_id=None, kind="TRANSIT_OUT", delta=-qty,
                cost=Decimal(line.unit_cost_snapshot), transfer_line_id=line.id,
                receipt_id=receipt.id, reason=f"Réception caisse : {voucher.reference}",
            )
            warehouse_service._move(
                actor=actor, bar_id=bar_id, product_id=pid,
                location_id=cashier.id, kind="TRANSFER_IN", delta=qty,
                cost=Decimal(line.unit_cost_snapshot), transfer_line_id=line.id,
                receipt_id=receipt.id, reason=f"Réception caisse : {voucher.reference}",
            )
            line.quantity_received = Decimal(line.quantity_received) + qty
            self._event(actor, voucher, "RECEIVED", "Accepté physiquement à CASHIER", line=line, quantity=qty)
        voucher.last_received_by_id = actor.id
        self._status(voucher, lines)
        db.session.flush()
        record(actor, bar_id, "warehouse.transfer.receive", "warehouse_transfer_receipts", receipt.id, voucher.reference)
        return receipt

    def report_dispute(self, actor, bar_id, transfer_id, quantities, note):
        permissions.require(actor, "warehouse.transfers.receive", bar_id)
        warehouse_service._bar(bar_id)
        warehouse_service._live(bar_id)
        voucher = self._transfer(bar_id, transfer_id)
        self._receiver(actor, voucher, bar_id)
        if voucher.status not in {"SHIPPED", "PARTIALLY_RECEIVED", "DISPUTED"}:
            raise WarehouseError("TRANSFER_NOT_RECEIVABLE")
        reason = _note(note, required=True)
        given = _lines(quantities)
        disputed = {pid: _quantity(raw, positive=True) for pid, raw in given.items()}
        lines = self._transfer_lines(bar_id, voucher.id)
        if set(disputed) - set(lines):
            raise LookupError("NOT_FOUND")
        for pid, qty in disputed.items():
            line = lines[pid]
            remaining = (
                Decimal(line.quantity_dispatched)
                - Decimal(line.quantity_received)
                - Decimal(line.quantity_disputed)
                - Decimal(line.quantity_lost)
            )
            if qty > remaining:
                raise WarehouseError("DISPUTE_EXCEEDS_TRANSIT")
        for pid, qty in disputed.items():
            line = lines[pid]
            line.quantity_disputed = Decimal(line.quantity_disputed) + qty
            self._event(actor, voucher, "DISPUTED", reason, line=line, quantity=qty)
        voucher.status = "DISPUTED"
        record(actor, bar_id, "warehouse.transfer.dispute", "warehouse_transfers", voucher.id, reason)
        return voucher

    def resolve_dispute(self, actor, bar_id, transfer_id, product_id, quantity, outcome, note):
        permissions.require(actor, "warehouse.discrepancies.resolve", bar_id)
        warehouse_service._bar(bar_id)
        warehouse_service._live(bar_id)
        voucher = self._transfer(bar_id, transfer_id)
        if voucher.status != "DISPUTED":
            raise WarehouseError("TRANSFER_NOT_DISPUTED")
        pid = _id(product_id)
        qty = _quantity(quantity, positive=True)
        reason = _note(note, required=True)
        if outcome not in {"DELIVERED", "LOSS_APPROVED"}:
            raise WarehouseError("INVALID_DISPUTE_RESOLUTION")
        lines = self._transfer_lines(bar_id, voucher.id)
        if pid not in lines:
            raise LookupError("NOT_FOUND")
        line = lines[pid]
        if qty > Decimal(line.quantity_disputed):
            raise WarehouseError("RESOLUTION_EXCEEDS_DISPUTE")
        cost = Decimal(line.unit_cost_snapshot)
        warehouse_service._move(
            actor=actor, bar_id=bar_id, product_id=pid,
            location_id=None, kind="TRANSIT_OUT", delta=-qty,
            cost=cost, transfer_line_id=line.id,
            reason=f"Litige {voucher.reference}: {outcome}: {reason}",
        )
        if outcome == "DELIVERED":
            cashier = warehouse_service.ensure_locations(bar_id)["CASHIER"]
            warehouse_service._move(
                actor=actor, bar_id=bar_id, product_id=pid,
                location_id=cashier.id, kind="TRANSFER_IN", delta=qty,
                cost=cost, transfer_line_id=line.id,
                reason=f"Livraison complémentaire validée {voucher.reference}: {reason}",
            )
            line.quantity_received = Decimal(line.quantity_received) + qty
            voucher.last_received_by_id = actor.id
        else:
            line.quantity_lost = Decimal(line.quantity_lost) + qty
        line.quantity_disputed = Decimal(line.quantity_disputed) - qty
        self._event(actor, voucher, outcome, reason, line=line, quantity=qty)
        self._status(voucher, lines)
        db.session.flush()
        record(actor, bar_id, "warehouse.transfer.resolve", "warehouse_transfers", voucher.id, f"{outcome}: {reason}")
        return voucher

    def close(self, actor, bar_id, transfer_id, note):
        permissions.require(actor, "warehouse.discrepancies.resolve", bar_id)
        warehouse_service._bar(bar_id)
        voucher = self._transfer(bar_id, transfer_id)
        if voucher.status not in {"RECEIVED", "PARTIALLY_RECEIVED", "SHIPPED"}:
            raise WarehouseError("TRANSFER_NOT_CLOSABLE")
        reason = _note(note, required=True)
        lines = self._transfer_lines(bar_id, voucher.id)
        if any(
            Decimal(x.quantity_dispatched) != Decimal(x.quantity_received) + Decimal(x.quantity_lost)
            for x in lines.values()
        ):
            raise WarehouseError("TRANSFER_UNSETTLED_TRANSIT")
        voucher.status = "CLOSED"
        voucher.closed_by_id = actor.id
        voucher.closed_at = utcnow()
        self._event(actor, voucher, "CLOSED", reason)
        record(actor, bar_id, "warehouse.transfer.close", "warehouse_transfers", voucher.id, reason)
        return voucher

    def detail(self, actor, bar_id, transfer_id):
        permissions.require(actor, "warehouse.transfers.read", bar_id)
        voucher = db.session.scalar(select(WarehouseTransfer).where(
            WarehouseTransfer.bar_id == bar_id, WarehouseTransfer.id == _id(transfer_id),
        ))
        if voucher is None:
            raise LookupError("NOT_FOUND")
        lines = list(db.session.scalars(select(WarehouseTransferLine).where(
            WarehouseTransferLine.bar_id == bar_id, WarehouseTransferLine.transfer_id == voucher.id,
        ).order_by(WarehouseTransferLine.id)))
        events = list(db.session.scalars(select(WarehouseTransferEvent).where(
            WarehouseTransferEvent.bar_id == bar_id, WarehouseTransferEvent.transfer_id == voucher.id,
        ).order_by(WarehouseTransferEvent.id)))
        return voucher, lines, events


warehouse_transfer_service = WarehouseTransferService()
