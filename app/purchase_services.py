"""Suppliers, purchases, receipts and supplier settlements."""
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select

from app.audit import record
from app.cash_services import cash_service
from app.extensions import db
from app.models import (
    Bar,
    Product,
    Purchase,
    PurchaseLine,
    Supplier,
    SupplierPayment,
    utcnow,
)
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text


PAYMENT_METHODS = {"CASH", "CARD", "MOBILE_MONEY", "BANK_TRANSFER"}
PURCHASE_UNITS = {"CASE", "BOTTLE"}


def _page(query, page=1, page_size=50):
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 50)))
    return (
        db.session.scalars(query.offset((page - 1) * page_size).limit(page_size + 1)).all(),
        page,
        page_size,
    )


def _as_purchase_date(value):
    if value in (None, ""):
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValueError("INVALID_PURCHASE_DATE") from None


def _case_size(value):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("UNITS_PER_CASE_REQUIRED") from None
    if parsed <= 0:
        raise ValueError("UNITS_PER_CASE_REQUIRED")
    return parsed


class SupplierService:
    def create(self, actor, bar_id, data):
        permissions.require(actor, "suppliers.manage", bar_id)
        item = Supplier(
            bar_id=bar_id,
            name=required_text(data["name"], 160),
            phone=(data.get("phone") or None),
            email=(data.get("email") or None),
            address=(data.get("address") or None),
            note=(data.get("note") or None),
            is_active=bool(data.get("is_active", True)),
        )
        db.session.add(item)
        db.session.flush()
        record(actor, bar_id, "suppliers.create", "suppliers", item.id, item.name)
        return item

    def list(self, actor, bar_id, q=None, active=None, page=1, page_size=50):
        permissions.require(actor, "suppliers.read", bar_id)
        query = select(Supplier).where(Supplier.bar_id == bar_id)
        if q:
            query = query.where(Supplier.name.ilike(f"%{q}%"))
        if active is not None:
            query = query.where(Supplier.is_active.is_(active))
        return _page(query.order_by(Supplier.name, Supplier.id), page, page_size)

    def get(self, actor, bar_id, supplier_id):
        permissions.require(actor, "suppliers.read", bar_id)
        item = db.session.scalar(select(Supplier).where(Supplier.bar_id == bar_id, Supplier.id == supplier_id))
        if not item:
            raise LookupError("NOT_FOUND")
        return item

    def update(self, actor, bar_id, supplier_id, data):
        permissions.require(actor, "suppliers.manage", bar_id)
        item = db.session.scalar(
            select(Supplier).where(Supplier.bar_id == bar_id, Supplier.id == supplier_id).with_for_update()
        )
        if not item:
            raise LookupError("NOT_FOUND")
        if "name" in data:
            item.name = required_text(data["name"], 160)
        for key in ("phone", "email", "address", "note"):
            if key in data:
                setattr(item, key, data.get(key) or None)
        if "is_active" in data:
            item.is_active = bool(data["is_active"])
        record(actor, bar_id, "suppliers.update", "suppliers", item.id, item.name)
        return item


class PurchaseService:
    def create(
        self,
        actor,
        bar_id,
        supplier_id,
        reference,
        lines,
        supplier_invoice_reference=None,
        purchase_date=None,
        notes=None,
    ):
        permissions.require(actor, "purchases.manage", bar_id)
        supplier = db.session.scalar(
            select(Supplier).where(Supplier.id == supplier_id, Supplier.bar_id == bar_id).with_for_update()
        )
        if not supplier or not supplier.is_active:
            raise LookupError("NOT_FOUND")
        if not lines:
            raise ValueError("PURCHASE_EMPTY")
        purchase = Purchase(
            bar_id=bar_id,
            supplier_id=supplier_id,
            reference=required_text(reference, 64),
            supplier_invoice_reference=(supplier_invoice_reference or None),
            purchase_date=_as_purchase_date(purchase_date),
            notes=(notes or None),
            status="DRAFT",
            currency=db.session.get(Bar, bar_id).currency,
            supplier_name_snapshot=supplier.name,
            subtotal_amount=0,
            discount_amount=0,
            tax_amount=0,
            total_amount=0,
            created_by_id=actor.id,
        )
        db.session.add(purchase)
        db.session.flush()
        self.replace_lines(actor, bar_id, purchase.id, lines)
        record(actor, bar_id, "purchases.create", "purchases", purchase.id, purchase.reference)
        return purchase

    def get(self, actor, bar_id, purchase_id):
        permissions.require(actor, "purchases.read", bar_id)
        item = db.session.scalar(select(Purchase).where(Purchase.bar_id == bar_id, Purchase.id == purchase_id))
        if not item:
            raise LookupError("NOT_FOUND")
        return item

    def list(self, actor, bar_id, status=None, supplier_id=None, page=1, page_size=50):
        permissions.require(actor, "purchases.read", bar_id)
        query = select(Purchase).where(Purchase.bar_id == bar_id)
        if status:
            query = query.where(Purchase.status == status)
        if supplier_id:
            query = query.where(Purchase.supplier_id == int(supplier_id))
        return _page(query.order_by(Purchase.purchase_date.desc(), Purchase.id.desc()), page, page_size)

    def update(self, actor, bar_id, purchase_id, data):
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._draft(bar_id, purchase_id)
        if "reference" in data:
            purchase.reference = required_text(data["reference"], 64)
        if "supplier_invoice_reference" in data:
            purchase.supplier_invoice_reference = data.get("supplier_invoice_reference") or None
        if "purchase_date" in data:
            purchase.purchase_date = _as_purchase_date(data.get("purchase_date"))
        if "notes" in data:
            purchase.notes = data.get("notes") or None
        if "supplier_id" in data:
            supplier = db.session.scalar(
                select(Supplier).where(Supplier.id == data["supplier_id"], Supplier.bar_id == bar_id)
            )
            if not supplier or not supplier.is_active:
                raise LookupError("NOT_FOUND")
            purchase.supplier_id = supplier.id
            purchase.supplier_name_snapshot = supplier.name
        if "lines" in data:
            self.replace_lines(actor, bar_id, purchase_id, data["lines"])
        record(actor, bar_id, "purchases.update", "purchases", purchase.id, purchase.reference)
        return purchase

    def replace_lines(self, actor, bar_id, purchase_id, lines):
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._draft(bar_id, purchase_id)
        if not lines:
            raise ValueError("PURCHASE_EMPTY")
        db.session.query(PurchaseLine).filter_by(bar_id=bar_id, purchase_id=purchase_id).delete()
        total = Decimal("0")

        for n, line in enumerate(lines, 1):
            product = db.session.scalar(
                select(Product).where(Product.id == line["product_id"], Product.bar_id == bar_id).with_for_update()
            )
            if not product or not product.is_active:
                raise LookupError("NOT_FOUND")

            purchase_unit = str(line.get("purchase_unit") or "BOTTLE").upper()
            if purchase_unit not in PURCHASE_UNITS:
                raise ValueError("INVALID_PURCHASE_UNIT")

            entry_quantity = number(
                line.get("purchase_quantity", line.get("quantity")),
                6,
                positive=True,
            )
            entry_unit_price = number(
                line.get("purchase_unit_price", line.get("unit_cost", "0")),
                4,
            )
            if entry_unit_price < 0:
                raise ValueError("INVALID_LINE")

            units_per_case = None
            if purchase_unit == "CASE":
                units_per_case = _case_size(line.get("units_per_case") or product.units_per_case)
                if product.units_per_case != units_per_case:
                    product.units_per_case = units_per_case
                stock_quantity = number(entry_quantity * Decimal(units_per_case), 6, positive=True)
                base_unit_cost = (entry_unit_price / Decimal(units_per_case)).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
            else:
                stock_quantity = entry_quantity
                base_unit_cost = entry_unit_price

            amount = number(entry_quantity * entry_unit_price, 4)
            total += amount
            db.session.add(
                PurchaseLine(
                    bar_id=bar_id,
                    purchase_id=purchase.id,
                    product_id=product.id,
                    line_no=n,
                    product_name_snapshot=product.name,
                    unit_snapshot=product.base_unit,
                    quantity=stock_quantity,
                    unit_cost_snapshot=base_unit_cost,
                    purchase_unit=purchase_unit,
                    purchase_quantity=entry_quantity,
                    units_per_case_snapshot=units_per_case,
                    purchase_unit_price_snapshot=entry_unit_price,
                    subtotal_amount=amount,
                    discount_amount=0,
                    tax_amount=0,
                    total_amount=amount,
                )
            )
        purchase.subtotal_amount = purchase.total_amount = total
        return purchase

    def receive(self, actor, bar_id, purchase_id):
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._draft(bar_id, purchase_id)
        lines = self._lines(bar_id, purchase.id)
        if not lines:
            raise ValueError("PURCHASE_EMPTY")
        for line in lines:
            stock_service.move(
                actor,
                bar_id,
                line.product_id,
                "PURCHASE",
                line.quantity,
                f"Reception achat {purchase.reference}",
                purchase_line_id=line.id,
            )
        purchase.status = "POSTED"
        purchase.posted_at = utcnow()
        record(actor, bar_id, "purchases.receive", "purchases", purchase.id, purchase.reference)
        return purchase

    def reopen(self, actor, bar_id, purchase_id, reason):
        """Reverse a received purchase and create a fresh correction draft.

        Purchase lines referenced by stock movements are immutable audit evidence.
        Reusing those rows as an editable draft would either break their foreign
        keys or rewrite history.  The posted purchase is therefore cancelled after
        an auditable stock reversal and a new draft is copied from it for editing.
        """
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._posted(bar_id, purchase_id)
        if self.net_paid(bar_id, purchase.id) != 0:
            raise ValueError("PURCHASE_HAS_PAYMENTS")
        reason = required_text(reason)
        source_lines = self._lines(bar_id, purchase.id)
        if not source_lines:
            raise ValueError("PURCHASE_EMPTY")

        for line in source_lines:
            stock_service.reverse_purchase_receipt(
                actor,
                bar_id,
                line.id,
                f"Correction achat {purchase.reference}: {reason}",
            )

        purchase.status = "CANCELLED"
        purchase.cancelled_at = utcnow()

        correction_lines = [
            {
                "product_id": line.product_id,
                "purchase_unit": line.purchase_unit,
                "purchase_quantity": line.purchase_quantity,
                "purchase_unit_price": line.purchase_unit_price_snapshot,
                "units_per_case": line.units_per_case_snapshot,
            }
            for line in source_lines
        ]
        correction_reference = f"{purchase.reference[:45]}-CORR-{purchase.id}"
        correction_note = f"Correction de {purchase.reference}: {reason}"
        if purchase.notes:
            correction_note = f"{correction_note} | {purchase.notes}"
        correction = self.create(
            actor,
            bar_id,
            purchase.supplier_id,
            correction_reference,
            correction_lines,
            purchase.supplier_invoice_reference,
            purchase.purchase_date,
            correction_note[:500],
        )
        record(
            actor,
            bar_id,
            "purchases.reopen",
            "purchases",
            purchase.id,
            f"{reason} -> brouillon {correction.reference}",
        )
        return correction

    def cancel_received(self, actor, bar_id, purchase_id, reason):
        """Cancel a received purchase while preserving an auditable stock reversal."""
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._posted(bar_id, purchase_id)
        if self.net_paid(bar_id, purchase.id) != 0:
            raise ValueError("PURCHASE_HAS_PAYMENTS")
        reason = required_text(reason)
        for line in self._lines(bar_id, purchase.id):
            stock_service.reverse_purchase_receipt(
                actor,
                bar_id,
                line.id,
                f"Annulation achat {purchase.reference}: {reason}",
            )
        purchase.status = "CANCELLED"
        purchase.cancelled_at = utcnow()
        record(actor, bar_id, "purchases.cancel_received", "purchases", purchase.id, reason)
        return purchase

    def cancel(self, actor, bar_id, purchase_id, reason):
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._draft(bar_id, purchase_id)
        purchase.status = "CANCELLED"
        purchase.cancelled_at = utcnow()
        record(actor, bar_id, "purchases.cancel", "purchases", purchase.id, required_text(reason))
        return purchase

    def net_paid(self, bar_id, purchase_id):
        payment = db.session.scalar(
            select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
                SupplierPayment.purchase_id == purchase_id,
                SupplierPayment.bar_id == bar_id,
                SupplierPayment.entry_kind == "PAYMENT",
            )
        )
        reversal = db.session.scalar(
            select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
                SupplierPayment.purchase_id == purchase_id,
                SupplierPayment.bar_id == bar_id,
                SupplierPayment.entry_kind == "REVERSAL",
            )
        )
        return Decimal(payment or 0) - Decimal(reversal or 0)

    def due(self, bar_id, purchase_id):
        purchase = db.session.get(Purchase, purchase_id)
        if not purchase or purchase.bar_id != bar_id:
            raise LookupError("NOT_FOUND")
        return purchase.total_amount - self.net_paid(bar_id, purchase_id)

    def pay(
        self,
        actor,
        bar_id,
        purchase_id,
        reference,
        amount,
        method,
        reason,
        cash_session_id=None,
        provider_code=None,
        provider_transaction_id=None,
    ):
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = db.session.scalar(
            select(Purchase).where(Purchase.bar_id == bar_id, Purchase.id == purchase_id).with_for_update()
        )
        if not purchase:
            raise LookupError("NOT_FOUND")
        if purchase.status != "POSTED":
            raise ValueError("PURCHASE_NOT_PAYABLE")
        amount = number(amount, positive=True)
        if amount > self.due(bar_id, purchase_id):
            raise ValueError("SUPPLIER_PAYMENT_LIMIT")
        item = self._payment(
            actor,
            bar_id,
            purchase,
            required_text(reference, 64),
            amount,
            method,
            required_text(reason),
            "PAYMENT",
            cash_session_id,
            provider_code,
            provider_transaction_id,
        )
        record(actor, bar_id, "supplier_payments.record", "supplier_payments", item.id, item.reference)
        return item

    def reverse_payment(self, actor, bar_id, supplier_payment_id, reference, reason, cash_session_id=None):
        permissions.require(actor, "purchases.manage", bar_id)
        source = db.session.scalar(
            select(SupplierPayment)
            .where(SupplierPayment.bar_id == bar_id, SupplierPayment.id == supplier_payment_id)
            .with_for_update()
        )
        if not source:
            raise LookupError("NOT_FOUND")
        if source.entry_kind != "PAYMENT" or source.reversal_of_id is not None:
            raise ValueError("ONLY_PAYMENT_REVERSIBLE")
        if db.session.scalar(
            select(SupplierPayment.id).where(SupplierPayment.bar_id == bar_id, SupplierPayment.reversal_of_id == source.id)
        ):
            raise ValueError("ALREADY_REVERSED")
        purchase = db.session.scalar(
            select(Purchase).where(Purchase.bar_id == bar_id, Purchase.id == source.purchase_id).with_for_update()
        )
        item = self._payment(
            actor,
            bar_id,
            purchase,
            required_text(reference, 64),
            source.amount,
            source.method,
            required_text(reason),
            "REVERSAL",
            cash_session_id if source.method == "CASH" else None,
            None,
            None,
            source.id,
        )
        record(actor, bar_id, "supplier_payments.reverse", "supplier_payments", item.id, item.reference)
        return item

    def _draft(self, bar_id, purchase_id):
        purchase = db.session.scalar(
            select(Purchase).where(Purchase.id == purchase_id, Purchase.bar_id == bar_id).with_for_update()
        )
        if not purchase:
            raise LookupError("NOT_FOUND")
        if purchase.status != "DRAFT":
            raise ValueError("PURCHASE_NOT_DRAFT")
        return purchase

    def _posted(self, bar_id, purchase_id):
        purchase = db.session.scalar(
            select(Purchase).where(Purchase.id == purchase_id, Purchase.bar_id == bar_id).with_for_update()
        )
        if not purchase:
            raise LookupError("NOT_FOUND")
        if purchase.status != "POSTED":
            raise ValueError("PURCHASE_NOT_POSTED")
        return purchase

    def _lines(self, bar_id, purchase_id):
        return list(
            db.session.scalars(
                select(PurchaseLine)
                .where(PurchaseLine.purchase_id == purchase_id, PurchaseLine.bar_id == bar_id)
                .order_by(PurchaseLine.line_no)
            )
        )

    def _payment(
        self,
        actor,
        bar_id,
        purchase,
        reference,
        amount,
        method,
        reason,
        entry_kind,
        cash_session_id=None,
        provider_code=None,
        provider_transaction_id=None,
        reversal_of_id=None,
    ):
        if method not in PAYMENT_METHODS:
            raise ValueError("INVALID_METHOD")
        if method == "CASH":
            if cash_session_id is None or provider_code is not None or provider_transaction_id is not None:
                raise ValueError("CASH_LOCATION_REQUIRED")
            session = cash_service.session(bar_id, cash_session_id)
            if session.currency != purchase.currency:
                raise ValueError("CURRENCY_MISMATCH")
        elif cash_session_id is not None or (provider_code is None) != (provider_transaction_id is None):
            raise ValueError("PROVIDER_REFERENCE_REQUIRED")
        elif provider_code is not None:
            provider_code = required_text(provider_code, 32)
            provider_transaction_id = required_text(provider_transaction_id, 128)
        item = SupplierPayment(
            bar_id=bar_id,
            purchase_id=purchase.id,
            reference=reference,
            amount=amount,
            currency=purchase.currency,
            entry_kind=entry_kind,
            reversal_of_id=reversal_of_id,
            method=method,
            provider_code=provider_code,
            provider_transaction_id=provider_transaction_id,
            cash_session_id=cash_session_id,
            paid_at=utcnow(),
            recorded_by_id=actor.id,
            reason=reason,
        )
        db.session.add(item)
        db.session.flush()
        if method == "CASH":
            cash_service.entry(
                actor,
                bar_id,
                -amount if entry_kind == "PAYMENT" else amount,
                purchase.currency,
                reason,
                session_id=cash_session_id,
                supplier_payment_id=item.id,
            )
        return item


supplier_service = SupplierService()
purchase_service = PurchaseService()
