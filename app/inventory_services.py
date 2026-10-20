"""Period-based physical inventory with auditable theoretical sales and cash reconciliation."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.customer_models import CustomerLedgerEntry
from app.extensions import db
from app.inventory_period_models import InventoryLineSnapshot, InventoryPeriodSnapshot
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_count_models import InventoryLineCountState
from app.shift_models import EmployeeShift
from app.shift_service import get_active_shift_for_user
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
    StaffAssignment,
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
    def _is_cashier(self, actor, bar_id):
        if actor.category != "EMPLOYEE":
            return False
        return db.session.scalar(
            select(StaffAssignment.id).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.user_id == actor.id,
                StaffAssignment.role == "CASHIER",
                StaffAssignment.ended_at.is_(None),
            ).limit(1)
        ) is not None

    def get(self, actor, bar_id, inventory_id, *, write=False, for_count=False):
        action = "inventory.count" if for_count else "inventory.adjust" if write else "inventory.read"
        permissions.require(actor, action, bar_id)
        query = select(Inventory).where(Inventory.id == inventory_id, Inventory.bar_id == bar_id)
        if write or for_count:
            query = query.with_for_update().execution_options(populate_existing=True)
        inv = db.session.scalar(query)
        if inv is None:
            raise LookupError("NOT_FOUND")
        if self._is_cashier(actor, bar_id):
            # A cashier may inspect/count only inventories attributed to them.
            belongs = db.session.scalar(
                select(InventoryCashierAssignment.id).where(
                    InventoryCashierAssignment.bar_id == bar_id,
                    InventoryCashierAssignment.inventory_id == inventory_id,
                    InventoryCashierAssignment.cashier_user_id == actor.id,
                )
            )
            if belongs is None:
                raise LookupError("NOT_FOUND")
        if (write or for_count) and inv.status != "DRAFT":
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

    def create(self, actor, bar_id, reference, product_ids=None, reason="Inventaire physique", period_end=None, cashier_shift_id=None):
        # Legacy owners/admins retain the V1 flow until the V2 approval flow ships.
        # Cashiers cannot use that legacy path: they always need an active cashier shift.
        is_cashier = self._is_cashier(actor, bar_id)
        shift = None
        if is_cashier or cashier_shift_id is not None:
            permissions.require(actor, "inventory.create", bar_id)
            if is_cashier:
                shift = get_active_shift_for_user(actor.id, bar_id)
                if not shift or shift.role_snapshot != "CASHIER":
                    raise PermissionError("CASHIER_OFF_DUTY")
                if cashier_shift_id is not None and str(cashier_shift_id) != str(shift.id):
                    raise LookupError("NOT_FOUND")
            else:
                if (isinstance(cashier_shift_id, bool)
                        or not isinstance(cashier_shift_id, (str, int))
                        or not str(cashier_shift_id).isascii()
                        or not str(cashier_shift_id).isdigit()
                        or int(cashier_shift_id) <= 0):
                    raise ValueError("INVALID_CASHIER_SHIFT")
                shift = db.session.scalar(
                    select(EmployeeShift).where(
                        EmployeeShift.bar_id == bar_id,
                        EmployeeShift.id == int(cashier_shift_id),
                        EmployeeShift.role_snapshot == "CASHIER",
                        EmployeeShift.status == "OPEN",
                    ).with_for_update()
                )
            if shift is None or shift.bar_id != bar_id or shift.status != "OPEN":
                raise LookupError("CASHIER_SHIFT_NOT_FOUND")
            assignment = db.session.scalar(
                select(StaffAssignment).where(
                    StaffAssignment.id == shift.staff_assignment_id,
                    StaffAssignment.bar_id == bar_id,
                    StaffAssignment.role == "CASHIER",
                    StaffAssignment.ended_at.is_(None),
                )
            )
            if not assignment or not assignment.user.is_active:
                raise LookupError("CASHIER_SHIFT_NOT_FOUND")
            if is_cashier and assignment.user_id != actor.id:
                raise LookupError("NOT_FOUND")
        else:
            permissions.require(actor, "inventory.adjust", bar_id)
            assignment = None
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

        # All commerce stock movements acquire the same bar write lock as this
        # transaction; the cursor is captured at a consistent ledger boundary.
        opening_checkpoint = db.session.scalar(
            select(func.max(StockMovement.id)).where(StockMovement.bar_id == bar_id)
        ) or 0 if shift is not None else None
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
        if shift is not None:
            db.session.add(
                InventoryCashierAssignment(
                    bar_id=bar_id,
                    inventory_id=inv.id,
                    cashier_shift_id=shift.id,
                    cashier_user_id=assignment.user_id,
                    cashier_name_snapshot=assignment.user.display_name,
                    assigned_by_id=actor.id,
                    assigned_at=utcnow(),
                    movement_checkpoint_id=opening_checkpoint,
                )
            )
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
                expected_quantity_snapshot=balance.quantity if shift is not None else machine_at_end,
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
        inv = self.get(actor, bar_id, inventory_id, for_count=True)
        attribution = db.session.scalar(
            select(InventoryCashierAssignment).where(
                InventoryCashierAssignment.bar_id == bar_id,
                InventoryCashierAssignment.inventory_id == inv.id,
            )
        )
        if attribution is not None and self._is_cashier(actor, bar_id):
            active = get_active_shift_for_user(actor.id, bar_id)
            if not active or active.id != attribution.cashier_shift_id:
                raise PermissionError("INVENTORY_SHIFT_CLOSED")
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
        count_checkpoint = db.session.scalar(
            select(func.max(StockMovement.id)).where(StockMovement.bar_id == bar_id)
        ) or 0 if attribution is not None else None
        values = {}
        for key, raw in quantities.items():
            product_id = product_identifier(key)
            if product_id not in lines or product_id in values:
                raise ValueError("INVALID_COUNT_PRODUCT")
            value = None if raw is None and attribution is not None else number(raw, 6)
            if value is not None and value < 0:
                raise ValueError("INVALID_COUNT")
            values[product_id] = value

        for product_id, value in values.items():
            line = lines[product_id]
            snap = snapshots.get(line.id)
            if not snap:
                raise ValueError("INVENTORY_PERIOD_DATA_MISSING")
            previous_quantity = line.counted_quantity
            line.counted_quantity = value
            if value is None:
                snap.theoretical_sold_quantity = ZERO
                snap.theoretical_sales_amount = ZERO
            else:
                sold = Decimal(snap.theoretical_quantity) - value
                snap.theoretical_sold_quantity = sold
                snap.theoretical_sales_amount = _money(sold * Decimal(snap.sale_price_snapshot))
            if str(product_id) in notes:
                text = str(notes[str(product_id)] or "").strip()
                if len(text) > 500:
                    raise ValueError("NOTE_TOO_LONG")
                snap.note = text or None
            if attribution is not None:
                state = db.session.scalar(
                    select(InventoryLineCountState).where(
                        InventoryLineCountState.bar_id == bar_id,
                        InventoryLineCountState.inventory_line_id == line.id,
                    ).with_for_update()
                )
                if state is None:
                    db.session.add(InventoryLineCountState(
                        bar_id=bar_id,
                        inventory_line_id=line.id,
                        counted_by_id=actor.id,
                        counted_at=utcnow() if value is not None else None,
                        movement_checkpoint_id=count_checkpoint if value is not None else None,
                        revision=1,
                    ))
                else:
                    state.counted_by_id = actor.id
                    if previous_quantity != value:
                        state.counted_at = utcnow() if value is not None else None
                        state.movement_checkpoint_id = count_checkpoint if value is not None else None
                    state.revision += 1

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

    def count_v2_entries(self, actor, bar_id, inventory_id, entries):
        """Save only explicitly supplied V2 rows with optimistic revisions.

        Empty cases AND units means uncounted, never a measured zero.
        A literal numeric 0 means a measured zero.
        """
        inv = self.get(actor, bar_id, inventory_id, for_count=True)
        attribution = db.session.scalar(
            select(InventoryCashierAssignment).where(
                InventoryCashierAssignment.bar_id == bar_id,
                InventoryCashierAssignment.inventory_id == inv.id,
            )
        )
        if attribution is None:
            raise ValueError("INVENTORY_V2_REQUIRED")
        if self._is_cashier(actor, bar_id):
            active = get_active_shift_for_user(actor.id, bar_id)
            if not active or active.id != attribution.cashier_shift_id:
                raise PermissionError("INVENTORY_SHIFT_CLOSED")
        if not isinstance(entries, list) or not entries or len(entries) > 500:
            raise ValueError("COUNT_REQUIRED")
        records = {
            line.product_id: (line, product)
            for line, product in db.session.execute(
                select(InventoryLine, Product)
                .join(Product, (Product.id == InventoryLine.product_id)
                      & (Product.bar_id == InventoryLine.bar_id))
                .where(InventoryLine.bar_id == bar_id, InventoryLine.inventory_id == inv.id)
                .with_for_update()
            )
        }
        snapshots = {
            item.inventory_line_id: item
            for item in db.session.scalars(
                select(InventoryLineSnapshot).where(
                    InventoryLineSnapshot.bar_id == bar_id,
                    InventoryLineSnapshot.inventory_line_id.in_(
                        [line.id for line, _ in records.values()] or [-1]
                    ),
                )
            )
        }
        quantities = {}
        notes = {}
        seen_product_ids = set()
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("INVALID_COUNT")
            product_id = product_identifier(entry.get("product_id"))
            if product_id not in records or product_id in seen_product_ids:
                raise ValueError("INVALID_COUNT_PRODUCT")
            seen_product_ids.add(product_id)
            line, product = records[product_id]
            old_state = db.session.scalar(
                select(InventoryLineCountState).where(
                    InventoryLineCountState.bar_id == bar_id,
                    InventoryLineCountState.inventory_line_id == line.id,
                ).with_for_update()
            )
            revision = entry.get("revision")
            if (isinstance(revision, bool) or not isinstance(revision, (int, str))
                    or not str(revision).isascii() or not str(revision).isdigit()
                    or int(revision) != (old_state.revision if old_state else 0)):
                raise ValueError("INVENTORY_COUNT_CONFLICT")
            raw_cases = entry.get("cases")
            raw_units = entry.get("units")
            has_cases = raw_cases is not None and str(raw_cases).strip() != ""
            has_units = raw_units is not None and str(raw_units).strip() != ""
            if not has_cases and not has_units:
                quantity = None
            else:
                cases = 0
                if has_cases:
                    if (isinstance(raw_cases, bool) or not isinstance(raw_cases, (int, str))
                            or not str(raw_cases).isascii() or not str(raw_cases).isdigit()
                            or not product.units_per_case or product.units_per_case <= 1):
                        raise ValueError("INVALID_COUNT")
                    cases = int(raw_cases)
                units = number(raw_units, 6) if has_units else ZERO
                if units < 0:
                    raise ValueError("INVALID_COUNT")
                quantity = number(units + Decimal(cases) * Decimal(product.units_per_case or 1), 6)
            note = entry.get("note", "")
            if not isinstance(note, str) or len(note.strip()) > 500:
                raise ValueError("NOTE_TOO_LONG")
            snapshot = snapshots.get(line.id)
            if snapshot is None:
                raise ValueError("INVENTORY_PERIOD_DATA_MISSING")
            if quantity == line.counted_quantity and note.strip() == (snapshot.note or ""):
                # Reposting a form or saving the same row must not rewrite
                # the physical count timestamp or increase its revision.
                continue
            quantities[str(product_id)] = quantity
            notes[str(product_id)] = note
        if not quantities:
            return inv
        return self.count(actor, bar_id, inventory_id, quantities, notes)

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
        # Fail closed: V2 assignments must wait for the approval workflow.
        if db.session.scalar(
            select(InventoryCashierAssignment.id).where(
                InventoryCashierAssignment.bar_id == bar_id,
                InventoryCashierAssignment.inventory_id == inv.id,
            )
        ) is not None:
            raise ValueError("INVENTORY_V2_PENDING_APPROVAL")
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
