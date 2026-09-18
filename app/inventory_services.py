"""Period-based physical inventory with auditable theoretical sales and cash reconciliation."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.inventory_period_models import InventoryLineSnapshot, InventoryPeriodSnapshot
from app.models import (
    Bar,
    Expense,
    Inventory,
    InventoryLine,
    Order,
    Payment,
    Product,
    Refund,
    StockBalance,
    StockMovement,
    utcnow,
)
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text

ZERO = Decimal("0")
MONEY_STEP = Decimal("0.0001")


def product_identifier(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isascii() or not str(value).isdigit():
        raise ValueError("INVALID_PRODUCT_ID")
    result = int(value)
    if not 0 < result < 2**64:
        raise ValueError("INVALID_PRODUCT_ID")
    return result


def _money(value):
    return Decimal(value or 0).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def _utc_naive(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _in_period(column, start_at, end_at):
    filters = [column <= end_at]
    if start_at is not None:
        filters.append(column > start_at)
    return filters


class InventoryService:
    def get(self, actor, bar_id, inventory_id, *, write=False):
        permissions.require(actor, "inventory.adjust" if write else "inventory.read", bar_id)
        query = select(Inventory).where(Inventory.id == inventory_id, Inventory.bar_id == bar_id)
        if write:
            query = query.with_for_update().execution_options(populate_existing=True)
        inv = db.session.scalar(query)
        if inv is None:
            raise LookupError("NOT_FOUND")
        if write and inv.status != "DRAFT":
            raise ValueError("INVENTORY_IMMUTABLE")
        return inv

    def _end_at(self, bar, raw):
        local_tz = ZoneInfo(bar.timezone)
        now_local = datetime.now(local_tz)
        try:
            chosen = date.fromisoformat(str(raw)) if raw else now_local.date()
        except (TypeError, ValueError):
            raise ValueError("INVALID_END_DATE") from None
        if chosen > now_local.date():
            raise ValueError("INVALID_END_DATE")
        if chosen == now_local.date():
            end_local = now_local
        else:
            end_local = datetime.combine(chosen, time.max).replace(tzinfo=local_tz)
        return _utc_naive(end_local)

    def _period(self, bar_id, end_at):
        previous = db.session.scalar(
            select(Inventory)
            .join(
                InventoryPeriodSnapshot,
                (InventoryPeriodSnapshot.bar_id == Inventory.bar_id)
                & (InventoryPeriodSnapshot.inventory_id == Inventory.id),
            )
            .where(
                Inventory.bar_id == bar_id,
                Inventory.status == "POSTED",
                InventoryPeriodSnapshot.period_end_at < end_at,
            )
            .order_by(InventoryPeriodSnapshot.period_end_at.desc(), Inventory.id.desc())
            .limit(1)
        )
        if previous:
            snap = db.session.scalar(
                select(InventoryPeriodSnapshot).where(
                    InventoryPeriodSnapshot.bar_id == bar_id,
                    InventoryPeriodSnapshot.inventory_id == previous.id,
                )
            )
            return previous, snap.period_end_at

        # Legacy inventories created before period snapshots can still seed the
        # first modern period.
        legacy = db.session.scalar(
            select(Inventory)
            .outerjoin(
                InventoryPeriodSnapshot,
                (InventoryPeriodSnapshot.bar_id == Inventory.bar_id)
                & (InventoryPeriodSnapshot.inventory_id == Inventory.id),
            )
            .where(
                Inventory.bar_id == bar_id,
                Inventory.status == "POSTED",
                InventoryPeriodSnapshot.id.is_(None),
                Inventory.counted_at < end_at,
            )
            .order_by(Inventory.counted_at.desc(), Inventory.id.desc())
            .limit(1)
        )
        return (legacy, _utc_naive(legacy.counted_at)) if legacy else (None, None)

    def _movement_total(self, bar_id, product_id, end_at, start_at=None, movement_type=None):
        query = select(func.coalesce(func.sum(StockMovement.quantity_delta), 0)).where(
            StockMovement.bar_id == bar_id,
            StockMovement.product_id == product_id,
            *_in_period(StockMovement.occurred_at, start_at, end_at),
        )
        if movement_type:
            query = query.where(StockMovement.movement_type == movement_type)
        return Decimal(db.session.scalar(query) or 0)

    def create(self, actor, bar_id, reference, product_ids=None, reason="Inventaire physique", period_end=None):
        permissions.require(actor, "inventory.adjust", bar_id)
        bar = db.session.get(Bar, bar_id)
        if not bar:
            raise LookupError("NOT_FOUND")
        reference = required_text(reference, 64)
        reason = required_text(reason)
        end_at = self._end_at(bar, period_end)
        previous, start_at = self._period(bar_id, end_at)
        if start_at is not None and end_at <= start_at:
            raise ValueError("INVENTORY_PERIOD_OVERLAP")

        if product_ids:
            if not isinstance(product_ids, list):
                raise ValueError("INVALID_INVENTORY")
            ids = [product_identifier(value) for value in product_ids]
            if len(set(ids)) != len(ids):
                raise ValueError("DUPLICATE_PRODUCT")
            products = list(
                db.session.scalars(
                    select(Product)
                    .where(Product.id.in_(ids), Product.bar_id == bar_id)
                    .order_by(Product.name, Product.id)
                    .with_for_update()
                )
            )
            if len(products) != len(ids):
                raise LookupError("NOT_FOUND")
        else:
            products = list(
                db.session.scalars(
                    select(Product)
                    .where(Product.bar_id == bar_id, Product.is_active.is_(True))
                    .order_by(Product.name, Product.id)
                    .with_for_update()
                )
            )
        if not products:
            raise ValueError("INVALID_INVENTORY")

        prior_counts = {}
        if previous:
            prior_counts = {
                line.product_id: Decimal(line.counted_quantity)
                for line in db.session.scalars(
                    select(InventoryLine).where(
                        InventoryLine.bar_id == bar_id,
                        InventoryLine.inventory_id == previous.id,
                        InventoryLine.counted_quantity.is_not(None),
                    )
                )
            }

        inv = Inventory(
            bar_id=bar_id,
            reference=reference,
            status="DRAFT",
            counted_at=utcnow(),
            created_by_id=actor.id,
            reason=reason,
        )
        db.session.add(inv)
        db.session.flush()
        period = InventoryPeriodSnapshot(
            bar_id=bar_id,
            inventory_id=inv.id,
            period_start_at=start_at,
            period_end_at=end_at,
            theoretical_sales_amount=0,
            expenses_amount=0,
            credit_sales_amount=0,
            expected_cash_amount=0,
            recorded_net_amount=0,
            cash_difference_amount=0,
        )
        db.session.add(period)

        for product in products:
            balance = db.session.scalar(
                select(StockBalance)
                .where(StockBalance.product_id == product.id, StockBalance.bar_id == bar_id)
                .with_for_update()
            )
            if balance is None:
                balance = StockBalance(bar_id=bar_id, product_id=product.id, quantity=0, version=0)
                db.session.add(balance)
                db.session.flush()

            if product.id in prior_counts:
                opening = prior_counts[product.id]
            else:
                opening = self._movement_total(bar_id, product.id, end_at, start_at, "INITIAL")
                opening = max(opening, ZERO)
            purchases = self._movement_total(bar_id, product.id, end_at, start_at, "PURCHASE")
            purchases = max(purchases, ZERO)
            theoretical = opening + purchases
            machine_at_end = self._movement_total(bar_id, product.id, end_at)
            machine_at_end = max(machine_at_end, ZERO)

            line = InventoryLine(
                bar_id=bar_id,
                inventory_id=inv.id,
                product_id=product.id,
                expected_quantity_snapshot=machine_at_end,
                balance_version_snapshot=balance.version,
            )
            db.session.add(line)
            db.session.flush()
            db.session.add(
                InventoryLineSnapshot(
                    bar_id=bar_id,
                    inventory_line_id=line.id,
                    opening_quantity=opening,
                    purchase_quantity=purchases,
                    theoretical_quantity=theoretical,
                    sale_price_snapshot=product.sale_price,
                    theoretical_sold_quantity=0,
                    theoretical_sales_amount=0,
                )
            )
        return inv

    def count(self, actor, bar_id, inventory_id, quantities, notes=None):
        inv = self.get(actor, bar_id, inventory_id, write=True)
        if not isinstance(quantities, dict) or not quantities:
            raise ValueError("COUNT_REQUIRED")
        notes = notes or {}
        lines = {
            line.product_id: line
            for line in db.session.scalars(
                select(InventoryLine)
                .where(InventoryLine.inventory_id == inv.id, InventoryLine.bar_id == bar_id)
                .with_for_update()
            )
        }
        snapshots = {
            item.inventory_line_id: item
            for item in db.session.scalars(
                select(InventoryLineSnapshot)
                .where(InventoryLineSnapshot.bar_id == bar_id)
                .where(InventoryLineSnapshot.inventory_line_id.in_([line.id for line in lines.values()]))
                .with_for_update()
            )
        }
        values = {}
        for key, raw in quantities.items():
            product_id = product_identifier(key)
            if product_id not in lines or product_id in values:
                raise ValueError("INVALID_COUNT_PRODUCT")
            value = number(raw, 6)
            if value < 0:
                raise ValueError("INVALID_COUNT")
            values[product_id] = value

        for product_id, value in values.items():
            line = lines[product_id]
            snap = snapshots.get(line.id)
            if not snap:
                raise ValueError("INVENTORY_PERIOD_DATA_MISSING")
            line.counted_quantity = value
            sold = Decimal(snap.theoretical_quantity) - value
            snap.theoretical_sold_quantity = sold
            snap.theoretical_sales_amount = _money(sold * Decimal(snap.sale_price_snapshot))
            if str(product_id) in notes:
                text = str(notes[str(product_id)] or "").strip()
                if len(text) > 500:
                    raise ValueError("NOTE_TOO_LONG")
                snap.note = text or None

        inv.counted_at = utcnow()
        period = db.session.scalar(
            select(InventoryPeriodSnapshot).where(
                InventoryPeriodSnapshot.bar_id == bar_id,
                InventoryPeriodSnapshot.inventory_id == inv.id,
            )
        )
        if period:
            period.theoretical_sales_amount = _money(
                sum(
                    (Decimal(s.theoretical_sales_amount) for s in snapshots.values() if lines[next(pid for pid, line in lines.items() if line.id == s.inventory_line_id)].counted_quantity is not None),
                    ZERO,
                )
            )
        return inv

    def _financial_snapshot(self, inv, period, theoretical_sales):
        start_at = period.period_start_at
        end_at = period.period_end_at

        expenses = ZERO
        for item in db.session.scalars(
            select(Expense).where(Expense.bar_id == inv.bar_id, *_in_period(Expense.incurred_at, start_at, end_at))
        ):
            expenses += Decimal(item.amount) if item.entry_kind == "EXPENSE" else -Decimal(item.amount)
        expenses = max(_money(expenses), ZERO)

        order_ids = list(
            db.session.scalars(
                select(Order.id).where(
                    Order.bar_id == inv.bar_id,
                    Order.status.in_(["CONFIRMED", "SERVED"]),
                    Order.posted_at.is_not(None),
                    *_in_period(Order.posted_at, start_at, end_at),
                )
            )
        )

        credit_sales = ZERO
        payments = ZERO
        refunds = ZERO
        if order_ids:
            credit_sales = Decimal(
                db.session.scalar(
                    select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
                        CustomerLedgerEntry.bar_id == inv.bar_id,
                        CustomerLedgerEntry.order_id.in_(order_ids),
                        CustomerLedgerEntry.entry_kind.in_(["CREDIT_SALE", "REVERSAL"]),
                        CustomerLedgerEntry.occurred_at <= end_at,
                    )
                )
                or 0
            )
            payments = Decimal(
                db.session.scalar(
                    select(func.coalesce(func.sum(Payment.amount_applied), 0)).where(
                        Payment.bar_id == inv.bar_id,
                        Payment.order_id.in_(order_ids),
                        Payment.received_at <= end_at,
                    )
                )
                or 0
            )
            refunds = Decimal(
                db.session.scalar(
                    select(func.coalesce(func.sum(Refund.amount), 0)).where(
                        Refund.bar_id == inv.bar_id,
                        Refund.order_id.in_(order_ids),
                        Refund.refunded_at <= end_at,
                    )
                )
                or 0
            )

        credit_sales = max(_money(credit_sales), ZERO)
        net_receipts = _money(payments - refunds)
        expected_cash = _money(theoretical_sales - credit_sales - expenses)
        recorded_net = _money(net_receipts - expenses)
        cash_difference = _money(recorded_net - expected_cash)

        period.theoretical_sales_amount = _money(theoretical_sales)
        period.expenses_amount = expenses
        period.credit_sales_amount = credit_sales
        period.expected_cash_amount = expected_cash
        period.recorded_net_amount = recorded_net
        period.cash_difference_amount = cash_difference

    def post(self, actor, bar_id, inventory_id):
        inv = self.get(actor, bar_id, inventory_id, write=True)
        lines = list(
            db.session.scalars(
                select(InventoryLine)
                .where(InventoryLine.inventory_id == inv.id, InventoryLine.bar_id == bar_id)
                .order_by(InventoryLine.product_id)
                .with_for_update()
            )
        )
        if not lines:
            raise ValueError("COUNT_REQUIRED")

        snapshots = {
            item.inventory_line_id: item
            for item in db.session.scalars(
                select(InventoryLineSnapshot)
                .where(
                    InventoryLineSnapshot.bar_id == bar_id,
                    InventoryLineSnapshot.inventory_line_id.in_([line.id for line in lines]),
                )
                .with_for_update()
            )
        }
        period = db.session.scalar(
            select(InventoryPeriodSnapshot)
            .where(
                InventoryPeriodSnapshot.bar_id == bar_id,
                InventoryPeriodSnapshot.inventory_id == inv.id,
            )
            .with_for_update()
        )
        if not period or len(snapshots) != len(lines):
            raise ValueError("INVENTORY_PERIOD_DATA_MISSING")

        for line in lines:
            if line.counted_quantity is None:
                raise ValueError("COUNT_REQUIRED")
            balance = db.session.scalar(
                select(StockBalance)
                .where(StockBalance.bar_id == bar_id, StockBalance.product_id == line.product_id)
                .with_for_update()
            )
            if balance is None or balance.version != line.balance_version_snapshot:
                raise ValueError("INVENTORY_STALE")

        theoretical_sales = ZERO
        for line in lines:
            snap = snapshots[line.id]
            sold = Decimal(snap.theoretical_quantity) - Decimal(line.counted_quantity)
            snap.theoretical_sold_quantity = sold
            snap.theoretical_sales_amount = _money(sold * Decimal(snap.sale_price_snapshot))
            theoretical_sales += Decimal(snap.theoretical_sales_amount)

        self._financial_snapshot(inv, period, _money(theoretical_sales))

        for line in lines:
            delta = Decimal(line.counted_quantity) - Decimal(line.expected_quantity_snapshot)
            if delta:
                stock_service.move(
                    actor,
                    bar_id,
                    line.product_id,
                    "INVENTORY_ADJUSTMENT",
                    delta,
                    f"Inventaire {inv.reference}",
                    inventory_line_id=line.id,
                )
        inv.status = "POSTED"
        inv.posted_at = utcnow()
        return inv

    def cancel(self, actor, bar_id, inventory_id):
        inv = self.get(actor, bar_id, inventory_id, write=True)
        inv.status = "CANCELLED"
        inv.cancelled_at = utcnow()
        return inv


inventory_service = InventoryService()
