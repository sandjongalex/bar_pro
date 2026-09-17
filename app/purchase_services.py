"""Suppliers, purchases, receipts and supplier settlements."""
from decimal import Decimal

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


def _page(query, page=1, page_size=50):
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 50)))
    return (
        db.session.scalars(query.offset((page - 1) * page_size).limit(page_size + 1)).all(),
        page,
        page_size,
    )


class SupplierService:
    def create(self, actor, bar_id, data):
        permissions.require(actor, "suppliers.manage", bar_id)
        item = Supplier(
            bar_id=bar_id,
            name=required_text(data["name"], 160),
            phone=(data.get("phone") or None),
            email=(data.get("email") or None),
            address=(data.get("address") or None),
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
        for key in ("phone", "email", "address"):
            if key in data:
                setattr(item, key, data.get(key) or None)
        if "is_active" in data:
            item.is_active = bool(data["is_active"])
        record(actor, bar_id, "suppliers.update", "suppliers", item.id, item.name)
        return item


class PurchaseService:
    def create(self, actor, bar_id, supplier_id, reference, lines, supplier_invoice_reference=None):
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
        return _page(query.order_by(Purchase.id.desc()), page, page_size)

    def update(self, actor, bar_id, purchase_id, data):
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._draft(bar_id, purchase_id)
        if "reference" in data:
            purchase.reference = required_text(data["reference"], 64)
        if "supplier_invoice_reference" in data:
            purchase.supplier_invoice_reference = data.get("supplier_invoice_reference") or None
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
                select(Product).where(Product.id == line["product_id"], Product.bar_id == bar_id)
            )
            if not product or not product.is_active:
                raise LookupError("NOT_FOUND")
            qty = number(line["quantity"], 6, positive=True)
            cost = number(line["unit_cost"])
            if cost < 0:
                raise ValueError("INVALID_LINE")
            amount = number(qty * cost)
            total += amount
            db.session.add(
                PurchaseLine(
                    bar_id=bar_id,
                    purchase_id=purchase.id,
                    product_id=product.id,
                    line_no=n,
                    product_name_snapshot=product.name,
                    unit_snapshot=product.base_unit,
                    quantity=qty,
                    unit_cost_snapshot=cost,
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
        lines = list(
            db.session.scalars(
                select(PurchaseLine).where(PurchaseLine.purchase_id == purchase.id, PurchaseLine.bar_id == bar_id)
            )
        )
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

    def cancel(self, actor, bar_id, purchase_id, reason):
        permissions.require(actor, "purchases.manage", bar_id)
        purchase = self._draft(bar_id, purchase_id)
        purchase.status = "CANCELLED"
        purchase.cancelled_at = utcnow()
        record(actor, bar_id, "purchases.cancel", "purchases", purchase.id, required_text(reason))
        return purchase

    def due(self, bar_id, purchase_id):
        purchase = db.session.get(Purchase, purchase_id)
        if not purchase or purchase.bar_id != bar_id:
            raise LookupError("NOT_FOUND")
        paid = db.session.scalar(
            select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
                SupplierPayment.purchase_id == purchase_id,
                SupplierPayment.bar_id == bar_id,
                SupplierPayment.entry_kind == "PAYMENT",
            )
        )
        reversed_amount = db.session.scalar(
            select(func.coalesce(func.sum(SupplierPayment.amount), 0)).where(
                SupplierPayment.purchase_id == purchase_id,
                SupplierPayment.bar_id == bar_id,
                SupplierPayment.entry_kind == "REVERSAL",
            )
        )
        return purchase.total_amount - paid + reversed_amount

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
