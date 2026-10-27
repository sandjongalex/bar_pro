"""Inventory-linked financial reconciliation without stock or cash shortcuts."""
from __future__ import annotations

import secrets
from decimal import Decimal

from sqlalchemy import select

from app.audit import record
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_period_models import InventoryPeriodSnapshot
from app.customer_models import CustomerLedgerEntry
from app.shift_service import get_active_shift_for_user
from app.cash_services import cash_service
from app.customer_services import customer_service
from app.expense_services import expense_service
from app.extensions import db
from app.inventory_finance_models import InventoryCashReconciliation, InventoryDebtRequest
from app.inventory_services import inventory_service
from app.models import (
    CashSession, Customer, Expense, ExpenseCategory, Order, Payment, Refund, User, utcnow,
)
from app.permissions import permissions
from app.validation import number, required_text


def _check(actor, bar_id, inventory_id, *, write=False):
    # for_count applies cashier-shift gating and V2 assignment scope.
    inv = inventory_service.get(actor, bar_id, inventory_id, for_count=write)
    if write and inv.status != "DRAFT":
        raise ValueError("INVENTORY_IMMUTABLE")
    if write and inventory_service._is_cashier(actor, bar_id):
        assignment = db.session.scalar(select(InventoryCashierAssignment).where(
            InventoryCashierAssignment.bar_id == bar_id,
            InventoryCashierAssignment.inventory_id == inv.id,
            InventoryCashierAssignment.cashier_user_id == actor.id,
        ))
        active = get_active_shift_for_user(actor.id, bar_id)
        if assignment is None or active is None or active.id != assignment.cashier_shift_id:
            raise PermissionError("INVENTORY_SHIFT_CLOSED")
    return inv


def _active_cash(bar_id):
    return db.session.scalar(select(CashSession).where(
        CashSession.bar_id == bar_id, CashSession.status == "OPEN",
    ).order_by(CashSession.id.desc()).limit(1))


def _saved(bar_id, inventory_id, *, lock=False):
    query = select(InventoryCashReconciliation).where(
        InventoryCashReconciliation.bar_id == bar_id,
        InventoryCashReconciliation.inventory_id == inventory_id,
    )
    if lock:
        query = query.with_for_update()
    return db.session.scalar(query)


def pending_count(bar_id, inventory_id):
    return len(list(db.session.scalars(select(InventoryDebtRequest.id).where(
        InventoryDebtRequest.bar_id == bar_id,
        InventoryDebtRequest.inventory_id == inventory_id,
        InventoryDebtRequest.status == "PENDING",
    ))))


def period_financial_breakdown(bar_id, inventory_id):
    """Auditable period overview, separate from drawer and stock discrepancy.

    Count each expense once by payment method; manual debt is a customer
    receivable, never an automatic cash deduction. Amounts are informational
    and MUST NOT be used to infer personal responsibility.
    """
    period = db.session.scalar(select(InventoryPeriodSnapshot).where(
        InventoryPeriodSnapshot.bar_id == bar_id,
        InventoryPeriodSnapshot.inventory_id == inventory_id,
    ))
    if period is None:
        return None
    start_at, end_at = period.period_start_at, period.period_end_at

    def in_period(column):
        conditions = [column <= end_at]
        if start_at is not None:
            conditions.append(column > start_at)
        return conditions

    expenses = {"CASH": Decimal("0"), "MOBILE_MONEY": Decimal("0"),
                "BANK_TRANSFER": Decimal("0"), "CARD": Decimal("0")}
    for entry in db.session.scalars(select(Expense).where(
        Expense.bar_id == bar_id, *in_period(Expense.incurred_at),
    )):
        sign = 1 if entry.entry_kind == "EXPENSE" else -1
        expenses[entry.method] = expenses.get(entry.method, Decimal("0")) + sign * Decimal(entry.amount)

    payments = {"CASH": Decimal("0"), "MOBILE_MONEY": Decimal("0"),
                "BANK_TRANSFER": Decimal("0"), "CARD": Decimal("0")}
    for entry in db.session.scalars(select(Payment).where(
        Payment.bar_id == bar_id, *in_period(Payment.received_at),
    )):
        payments[entry.method] = payments.get(entry.method, Decimal("0")) + Decimal(entry.amount_applied)

    refunds = {"CASH": Decimal("0"), "MOBILE_MONEY": Decimal("0"),
               "BANK_TRANSFER": Decimal("0"), "CARD": Decimal("0")}
    for entry in db.session.scalars(select(Refund).where(
        Refund.bar_id == bar_id, *in_period(Refund.refunded_at),
    )):
        refunds[entry.method] = refunds.get(entry.method, Decimal("0")) + Decimal(entry.amount)

    linked_credit = Decimal("0")
    manual_credit = Decimal("0")
    for entry in db.session.scalars(select(CustomerLedgerEntry).where(
        CustomerLedgerEntry.bar_id == bar_id,
        CustomerLedgerEntry.entry_kind.in_(("CREDIT_SALE", "REVERSAL", "ADJUSTMENT")),
        *in_period(CustomerLedgerEntry.occurred_at),
    )):
        if entry.order_id is not None and entry.entry_kind in ("CREDIT_SALE", "REVERSAL"):
            linked_credit += Decimal(entry.amount_delta)
        elif entry.order_id is None and entry.entry_kind == "ADJUSTMENT":
            manual_credit += Decimal(entry.amount_delta)

    return {
        "start_at": start_at, "end_at": end_at,
        "expenses_by_method": expenses,
        "payments_by_method": payments,
        "refunds_by_method": refunds,
        "linked_credit": linked_credit,
        "manual_credit": manual_credit,
        "cash_expenses": expenses["CASH"],
        "cash_receipts": payments["CASH"] - refunds["CASH"],
        "pending_debts": pending_count(bar_id, inventory_id),
    }


def view(actor, bar_id, inventory_id):
    inv = _check(actor, bar_id, inventory_id)
    saved = _saved(bar_id, inv.id)
    assignment = db.session.scalar(select(InventoryCashierAssignment).where(
        InventoryCashierAssignment.bar_id == bar_id,
        InventoryCashierAssignment.inventory_id == inv.id,
    ))
    period_breakdown = period_financial_breakdown(bar_id, inv.id)
    live_session = _active_cash(bar_id) if inv.status == "DRAFT" else None
    saved_session = db.session.scalar(select(CashSession).where(
        CashSession.bar_id == bar_id, CashSession.id == saved.cash_session_id,
    )) if saved else None
    # A closed historic count must always show its original session identity.
    current = saved_session or live_session
    live_expected = (cash_service.expected(live_session)
                     if live_session and live_session.status == "OPEN" else None)
    rows = db.session.execute(
        select(InventoryDebtRequest, Customer)
        .join(Customer, (Customer.id == InventoryDebtRequest.customer_id)
              & (Customer.bar_id == InventoryDebtRequest.bar_id))
        .where(InventoryDebtRequest.bar_id == bar_id,
               InventoryDebtRequest.inventory_id == inv.id)
        .order_by(InventoryDebtRequest.id.desc())
    ).all()
    users = {
        user.id: user.display_name
        for user in db.session.scalars(select(User).where(
            User.id.in_([request.requested_by_id for request, _ in rows] or [-1])
        ))
    }
    requests = [{
        "id": req.id,
        "customer": customer.display_name,
        "amount": req.amount,
        "status": req.status,
        "kind": "Vente à crédit (facture)" if req.order_id else "Dette manuelle hors facture",
        "order_id": req.order_id,
        "reason": req.reason,
        "requested_by": users.get(req.requested_by_id, "Utilisateur"),
    } for req, customer in rows]
    can_edit = inv.status == "DRAFT" and permissions.evaluate(
        actor, "inventory.count", bar_id
    ).allowed
    can_decide = can_edit and permissions.evaluate(
        actor, "customer_debt.manage", bar_id
    ).allowed
    return {
        "is_editable": can_edit,
        "period_breakdown": period_breakdown,
        "cashier_name": assignment.cashier_name_snapshot if assignment else None,
        "cashier_shift_linked": bool(assignment and assignment.cashier_shift_id),
        "cash_session_is_cashier_verified": False,
        "can_decide_debts": can_decide,
        "saved": saved,
        "session": current,
        "session_open": live_session is not None and (
            saved is None or live_session.id == saved.cash_session_id),
        "live_expected": live_expected,
        "changed_since_count": saved is not None and (
            live_session is not None and (
                live_session.id != saved.cash_session_id
                or Decimal(live_expected) != Decimal(saved.expected_amount)
            )
        ),
        "categories": list(db.session.scalars(select(ExpenseCategory).where(
            ExpenseCategory.bar_id == bar_id, ExpenseCategory.is_active.is_(True),
        ).order_by(ExpenseCategory.name)).all()),
        "customers": list(db.session.scalars(select(Customer).where(
            Customer.bar_id == bar_id, Customer.is_active.is_(True),
        ).order_by(Customer.display_name).limit(200)).all()),
        "orders": list(db.session.scalars(select(Order).where(
            Order.bar_id == bar_id,
            Order.status.in_(("CONFIRMED", "SERVED")),
        ).order_by(Order.id.desc()).limit(100)).all()),
        "debt_requests": requests,
        "pending_debts": sum(x["status"] == "PENDING" for x in requests),
    }


def count_cash(actor, bar_id, inventory_id, amount, reason=""):
    inv = _check(actor, bar_id, inventory_id, write=True)
    session = _active_cash(bar_id)
    if not session:
        raise ValueError("INVENTORY_CASH_SESSION_REQUIRED")
    # The same lock as all money writes: no stale expected number.
    session = cash_service.session(bar_id, session.id)
    saved = _saved(bar_id, inv.id, lock=True)
    if saved is not None and saved.cash_session_id != session.id:
        raise ValueError("INVENTORY_CASH_SESSION_MISMATCH")
    counted = number(amount)
    if counted < 0:
        raise ValueError("INVALID_COUNTED_AMOUNT")
    expected = Decimal(cash_service.expected(session, locking=True))
    difference = counted - expected
    note = (reason or "").strip()
    if len(note) > 500 or (difference != 0 and not note):
        raise ValueError("INVENTORY_CASH_REASON_REQUIRED")
    if saved is None:
        saved = InventoryCashReconciliation(bar_id=bar_id, inventory_id=inv.id,
                                             cash_session_id=session.id,
                                             counted_by_id=actor.id)
        db.session.add(saved)
    saved.counted_amount = counted
    saved.expected_amount = expected
    saved.opening_amount = session.opening_amount
    saved.difference_amount = difference
    saved.reason = note or None
    saved.counted_by_id = actor.id
    saved.counted_at = utcnow()
    db.session.flush()
    record(actor, bar_id, "inventory.finance.cash_count", "inventory_cash_reconciliations",
           saved.id, note or "Comptage physique conforme", changes={
               "inventory_id": inv.id, "session_id": session.id,
               "expected": str(expected), "counted": str(counted),
               "difference": str(difference),
           })
    return saved


def add_expense(actor, bar_id, inventory_id, *, category_id, amount, description,
                method, nonce):
    inv = _check(actor, bar_id, inventory_id, write=True)
    permissions.require(actor, "expenses.record", bar_id)
    nonce = (nonce or "").strip()
    if not (8 <= len(nonce) <= 48 and nonce.isalnum()):
        raise ValueError("INVALID_REQUEST")
    # One reference per rendered form: duplicate HTTP submissions cannot
    # silently book the same expense twice.
    reference = "INVEXP-" + nonce
    from app.models import Expense
    previous = db.session.scalar(select(Expense.id).where(
        Expense.bar_id == bar_id, Expense.reference == reference,
    ))
    if previous is not None:
        raise ValueError("INVENTORY_EXPENSE_ALREADY_RECORDED")
    method = (method or "").upper()
    session = _active_cash(bar_id) if method == "CASH" else None
    if method == "CASH" and session is None:
        raise ValueError("INVENTORY_CASH_SESSION_REQUIRED")
    note = required_text(description, 400)
    expense = expense_service.create(
        actor, bar_id, category_id, reference,
        f"Inventaire {inv.reference} : {note}",
        amount, method, None, session.id if session is not None else None,
    )
    db.session.flush()
    record(actor, bar_id, "inventory.finance.late_expense", "inventories",
           inv.id, note, changes={"expense_id": expense.id, "amount": str(expense.amount),
                                  "method": method})
    return expense


def request_debt(actor, bar_id, inventory_id, *, customer_id, order_id, amount, reason, nonce=None):
    inv = _check(actor, bar_id, inventory_id, write=True)
    customer = db.session.scalar(select(Customer).where(
        Customer.bar_id == bar_id, Customer.id == customer_id,
        Customer.is_active.is_(True),
    ))
    if customer is None:
        raise LookupError("NOT_FOUND")
    order_id = int(order_id) if order_id else None
    if order_id:
        order = db.session.scalar(select(Order).where(
            Order.bar_id == bar_id, Order.id == order_id,
            Order.status.in_(("CONFIRMED", "SERVED")),
        ))
        if order is None or (order.customer_id is not None and order.customer_id != customer.id):
            raise ValueError("INVENTORY_DEBT_ORDER_INVALID")
    nonce = nonce or secrets.token_hex(12)
    if not (8 <= len(nonce) <= 48 and nonce.isalnum()):
        raise ValueError("INVALID_REQUEST")
    if db.session.scalar(select(InventoryDebtRequest.id).where(
        InventoryDebtRequest.bar_id == bar_id,
        InventoryDebtRequest.request_nonce == nonce,
    )) is not None:
        raise ValueError("INVENTORY_DEBT_DUPLICATE")
    request = InventoryDebtRequest(
        request_nonce=nonce,
        bar_id=bar_id, inventory_id=inv.id, customer_id=customer.id,
        order_id=order_id, amount=number(amount, positive=True),
        reason=required_text(reason, 500), requested_by_id=actor.id,
        status="PENDING",
    )
    db.session.add(request)
    db.session.flush()
    record(actor, bar_id, "inventory.finance.debt_requested", "inventory_debt_requests",
           request.id, request.reason, changes={"inventory_id": inv.id,
                        "linked_order": order_id, "amount": str(request.amount)})
    return request


def decide_debt(actor, bar_id, inventory_id, request_id, *, decision, reason):
    inv = _check(actor, bar_id, inventory_id, write=True)
    permissions.require(actor, "customer_debt.manage", bar_id)
    req = db.session.scalar(select(InventoryDebtRequest).where(
        InventoryDebtRequest.bar_id == bar_id,
        InventoryDebtRequest.inventory_id == inv.id,
        InventoryDebtRequest.id == request_id,
    ).with_for_update())
    if req is None:
        raise LookupError("NOT_FOUND")
    if req.status != "PENDING":
        raise ValueError("INVENTORY_DEBT_ALREADY_DECIDED")
    if decision not in ("approve", "reject"):
        raise ValueError("INVALID_ACTION")
    note = required_text(reason, 500)
    if decision == "approve":
        reference = f"INVDET-{req.id}"
        if req.order_id:
            entry = customer_service.credit_order(actor, bar_id, req.order_id,
                                                   req.customer_id, reference)
            if Decimal(entry.amount_delta) != Decimal(req.amount):
                raise ValueError("INVENTORY_DEBT_AMOUNT_MISMATCH")
        else:
            entry = customer_service.record_manual_debt(
                actor, bar_id, req.customer_id, reference, req.amount,
                f"Inventaire {inv.reference} : {req.reason[:220]} / {note[:180]}",
            )
        db.session.flush()
        req.ledger_entry_id = entry.id
        req.status = "APPROVED"
    else:
        req.status = "REJECTED"
    req.decided_by_id = actor.id
    req.decided_at = utcnow()
    req.decision_reason = note
    db.session.flush()
    record(actor, bar_id, "inventory.finance.debt_" + req.status.lower(),
           "inventory_debt_requests", req.id, note,
           changes={"inventory_id": inv.id, "ledger_entry_id": req.ledger_entry_id})
    return req


def assert_no_pending_debts(bar_id, inventory_id):
    if pending_count(bar_id, inventory_id):
        raise ValueError("INVENTORY_FINANCE_DEBTS_PENDING")
