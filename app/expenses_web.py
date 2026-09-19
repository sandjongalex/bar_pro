"""Server-rendered expense management for owners and bar administrators."""
from __future__ import annotations

import secrets
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.expense_services import expense_service
from app.extensions import db
from app.models import Bar, CashSession, Expense, ExpenseCategory, User
from app.permissions import permissions

bp = Blueprint("expenses_web", __name__, url_prefix="/bars/<int:bar_id>/expenses")

METHOD_LABELS = {
    "CASH": "Espèces",
    "MOBILE_MONEY": "Mobile Money",
    "CARD": "Carte",
    "BANK_TRANSFER": "Virement bancaire",
}


def _reference(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(2).upper()}"


def _bar(bar_id: int):
    permissions.require(current_user, "expenses.read", bar_id)
    item = db.session.get(Bar, bar_id)
    if not item:
        raise LookupError("NOT_FOUND")
    return item


def _period_bounds(bar: Bar, start_value: str, end_value: str):
    tz = ZoneInfo(bar.timezone)
    try:
        start_day = datetime.fromisoformat(start_value).date()
        end_day = datetime.fromisoformat(end_value).date()
    except (TypeError, ValueError):
        raise ValueError("INVALID_PERIOD") from None
    if end_day < start_day:
        raise ValueError("INVALID_PERIOD")
    start_local = datetime.combine(start_day, time.min, tzinfo=tz)
    end_local = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=tz)
    return (
        start_local.astimezone(timezone.utc).replace(tzinfo=None),
        end_local.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _default_period(bar: Bar):
    today = datetime.now(ZoneInfo(bar.timezone)).date()
    return today.replace(day=1).isoformat(), today.isoformat()


def _parse_local_datetime(bar: Bar, raw: str | None):
    if not raw:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        local = datetime.fromisoformat(raw)
    except ValueError:
        raise ValueError("INVALID_EXPENSE_DATE") from None
    if local.tzinfo is None:
        local = local.replace(tzinfo=ZoneInfo(bar.timezone))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def _message(exc):
    code = str(exc)
    messages = {
        "CATEGORY_NOT_FOUND": "La catégorie sélectionnée est introuvable ou inactive.",
        "INVALID_METHOD": "Le mode de paiement sélectionné est invalide.",
        "PROVIDER_REFERENCE_REQUIRED": "Renseignez le prestataire et la référence de transaction ensemble.",
        "PROVIDER_REFERENCE_INVALID": "La référence du prestataire est trop longue.",
        "CASH_SESSION_REQUIRED": "Une caisse ouverte est obligatoire pour une dépense en espèces.",
        "CASH_SESSION_NOT_OPEN": "La caisse sélectionnée n'est plus ouverte.",
        "INSUFFICIENT_DRAWER_CASH": "Le montant dépasse les espèces théoriquement disponibles dans la caisse.",
        "EXPENSE_NOT_REVERSIBLE": "Cette écriture ne peut pas être annulée.",
        "ALREADY_REVERSED": "Cette dépense a déjà été annulée.",
        "INVALID_EXPENSE_DATE": "La date de dépense est invalide.",
        "INVALID_PERIOD": "La période sélectionnée est invalide.",
        "POSITIVE_NUMBER_REQUIRED": "Le montant doit être strictement supérieur à zéro.",
        "INVALID_NUMBER": "Le montant saisi est invalide.",
        "INVALID_TEXT": "Un champ obligatoire est vide ou trop long.",
    }
    return messages.get(code, "Opération impossible. Vérifiez les informations saisies.")


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id: int):
    bar = _bar(bar_id)
    can_manage = permissions.evaluate(current_user, "expenses.manage", bar_id).allowed

    if request.method == "POST":
        if not can_manage:
            raise PermissionError("FORBIDDEN")
        action = request.form.get("action", "")
        try:
            if action == "category_create":
                expense_service.create_category(current_user, bar_id, request.form.get("name", ""))
                db.session.commit()
                flash("Catégorie de dépense enregistrée.", "success")

            elif action in {"category_enable", "category_disable"}:
                expense_service.set_category_active(
                    current_user,
                    bar_id,
                    int(request.form.get("category_id", "0")),
                    action == "category_enable",
                )
                db.session.commit()
                flash("Catégorie mise à jour.", "success")

            elif action == "expense_create":
                session_raw = (request.form.get("cash_session_id") or "").strip()
                expense_service.create(
                    current_user,
                    bar_id,
                    int(request.form.get("expense_category_id", "0")),
                    _reference("DEP"),
                    request.form.get("description", ""),
                    request.form.get("amount", ""),
                    request.form.get("method", ""),
                    _parse_local_datetime(bar, request.form.get("incurred_at")),
                    int(session_raw) if session_raw else None,
                    request.form.get("provider_code"),
                    request.form.get("provider_transaction_id"),
                )
                db.session.commit()
                flash("Dépense enregistrée.", "success")

            elif action == "expense_reverse":
                session_raw = (request.form.get("cash_session_id") or "").strip()
                expense_service.reverse(
                    current_user,
                    bar_id,
                    int(request.form.get("expense_id", "0")),
                    _reference("ANN-DEP"),
                    request.form.get("reason", ""),
                    int(session_raw) if session_raw else None,
                )
                db.session.commit()
                flash("Dépense annulée par une écriture de contrepassation.", "success")

            else:
                raise ValueError("INVALID_ACTION")

        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if isinstance(exc, IntegrityError):
                flash("Cette catégorie ou cette référence existe déjà.", "danger")
            else:
                flash(_message(exc), "danger")
        return redirect(url_for("expenses_web.manage", bar_id=bar_id))

    default_start, default_end = _default_period(bar)
    start = (request.args.get("start") or default_start).strip()
    end = (request.args.get("end") or default_end).strip()
    category_id = request.args.get("category_id", type=int)
    method = (request.args.get("method") or "").strip().upper()
    search_text = (request.args.get("q") or "").strip()

    try:
        start_at, end_at = _period_bounds(bar, start, end)
    except ValueError:
        flash("La période sélectionnée est invalide.", "danger")
        return redirect(url_for("expenses_web.manage", bar_id=bar_id, start=default_start, end=default_end))

    categories = list(
        db.session.scalars(
            select(ExpenseCategory)
            .where(ExpenseCategory.bar_id == bar_id)
            .order_by(ExpenseCategory.is_active.desc(), ExpenseCategory.name, ExpenseCategory.id)
        )
    )
    active_categories = [item for item in categories if item.is_active]

    filters = [
        Expense.bar_id == bar_id,
        Expense.incurred_at >= start_at,
        Expense.incurred_at < end_at,
    ]
    if category_id:
        filters.append(Expense.expense_category_id == category_id)
    if method in METHOD_LABELS:
        filters.append(Expense.method == method)
    if search_text:
        pattern = f"%{search_text}%"
        filters.append(or_(Expense.reference.ilike(pattern), Expense.description.ilike(pattern)))

    rows = list(
        db.session.scalars(
            select(Expense)
            .where(*filters)
            .order_by(Expense.incurred_at.desc(), Expense.id.desc())
            .limit(300)
        )
    )

    reversal_source_ids = {
        item.reversal_of_id for item in rows if item.entry_kind == "REVERSAL" and item.reversal_of_id is not None
    }
    source_ids = [item.id for item in rows if item.entry_kind == "EXPENSE"]
    if source_ids:
        reversal_source_ids.update(
            db.session.scalars(
                select(Expense.reversal_of_id).where(
                    Expense.bar_id == bar_id,
                    Expense.entry_kind == "REVERSAL",
                    Expense.reversal_of_id.in_(source_ids),
                )
            )
        )

    recorder_ids = {item.recorded_by_id for item in rows}
    recorders = {
        item.id: item
        for item in db.session.scalars(select(User).where(User.id.in_(recorder_ids)))
    } if recorder_ids else {}

    gross = sum((Decimal(item.amount or 0) for item in rows if item.entry_kind == "EXPENSE"), Decimal("0"))
    reversed_total = sum((Decimal(item.amount or 0) for item in rows if item.entry_kind == "REVERSAL"), Decimal("0"))
    net = gross - reversed_total

    by_category = defaultdict(lambda: {"gross": Decimal("0"), "reversed": Decimal("0"), "net": Decimal("0"), "count": 0})
    for item in rows:
        bucket = by_category[item.category_name_snapshot]
        amount = Decimal(item.amount or 0)
        if item.entry_kind == "EXPENSE":
            bucket["gross"] += amount
            bucket["count"] += 1
        else:
            bucket["reversed"] += amount
        bucket["net"] = bucket["gross"] - bucket["reversed"]
    category_summary = [
        {"name": name, **values}
        for name, values in sorted(by_category.items(), key=lambda pair: (-pair[1]["net"], pair[0].casefold()))
    ]

    tz = ZoneInfo(bar.timezone)
    now_local = datetime.now(tz)
    today_start = datetime.combine(now_local.date(), time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    tomorrow_start = datetime.combine(now_local.date() + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    today_rows = list(
        db.session.scalars(
            select(Expense).where(
                Expense.bar_id == bar_id,
                Expense.incurred_at >= today_start,
                Expense.incurred_at < tomorrow_start,
            )
        )
    )
    today_net = sum(
        (
            Decimal(item.amount or 0) if item.entry_kind == "EXPENSE" else -Decimal(item.amount or 0)
            for item in today_rows
        ),
        Decimal("0"),
    )

    open_sessions = list(
        db.session.scalars(
            select(CashSession)
            .where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")
            .order_by(CashSession.id.desc())
        )
    )

    return render_template(
        "expenses.html",
        bar=bar,
        can_manage=can_manage,
        categories=categories,
        active_categories=active_categories,
        rows=rows,
        recorders=recorders,
        reversal_source_ids=reversal_source_ids,
        category_summary=category_summary,
        open_sessions=open_sessions,
        method_labels=METHOD_LABELS,
        stats={
            "gross": gross,
            "reversed": reversed_total,
            "net": net,
            "today_net": today_net,
            "count": sum(1 for item in rows if item.entry_kind == "EXPENSE"),
        },
        filters={
            "start": start,
            "end": end,
            "category_id": category_id,
            "method": method,
            "q": search_text,
        },
        now_local=now_local.strftime("%Y-%m-%dT%H:%M"),
    )
