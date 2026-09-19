"""Expense categories and immutable expense/reversal operations."""
from __future__ import annotations

from sqlalchemy import select

from app.audit import record
from app.cash_services import cash_service
from app.extensions import db
from app.models import Bar, Expense, ExpenseCategory, utcnow
from app.permissions import permissions
from app.validation import number, required_text

PAYMENT_METHODS = {"CASH", "MOBILE_MONEY", "CARD", "BANK_TRANSFER"}


class ExpenseService:
    def category(self, bar_id: int, category_id: int, *, active_only: bool = False):
        query = select(ExpenseCategory).where(
            ExpenseCategory.bar_id == bar_id,
            ExpenseCategory.id == category_id,
        )
        if active_only:
            query = query.where(ExpenseCategory.is_active.is_(True))
        item = db.session.scalar(query)
        if not item:
            raise LookupError("CATEGORY_NOT_FOUND")
        return item

    def create_category(self, actor, bar_id: int, name: str):
        permissions.require(actor, "expenses.manage", bar_id)
        cleaned = required_text(name, 100)
        existing = db.session.scalar(
            select(ExpenseCategory).where(
                ExpenseCategory.bar_id == bar_id,
                ExpenseCategory.name == cleaned,
            )
        )
        if existing:
            if not existing.is_active:
                existing.is_active = True
                record(actor, bar_id, "expense_category.reactivate", "expense_categories", existing.id, cleaned)
            return existing
        item = ExpenseCategory(bar_id=bar_id, name=cleaned, is_active=True)
        db.session.add(item)
        db.session.flush()
        record(actor, bar_id, "expense_category.create", "expense_categories", item.id, cleaned)
        return item

    def set_category_active(self, actor, bar_id: int, category_id: int, active: bool):
        permissions.require(actor, "expenses.manage", bar_id)
        item = self.category(bar_id, category_id)
        item.is_active = bool(active)
        record(
            actor,
            bar_id,
            "expense_category.enable" if active else "expense_category.disable",
            "expense_categories",
            item.id,
            item.name,
        )
        return item

    def create(
        self,
        actor,
        bar_id: int,
        category_id: int,
        reference: str,
        description: str,
        amount,
        method: str,
        incurred_at=None,
        cash_session_id: int | None = None,
        provider_code: str | None = None,
        provider_transaction_id: str | None = None,
    ):
        permissions.require(actor, "expenses.manage", bar_id)
        bar = db.session.get(Bar, bar_id)
        if not bar:
            raise LookupError("NOT_FOUND")
        category = self.category(bar_id, category_id, active_only=True)
        value = number(amount, positive=True)
        method = required_text(method, 16).upper()
        if method not in PAYMENT_METHODS:
            raise ValueError("INVALID_METHOD")

        provider_code = (provider_code or "").strip() or None
        provider_transaction_id = (provider_transaction_id or "").strip() or None
        if bool(provider_code) != bool(provider_transaction_id):
            raise ValueError("PROVIDER_REFERENCE_REQUIRED")
        if provider_code and len(provider_code) > 32:
            raise ValueError("PROVIDER_REFERENCE_INVALID")
        if provider_transaction_id and len(provider_transaction_id) > 128:
            raise ValueError("PROVIDER_REFERENCE_INVALID")

        if method == "CASH":
            if cash_session_id is None:
                raise ValueError("CASH_SESSION_REQUIRED")
            session = cash_service.session(bar_id, cash_session_id)
            if session.currency != bar.currency:
                raise ValueError("CURRENCY_MISMATCH")
        else:
            cash_session_id = None

        item = Expense(
            bar_id=bar_id,
            expense_category_id=category.id,
            reference=required_text(reference, 64),
            description=required_text(description, 500),
            category_name_snapshot=category.name,
            amount=value,
            currency=bar.currency,
            entry_kind="EXPENSE",
            method=method,
            provider_code=provider_code,
            provider_transaction_id=provider_transaction_id,
            cash_session_id=cash_session_id,
            incurred_at=incurred_at or utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(item)
        db.session.flush()

        if method == "CASH":
            cash_service.entry(
                actor,
                bar_id,
                -value,
                bar.currency,
                item.description,
                session_id=cash_session_id,
                expense_id=item.id,
            )

        record(actor, bar_id, "expenses.record", "expenses", item.id, item.description)
        return item

    def reverse(
        self,
        actor,
        bar_id: int,
        expense_id: int,
        reference: str,
        reason: str,
        cash_session_id: int | None = None,
    ):
        permissions.require(actor, "expenses.manage", bar_id)
        source = db.session.scalar(
            select(Expense).where(
                Expense.bar_id == bar_id,
                Expense.id == expense_id,
            ).with_for_update()
        )
        if not source:
            raise LookupError("NOT_FOUND")
        if source.entry_kind != "EXPENSE" or source.reversal_of_id is not None:
            raise ValueError("EXPENSE_NOT_REVERSIBLE")
        if db.session.scalar(
            select(Expense.id).where(
                Expense.bar_id == bar_id,
                Expense.reversal_of_id == source.id,
            )
        ):
            raise ValueError("ALREADY_REVERSED")

        cleaned_reason = required_text(reason, 500)
        if source.method == "CASH":
            if cash_session_id is None:
                raise ValueError("CASH_SESSION_REQUIRED")
            cash_service.session(bar_id, cash_session_id)
        else:
            cash_session_id = None

        item = Expense(
            bar_id=bar_id,
            expense_category_id=source.expense_category_id,
            reference=required_text(reference, 64),
            description=f"Annulation : {cleaned_reason}"[:500],
            category_name_snapshot=source.category_name_snapshot,
            amount=source.amount,
            currency=source.currency,
            entry_kind="REVERSAL",
            reversal_of_id=source.id,
            method=source.method,
            cash_session_id=cash_session_id,
            incurred_at=utcnow(),
            recorded_by_id=actor.id,
        )
        db.session.add(item)
        db.session.flush()

        if source.method == "CASH":
            cash_service.entry(
                actor,
                bar_id,
                source.amount,
                source.currency,
                cleaned_reason,
                session_id=cash_session_id,
                expense_id=item.id,
            )

        record(actor, bar_id, "expenses.reverse", "expenses", item.id, cleaned_reason)
        return item


expense_service = ExpenseService()
