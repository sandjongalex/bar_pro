"""Customer accounts, credit sales, returnable cases and server notifications."""
from __future__ import annotations

from decimal import Decimal
from sqlalchemy import func, select

from app.audit import record
from app.cash_services import cash_service
from app.customer_models import CustomerCaseEntry, CustomerLedgerEntry, UserNotification
from app.extensions import db
from app.finance_totals import order_balance
from app.models import Bar, Customer, Order, Product, StaffAssignment, utcnow
from app.permissions import permissions
from app.validation import number, required_text


class CustomerService:
    def create(self, actor, bar_id, data):
        permissions.require(actor, "customers.manage", bar_id)
        name = required_text(data.get("display_name"), 160)
        phone = (data.get("phone") or "").strip() or None
        email = (data.get("email") or "").strip() or None
        if phone and len(phone) > 32:
            raise ValueError("INVALID_CUSTOMER_PHONE")
        if email and ("@" not in email or len(email) > 254):
            raise ValueError("INVALID_CUSTOMER_EMAIL")
        item = Customer(
            bar_id=bar_id,
            display_name=name,
            phone=phone,
            email=email,
            is_active=True,
        )
        db.session.add(item)
        db.session.flush()
        record(actor, bar_id, "customers.create", "customers", item.id, item.display_name)
        return item

    def set_active(self, actor, bar_id, customer_id, active):
        permissions.require(actor, "customers.manage", bar_id)
        item = db.session.scalar(
            select(Customer).where(Customer.id == customer_id, Customer.bar_id == bar_id).with_for_update()
        )
        if not item:
            raise LookupError("NOT_FOUND")
        item.is_active = bool(active)
        record(actor, bar_id, "customers.activate" if active else "customers.deactivate", "customers", item.id, item.display_name)
        return item

    def debt(self, bar_id, customer_id):
        value = db.session.scalar(
            select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.customer_id == customer_id,
            )
        )
        return Decimal(value or 0)

    def case_balance(self, bar_id, customer_id, product_id=None):
        query = select(func.coalesce(func.sum(CustomerCaseEntry.quantity_delta), 0)).where(
            CustomerCaseEntry.bar_id == bar_id,
            CustomerCaseEntry.customer_id == customer_id,
        )
        if product_id is not None:
            query = query.where(CustomerCaseEntry.product_id == product_id)
        return int(db.session.scalar(query) or 0)

    def credit_order(self, actor, bar_id, order_id, customer_id, reference):
        permissions.require(actor, "customer_credit.manage", bar_id)
        bar = db.session.get(Bar, bar_id)
        if not bar or not bar.credit_sales_enabled:
            raise ValueError("CREDIT_DISABLED")
        customer = db.session.scalar(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.bar_id == bar_id,
                Customer.is_active.is_(True),
            )
        )
        if not customer:
            raise LookupError("CUSTOMER_REQUIRED")
        order = db.session.scalar(
            select(Order).where(Order.id == order_id, Order.bar_id == bar_id).with_for_update()
        )
        if not order:
            raise LookupError("NOT_FOUND")
        if order.status not in {"CONFIRMED", "SERVED"}:
            raise ValueError("ORDER_NOT_PAYABLE")
        if order.customer_id is not None and order.customer_id != customer.id:
            raise ValueError("CUSTOMER_MISMATCH")
        if db.session.scalar(
            select(CustomerLedgerEntry.id).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.order_id == order.id,
                CustomerLedgerEntry.entry_kind == "CREDIT_SALE",
            )
        ):
            raise ValueError("ORDER_ALREADY_CREDITED")

        due = order_balance(order)["amount_due"]
        if due <= 0:
            raise ValueError("ORDER_NOT_PAYABLE")
        entry = CustomerLedgerEntry(
            bar_id=bar_id,
            customer_id=customer.id,
            order_id=order.id,
            reference=required_text(reference, 64),
            entry_kind="CREDIT_SALE",
            amount_delta=due,
            currency=order.currency,
            method=None,
            reason=f"Vente à crédit {order.reference}",
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(entry)
        order.customer_id = customer.id
        order.customer_name_snapshot = customer.display_name
        db.session.flush()
        order_balance(order, update=True)
        notify_server(order, "PAYMENT_VALIDATED", "Paiement validé", f"Commande {order.reference} soldée à crédit pour {customer.display_name}.")
        record(actor, bar_id, "customer_credit.sale", "customer_ledger_entries", entry.id, entry.reference)
        return entry

    def record_payment(self, actor, bar_id, customer_id, reference, amount, method, reason="", cash_session_id=None, provider_code=None, provider_transaction_id=None):
        permissions.require(actor, "customer_credit.manage", bar_id)
        customer = db.session.scalar(
            select(Customer).where(Customer.id == customer_id, Customer.bar_id == bar_id, Customer.is_active.is_(True))
        )
        if not customer:
            raise LookupError("NOT_FOUND")
        amount = number(amount, positive=True)
        debt = self.debt(bar_id, customer_id)
        if amount > debt:
            raise ValueError("CUSTOMER_PAYMENT_LIMIT")
        if method not in {"CASH", "MOBILE_MONEY", "CARD", "BANK_TRANSFER"}:
            raise ValueError("INVALID_METHOD")
        if (provider_code is None) != (provider_transaction_id is None):
            raise ValueError("PROVIDER_REFERENCE_REQUIRED")
        if method == "MOBILE_MONEY" and (not provider_code or not provider_transaction_id):
            raise ValueError("PROVIDER_REFERENCE_REQUIRED")
        if method == "CASH":
            if cash_session_id is None:
                raise ValueError("CASH_LOCATION_REQUIRED")
            session = cash_service.session(bar_id, cash_session_id)
            currency = session.currency
        else:
            if cash_session_id is not None:
                raise ValueError("INVALID_NONCASH_PAYMENT")
            currency = db.session.get(Bar, bar_id).currency
        entry = CustomerLedgerEntry(
            bar_id=bar_id,
            customer_id=customer.id,
            order_id=None,
            reference=required_text(reference, 64),
            entry_kind="PAYMENT",
            amount_delta=-amount,
            currency=currency,
            method=method,
            provider_code=required_text(provider_code, 32) if provider_code else None,
            provider_transaction_id=required_text(provider_transaction_id, 128) if provider_transaction_id else None,
            cash_session_id=cash_session_id,
            reason=(reason or f"Règlement client {customer.display_name}").strip(),
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(entry)
        db.session.flush()
        if method == "CASH":
            cash_service.entry(
                actor,
                bar_id,
                amount,
                currency,
                f"Règlement client {reference}",
                session_id=cash_session_id,
            )
        record(actor, bar_id, "customer_credit.payment", "customer_ledger_entries", entry.id, entry.reference)
        return entry

    def reverse_credit_and_cancel(self, actor, bar_id, entry_id, reference, reason):
        permissions.require(actor, "customer_credit.manage", bar_id)
        source = db.session.scalar(
            select(CustomerLedgerEntry).where(
                CustomerLedgerEntry.id == entry_id,
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.entry_kind == "CREDIT_SALE",
            ).with_for_update()
        )
        if not source or source.order_id is None:
            raise LookupError("NOT_FOUND")
        if db.session.scalar(
            select(CustomerLedgerEntry.id).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.reversal_of_id == source.id,
            )
        ):
            raise ValueError("ALREADY_REVERSED")
        if self.debt(bar_id, source.customer_id) < source.amount_delta:
            raise ValueError("CUSTOMER_CREDIT_ALREADY_SETTLED")
        reversal = CustomerLedgerEntry(
            bar_id=bar_id,
            customer_id=source.customer_id,
            order_id=source.order_id,
            reference=required_text(reference, 64),
            entry_kind="REVERSAL",
            amount_delta=-source.amount_delta,
            currency=source.currency,
            reversal_of_id=source.id,
            reason=required_text(reason, 500),
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(reversal)
        db.session.flush()
        order = db.session.get(Order, source.order_id)
        order_balance(order, update=True)
        from app.order_services import order_service
        order_service.cancel(actor, bar_id, order.id, reason)
        record(actor, bar_id, "customer_credit.reverse", "customer_ledger_entries", reversal.id, reversal.reference)
        return reversal

    def record_case(self, actor, bar_id, customer_id, product_id, quantity, direction, reason, order_id=None):
        permissions.require(actor, "cases.manage", bar_id)
        customer = db.session.scalar(
            select(Customer).where(Customer.id == customer_id, Customer.bar_id == bar_id, Customer.is_active.is_(True))
        )
        product = db.session.scalar(
            select(Product).where(Product.id == product_id, Product.bar_id == bar_id, Product.is_active.is_(True))
        )
        if not customer or not product:
            raise LookupError("NOT_FOUND")
        try:
            qty = int(quantity)
        except (TypeError, ValueError):
            raise ValueError("INVALID_CASE_QUANTITY") from None
        if qty <= 0:
            raise ValueError("INVALID_CASE_QUANTITY")
        if direction == "OUT":
            delta = qty
        elif direction == "RETURN":
            delta = -qty
            if qty > self.case_balance(bar_id, customer_id, product_id):
                raise ValueError("CASE_RETURN_LIMIT")
        else:
            raise ValueError("INVALID_CASE_DIRECTION")
        if order_id is not None:
            order = db.session.scalar(select(Order).where(Order.id == order_id, Order.bar_id == bar_id))
            if not order:
                raise LookupError("NOT_FOUND")
        entry = CustomerCaseEntry(
            bar_id=bar_id,
            customer_id=customer.id,
            product_id=product.id,
            order_id=order_id,
            quantity_delta=delta,
            reason=required_text(reason, 500),
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(entry)
        db.session.flush()
        record(actor, bar_id, "cases.out" if delta > 0 else "cases.return", "customer_case_entries", entry.id, entry.reason)
        return entry



def notify_server(order, kind, title, body):
    if not order or order.assigned_staff_id is None:
        return None
    assignment = db.session.get(StaffAssignment, order.assigned_staff_id)
    if not assignment:
        return None
    existing = db.session.scalar(
        select(UserNotification.id).where(
            UserNotification.bar_id == order.bar_id,
            UserNotification.user_id == assignment.user_id,
            UserNotification.order_id == order.id,
            UserNotification.kind == kind,
        )
    )
    if existing:
        return None
    item = UserNotification(
        bar_id=order.bar_id,
        user_id=assignment.user_id,
        order_id=order.id,
        kind=kind,
        title=required_text(title, 160),
        body=required_text(body, 500),
    )
    db.session.add(item)
    return item


customer_service = CustomerService()
