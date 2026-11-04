"""Warehouse V2 backend primitives, never wired to legacy commercial routes.

All methods are transaction-scoped; the caller commits/rolls back. Allocation
creates a *staged* snapshot only. No live cutover is exposed in PROMPT 1.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from sqlalchemy import func, select

from app.audit import record
from app.extensions import db
from app.models import Bar, CashSession, Product, StockBalance, StockMovement, utcnow
from app.permissions import permissions
from app.shift_models import EmployeeShift
from app.validation import number, required_text
from app.warehouse_models import (
    WarehouseAllocation, WarehouseAllocationLine, WarehouseLocation,
    WarehouseStockBalance, WarehouseStockMovement, WarehouseTransfer,
    WarehouseTransferLine, WarehouseTransferReceipt, WarehouseTransitBalance,
)


class WarehouseError(ValueError):
    pass


LOCATION_LABELS = {"MAIN": "Magasin principal", "CASHIER": "Magasin de la caisse"}


def _qty(value, *, positive=False):
    value = number(value, 6)
    if value < 0 or (positive and value == 0):
        raise WarehouseError("INVALID_WAREHOUSE_QUANTITY")
    return value


def _item_id(value):
    if isinstance(value, bool) or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0:
        raise WarehouseError("INVALID_PRODUCT_ID")
    return int(value)


def _normalized_lines(data, *, positive=False):
    if not isinstance(data, dict):
        raise WarehouseError("INVALID_WAREHOUSE_LINES")
    result = {}
    for key, value in data.items():
        product_id = _item_id(key)
        if product_id in result:
            raise WarehouseError("DUPLICATE_PRODUCT")
        result[product_id] = _qty(value, positive=positive)
    return result


class WarehouseService:
    @staticmethod
    def _bar(bar_id):
        bar = db.session.scalar(select(Bar).where(Bar.id == bar_id).with_for_update())
        if bar is None:
            raise LookupError("NOT_FOUND")
        return bar

    def ensure_locations(self, bar_id):
        """Create the two fixed physical locations, without any stock movement."""
        self._bar(bar_id)
        rows = {
            row.code: row
            for row in db.session.scalars(
                select(WarehouseLocation).where(WarehouseLocation.bar_id == bar_id).with_for_update()
            )
        }
        for code, label in LOCATION_LABELS.items():
            if code not in rows:
                row = WarehouseLocation(bar_id=bar_id, code=code, label=label)
                db.session.add(row)
                db.session.flush()
                rows[code] = row
        return rows

    def _allocation(self, bar_id, *, lock=False):
        query = select(WarehouseAllocation).where(WarehouseAllocation.bar_id == bar_id)
        return db.session.scalar(query.with_for_update() if lock else query)

    def _live(self, bar_id):
        state = self._allocation(bar_id, lock=True)
        if state is None or state.status != "LIVE":
            raise WarehouseError("WAREHOUSE_NOT_LIVE")
        return state

    @staticmethod
    def _stock(bar_id, location_id, product_id):
        stock = db.session.scalar(
            select(WarehouseStockBalance).where(
                WarehouseStockBalance.bar_id == bar_id,
                WarehouseStockBalance.location_id == location_id,
                WarehouseStockBalance.product_id == product_id,
            ).with_for_update()
        )
        if stock is None:
            stock = WarehouseStockBalance(
                bar_id=bar_id, location_id=location_id, product_id=product_id,
                quantity=Decimal("0"), version=0,
            )
            db.session.add(stock)
            db.session.flush()
        return stock

    @staticmethod
    def _transit(bar_id, product_id):
        row = db.session.scalar(
            select(WarehouseTransitBalance).where(
                WarehouseTransitBalance.bar_id == bar_id,
                WarehouseTransitBalance.product_id == product_id,
            ).with_for_update()
        )
        if row is None:
            row = WarehouseTransitBalance(bar_id=bar_id, product_id=product_id, quantity=0, version=0)
            db.session.add(row)
            db.session.flush()
        return row

    @staticmethod
    def _move(*, actor, bar_id, product_id, location_id, kind, delta, cost, reason,
              allocation_line_id=None, transfer_line_id=None, receipt_id=None):
        """Internal paired ledger write: caller holds the bar row lock."""
        if delta == 0:
            return None
        allowed_destination = {
            "TRANSFER_OUT": "MAIN",
            "TRANSFER_IN": "CASHIER",
            "PURCHASE_IN": "MAIN",
            "PURCHASE_REVERSAL": "MAIN",
        }.get(kind)
        if allowed_destination is not None:
            location_code = db.session.scalar(select(WarehouseLocation.code).where(
                WarehouseLocation.bar_id == bar_id,
                WarehouseLocation.id == location_id,
            ))
            if location_code != allowed_destination:
                # No path can ever move CASHIER stock back to MAIN.
                raise WarehouseError("INVALID_TRANSFER_DIRECTION")
        if kind.startswith("TRANSIT_"):
            if location_id is not None:
                raise WarehouseError("INVALID_LOCATION")
            balance = WarehouseService._transit(bar_id, product_id)
        else:
            if location_id is None:
                raise WarehouseError("INVALID_LOCATION")
            balance = WarehouseService._stock(bar_id, location_id, product_id)
        if Decimal(balance.quantity) + delta < 0:
            raise WarehouseError("INSUFFICIENT_WAREHOUSE_STOCK")
        balance.quantity = Decimal(balance.quantity) + delta
        balance.version += 1
        movement = WarehouseStockMovement(
            bar_id=bar_id, location_id=location_id, product_id=product_id,
            movement_type=kind, quantity_delta=delta, unit_cost_snapshot=cost,
            allocation_line_id=allocation_line_id, transfer_line_id=transfer_line_id,
            receipt_id=receipt_id, recorded_by_id=actor.id, occurred_at=utcnow(),
            reason=reason,
        )
        db.session.add(movement)
        return movement

    def stage_allocation(self, actor, bar_id, quantities):
        """Owner-approved initial MAIN/CASHIER split, without enabling live writes.

        Input: {product_id: {"MAIN": quantity, "CASHIER": quantity}}.
        Omitted products may only have zero legacy stock. No automatic guess.
        Repeated identical submissions return the original staging record.
        """
        permissions.require(actor, "inventory.adjust", bar_id)
        bar = self._bar(bar_id)
        if actor.category != "OWNER" or bar.owner_id != actor.id:
            raise PermissionError("OWNER_REQUIRED")
        if not isinstance(quantities, dict):
            raise WarehouseError("INVALID_ALLOCATION")
        proposed = {}
        for key, allocation in quantities.items():
            pid = _item_id(key)
            if pid in proposed or not isinstance(allocation, dict) or set(allocation) != {"MAIN", "CASHIER"}:
                raise WarehouseError("INVALID_ALLOCATION")
            proposed[pid] = (_qty(allocation["MAIN"]), _qty(allocation["CASHIER"]))

        # Lock the bar first (also used by every legacy write through permissions).
        products = list(db.session.scalars(
            select(Product).where(Product.bar_id == bar_id).order_by(Product.id).with_for_update()
        ))
        stocks = {
            row.product_id: row for row in db.session.scalars(
                select(StockBalance).where(StockBalance.bar_id == bar_id).with_for_update()
            )
        }
        if set(proposed) - {product.id for product in products}:
            raise LookupError("NOT_FOUND")
        expected = {}
        for product in products:
            balance = stocks.get(product.id)
            old_qty = Decimal(balance.quantity) if balance else Decimal("0")
            old_version = int(balance.version) if balance else 0
            main, cashier = proposed.get(product.id, (Decimal("0"), Decimal("0")))
            if main + cashier != old_qty:
                raise WarehouseError("ALLOCATION_TOTAL_MISMATCH")
            expected[product.id] = (main, cashier, old_qty, old_version, Decimal(product.valuation_unit_cost))

        previous = self._allocation(bar_id, lock=True)
        if previous:
            old_lines = list(db.session.scalars(
                select(WarehouseAllocationLine).where(
                    WarehouseAllocationLine.bar_id == bar_id,
                    WarehouseAllocationLine.allocation_id == previous.id,
                )
            ))
            if len(old_lines) != len(expected) or any(
                expected[line.product_id] != (
                    Decimal(line.main_quantity), Decimal(line.cashier_quantity),
                    Decimal(line.legacy_quantity_snapshot), int(line.legacy_balance_version),
                    Decimal(line.unit_cost_snapshot),
                )
                for line in old_lines
            ):
                raise WarehouseError("ALLOCATION_ALREADY_STAGED_OR_STALE")
            return previous

        # Never stage while the stock/cash responsibility is actively being used.
        if db.session.scalar(select(CashSession.id).where(
            CashSession.bar_id == bar_id, CashSession.status == "OPEN",
        ).limit(1)):
            raise WarehouseError("OPEN_CASH_SESSION")
        if db.session.scalar(select(EmployeeShift.id).where(
            EmployeeShift.bar_id == bar_id, EmployeeShift.status == "OPEN",
        ).limit(1)):
            raise WarehouseError("OPEN_EMPLOYEE_SHIFT")

        locations = self.ensure_locations(bar_id)
        cursor = db.session.scalar(
            select(func.max(StockMovement.id)).where(StockMovement.bar_id == bar_id)
        )
        staged = WarehouseAllocation(
            bar_id=bar_id, status="STAGED", validated_by_id=actor.id,
            validated_at=utcnow(), legacy_movement_checkpoint_id=cursor,
        )
        db.session.add(staged)
        db.session.flush()
        for product in products:
            main, cashier, original, version, cost = expected[product.id]
            line = WarehouseAllocationLine(
                bar_id=bar_id, allocation_id=staged.id, product_id=product.id,
                legacy_quantity_snapshot=original, legacy_balance_version=version,
                main_quantity=main, cashier_quantity=cashier, unit_cost_snapshot=cost,
            )
            db.session.add(line)
            db.session.flush()
            for code, qty in (("MAIN", main), ("CASHIER", cashier)):
                # Zero balances make it possible to distinguish missing from empty.
                location = locations[code]
                row = WarehouseStockBalance(
                    bar_id=bar_id, location_id=location.id, product_id=product.id,
                    quantity=0, version=0,
                )
                db.session.add(row)
                db.session.flush()
                if qty:
                    self._move(
                        actor=actor, bar_id=bar_id, product_id=product.id,
                        location_id=location.id, kind="OPENING", delta=qty, cost=cost,
                        allocation_line_id=line.id, reason="Répartition initiale validée",
                    )
        db.session.flush()
        record(actor, bar_id, "warehouse.allocation.stage", "warehouse_allocations", staged.id,
               "Répartition physique initiale; aucune activation des ventes")
        return staged

    def verify_staged(self, bar_id):
        """Fail closed when legacy stock has changed since owner validation."""
        staged = self._allocation(bar_id)
        if staged is None or staged.status != "STAGED":
            raise WarehouseError("WAREHOUSE_NOT_STAGED")
        for line in db.session.scalars(
            select(WarehouseAllocationLine).where(
                WarehouseAllocationLine.bar_id == bar_id,
                WarehouseAllocationLine.allocation_id == staged.id,
            )
        ):
            bal = db.session.scalar(select(StockBalance).where(
                StockBalance.bar_id == bar_id, StockBalance.product_id == line.product_id,
            ))
            quantity, version = (Decimal(bal.quantity), bal.version) if bal else (Decimal("0"), 0)
            if quantity != Decimal(line.legacy_quantity_snapshot) or version != line.legacy_balance_version:
                raise WarehouseError("ALLOCATION_STALE")
        return True

    def totals(self, bar_id):
        """One source of truth for staged warehouse balances; never add legacy stock."""
        # Filter by bar_id throughout, rather than trusting a caller-supplied location.
        rows = db.session.execute(
            select(WarehouseStockBalance.product_id, WarehouseLocation.code,
                   WarehouseStockBalance.quantity)
            .join(WarehouseLocation,
                  (WarehouseLocation.bar_id == WarehouseStockBalance.bar_id)
                  & (WarehouseLocation.id == WarehouseStockBalance.location_id))
            .where(WarehouseStockBalance.bar_id == bar_id)
        ).all()
        transit = {
            product_id: Decimal(qty)
            for product_id, qty in db.session.execute(
                select(WarehouseTransitBalance.product_id, WarehouseTransitBalance.quantity)
                .where(WarehouseTransitBalance.bar_id == bar_id)
            )
        }
        data = {}
        for product_id, code, qty in rows:
            data.setdefault(product_id, {"MAIN": Decimal(0), "CASHIER": Decimal(0), "TRANSIT": Decimal(0)})
            data[product_id][code] += Decimal(qty)
        for product_id, qty in transit.items():
            data.setdefault(product_id, {"MAIN": Decimal(0), "CASHIER": Decimal(0), "TRANSIT": Decimal(0)})
            data[product_id]["TRANSIT"] += qty
        for item in data.values():
            item["TOTAL"] = item["MAIN"] + item["CASHIER"] + item["TRANSIT"]
        return data

    def dispatch(self, actor, bar_id, reference, quantities):
        """Compatibility facade: all new dispatches use the audited V2 voucher."""
        from app.warehouse_transfer_service import warehouse_transfer_service
        # Reject before preparing anything when the two stores are not LIVE.
        permissions.require(actor, "warehouse.transfers.dispatch", bar_id)
        self._bar(bar_id)
        self._live(bar_id)
        voucher = warehouse_transfer_service.create_request(
            actor, bar_id, reference, quantities, recipient_user_id=actor.id,
        )
        warehouse_transfer_service.prepare(actor, bar_id, voucher.id, quantities)
        return warehouse_transfer_service.ship(actor, bar_id, voucher.id)

    def receive(self, actor, bar_id, transfer_id, received_quantities, idempotency_key):
        """Compatibility facade; receipt and idempotency now have one owner."""
        from app.warehouse_transfer_service import warehouse_transfer_service
        return warehouse_transfer_service.receive(
            actor, bar_id, transfer_id, received_quantities, idempotency_key,
        )


warehouse_service = WarehouseService()
