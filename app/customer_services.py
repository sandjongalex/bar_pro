"""Customer accounts, credit sales, returnable cases and server notifications."""
from __future__ import annotations

from decimal import Decimal
from datetime import date
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

    def update(self, actor, bar_id, customer_id, data):
        """Update customer identity without changing debt, stock or cash."""
        permissions.require(actor, "customers.manage", bar_id)
        item = db.session.scalar(
            select(Customer).where(Customer.id == customer_id, Customer.bar_id == bar_id).with_for_update()
        )
        if not item:
            raise LookupError("NOT_FOUND")
        name = required_text(data.get("display_name"), 160)
        phone = (data.get("phone") or "").strip() or None
        email = (data.get("email") or "").strip() or None
        if phone and len(phone) > 32:
            raise ValueError("INVALID_CUSTOMER_PHONE")
        if email and ("@" not in email or len(email) > 254):
            raise ValueError("INVALID_CUSTOMER_EMAIL")
        item.display_name = name
        item.phone = phone
        item.email = email
        db.session.flush()
        record(actor, bar_id, "customers.update", "customers", item.id, item.display_name)
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

    def debt(self, bar_id, customer_id, *, locking=False):
        if locking:
            # Read committed entries after the customer's row lock, not an old
            # MySQL REPEATABLE READ snapshot during two concurrent settlements.
            values = db.session.scalars(select(CustomerLedgerEntry.amount_delta).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.customer_id == customer_id,
                CustomerLedgerEntry.entry_kind != "ADJUSTMENT",
            ).with_for_update())
            return sum((Decimal(x or 0) for x in values), Decimal("0"))
        value = db.session.scalar(
            select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.customer_id == customer_id,
                CustomerLedgerEntry.entry_kind != "ADJUSTMENT",
            )
        )
        return Decimal(value or 0)

    def manual_debt_balance(self, bar_id, customer_id, *, locking=False):
        """Off-cash manual balance, kept separate from order-linked credit."""
        query = select(CustomerLedgerEntry.amount_delta).where(
            CustomerLedgerEntry.bar_id == bar_id,
            CustomerLedgerEntry.customer_id == customer_id,
            CustomerLedgerEntry.entry_kind == "ADJUSTMENT",
        )
        if locking:
            return sum((Decimal(value or 0) for value in db.session.scalars(
                query.with_for_update()
            )), Decimal("0"))
        value = db.session.scalar(
            select(func.coalesce(func.sum(CustomerLedgerEntry.amount_delta), 0)).where(
                CustomerLedgerEntry.bar_id == bar_id,
                CustomerLedgerEntry.customer_id == customer_id,
                CustomerLedgerEntry.entry_kind == "ADJUSTMENT",
            )
        )
        return Decimal(value or 0)

    def record_manual_debt(self, actor, bar_id, customer_id, reference, amount, reason, due_date=None):
        """Track a customer debt without creating an order, stock movement or cash entry."""
        permissions.require(actor, "customer_debt.manage", bar_id)
        customer = db.session.scalar(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.bar_id == bar_id,
                Customer.is_active.is_(True),
            ).with_for_update()
        )
        if not customer:
            raise LookupError("NOT_FOUND")
        amount = number(amount, positive=True)
        note = required_text(reason, 500)
        try:
            deadline = date.fromisoformat(due_date) if due_date else None
        except (TypeError, ValueError):
            raise ValueError("INVALID_DEBT_DUE_DATE") from None
        bar = db.session.get(Bar, bar_id)
        item = CustomerLedgerEntry(
            bar_id=bar_id,
            customer_id=customer.id,
            order_id=None,
            reference=required_text(reference, 64),
            entry_kind="ADJUSTMENT",
            amount_delta=amount,
            currency=bar.currency,
            reason=note,
            due_date=deadline,
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(item)
        db.session.flush()
        record(actor, bar_id, "customer_debt.manual_add", "customer_ledger_entries", item.id, item.reference)
        return item

    def record_manual_repayment(self, actor, bar_id, customer_id, reference, amount, reason):
        """Reduce manual debt only; do not record a cash or order payment."""
        permissions.require(actor, "customer_debt.manage", bar_id)
        customer = db.session.scalar(
            select(Customer).where(
                Customer.id == customer_id,
                Customer.bar_id == bar_id,
                Customer.is_active.is_(True),
            ).with_for_update()
        )
        if not customer:
            raise LookupError("NOT_FOUND")
        amount = number(amount, positive=True)
        note = required_text(reason, 500)
        if amount > self.manual_debt_balance(bar_id, customer.id, locking=True):
            raise ValueError("MANUAL_DEBT_REPAYMENT_LIMIT")
        bar = db.session.get(Bar, bar_id)
        item = CustomerLedgerEntry(
            bar_id=bar_id,
            customer_id=customer.id,
            order_id=None,
            reference=required_text(reference, 64),
            entry_kind="ADJUSTMENT",
            amount_delta=-amount,
            currency=bar.currency,
            reason=note,
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(item)
        db.session.flush()
        record(actor, bar_id, "customer_debt.manual_repayment", "customer_ledger_entries", item.id, item.reference)
        return item

    def reverse_manual_debt(self, actor, bar_id, entry_id, reference, reason):
        """Append an audited reversal, never delete the debt or touch stock/cash.

        Without payment allocation by debt, reject reversals after subsequent
        repayments so an unrelated debt cannot silently absorb the correction.
        """
        permissions.require(actor, "customer_debt.manage", bar_id)
        source = db.session.scalar(select(CustomerLedgerEntry).where(
            CustomerLedgerEntry.id == entry_id,
            CustomerLedgerEntry.bar_id == bar_id,
            CustomerLedgerEntry.entry_kind == "ADJUSTMENT",
            CustomerLedgerEntry.amount_delta > 0,
            CustomerLedgerEntry.reversal_of_id.is_(None),
        ).with_for_update())
        if not source:
            raise LookupError("NOT_FOUND")
        customer = db.session.scalar(select(Customer).where(
            Customer.bar_id == bar_id, Customer.id == source.customer_id
        ).with_for_update())
        if not customer:
            raise LookupError("NOT_FOUND")
        reversed_already = db.session.scalar(select(CustomerLedgerEntry.id).where(
            CustomerLedgerEntry.bar_id == bar_id,
            CustomerLedgerEntry.reversal_of_id == source.id,
        ).with_for_update())
        if reversed_already:
            raise ValueError("ALREADY_REVERSED")
        later_repayments = db.session.scalar(select(CustomerLedgerEntry.id).where(
            CustomerLedgerEntry.bar_id == bar_id,
            CustomerLedgerEntry.customer_id == source.customer_id,
            CustomerLedgerEntry.entry_kind == "ADJUSTMENT",
            CustomerLedgerEntry.amount_delta < 0,
            CustomerLedgerEntry.id > source.id,
            CustomerLedgerEntry.reversal_of_id.is_(None),
        ).limit(1).with_for_update())
        if later_repayments or self.manual_debt_balance(bar_id, source.customer_id, locking=True) < source.amount_delta:
            raise ValueError("MANUAL_DEBT_HAS_REPAYMENTS")
        item = CustomerLedgerEntry(
            bar_id=bar_id,
            customer_id=source.customer_id,
            order_id=None,
            reference=required_text(reference, 64),
            entry_kind="ADJUSTMENT",
            amount_delta=-source.amount_delta,
            currency=source.currency,
            reversal_of_id=source.id,
            reason=required_text(reason, 500),
            occurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(item)
        db.session.flush()
        record(actor, bar_id, "customer_debt.manual_reverse", "customer_ledger_entries",
               item.id, item.reason, changes={"reversal_of_id": source.id, "amount": str(item.amount_delta)})
        return item

    def case_balance(self, bar_id, customer_id, product_id=None, *, locking=False):
        query = select(CustomerCaseEntry.quantity_delta).where(
            CustomerCaseEntry.bar_id == bar_id,
            CustomerCaseEntry.customer_id == customer_id,
        )
        if product_id is not None:
            query = query.where(CustomerCaseEntry.product_id == product_id)
        if locking:
            return sum((int(x or 0) for x in db.session.scalars(query.with_for_update())), 0)
        return sum((int(x or 0) for x in db.session.scalars(query)), 0)

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

        due = order_balance(order, locking=True)["amount_due"]
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
            select(Customer).where(Customer.id == customer_id, Customer.bar_id == bar_id, Customer.is_active.is_(True)).with_for_update()
        )
        if not customer:
            raise LookupError("NOT_FOUND")
        amount = number(amount, positive=True)
        debt = self.debt(bar_id, customer_id, locking=True)
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
            select(Customer).where(Customer.id == customer_id, Customer.bar_id == bar_id, Customer.is_active.is_(True)).with_for_update()
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
            if qty > self.case_balance(bar_id, customer_id, product_id, locking=True):
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


def outstanding_manual_debts(entries, today=None):
    """Allocate manual repayments oldest-first to identify unpaid debt due dates.

    Input entries must belong to one bar/customer. Credit sales and cash receipts
    are deliberately excluded from this computation.
    """
    today = today or date.today()
    open_debts = {}
    for entry in sorted(entries, key=lambda item: item.id):
        if entry.entry_kind != "ADJUSTMENT":
            continue
        amount = Decimal(entry.amount_delta)
        if amount > 0:
            open_debts[entry.id] = [entry, amount]
        elif entry.reversal_of_id is not None:
            # A reversal targets its original debt, not an unrelated entry.
            if entry.reversal_of_id in open_debts:
                open_debts[entry.reversal_of_id][1] = max(
                    Decimal(0), open_debts[entry.reversal_of_id][1] + amount
                )
        else:
            remainder = -amount
            for row in open_debts.values():
                used = min(row[1], remainder)
                row[1] -= used
                remainder -= used
                if remainder <= 0:
                    break
    unpaid = [(entry, amount) for entry, amount in open_debts.values() if amount > 0]
    overdue = sum((amount for entry, amount in unpaid
                   if entry.due_date is not None and entry.due_date < today), Decimal(0))
    next_due = min((entry.due_date for entry, amount in unpaid if entry.due_date), default=None)
    return {"unpaid": unpaid, "overdue": overdue, "next_due": next_due}
