from __future__ import annotations

from datetime import datetime
import secrets
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth import api_required
from app.extensions import db
from app.inventory_period_models import InventoryLineSnapshot, InventoryPeriodSnapshot
from app.inventory_assignment_models import InventoryCashierAssignment
from app.inventory_count_models import InventoryLineCountState
from app.shift_models import EmployeeShift
from app.models import StaffAssignment, User
from app.inventory_services import inventory_service
from app.inventory_reconciliation import inventory_live_reconciliation
from app.inventory_approval import evaluation, policy_data, update_policy, submit, decide
from app.inventory_v2_reports import inventory_v2_report
from app.inventory_finance_services import view as finance_view, count_cash, add_expense, request_debt, decide_debt
from app.inventory_approval_models import InventoryApproval
from app.models import Bar, Inventory, InventoryLine, Product
from app.permissions import permissions
from app.product_display_order import product_order_expression

inventories_bp = Blueprint("inventories", __name__, url_prefix="/bars/<int:bar_id>/inventories")
api_inventories_bp = Blueprint("api_inventories", __name__, url_prefix="/api/v1/bars/<int:bar_id>/inventories")


def _message(code):
    messages = {
        "INVALID_END_DATE": "La date de fin d'inventaire est invalide ou située dans le futur.",
        "INVENTORY_PERIOD_OVERLAP": "La période chevauche le dernier inventaire validé.",
        "INVALID_INVENTORY": "Aucun produit actif n'est disponible pour cet inventaire.",
        "COUNT_REQUIRED": "Renseignez les quantités des produits à enregistrer.",
        "INVENTORY_COUNT_CONFLICT": "Une autre saisie a modifié ce produit. Actualisez la page avant de réessayer.",
        "INVENTORY_V2_REQUIRED": "Cette opération est réservée aux inventaires V2.",
        "INVENTORY_SHIFT_CLOSED": "La session de la caissière affectée est terminée.",
        "INVALID_COUNT": "Une quantité physique est invalide.",
        "INVALID_COUNT_PRODUCT": "Un produit du comptage est invalide.",
        "INVENTORY_STALE": "Le stock machine a changé depuis le début de l'inventaire. Annulez ce brouillon et recommencez pour éviter un mauvais ajustement.",
        "INVENTORY_IMMUTABLE": "Cet inventaire est déjà terminé et ne peut plus être modifié.",
        "INVENTORY_PERIOD_DATA_MISSING": "Les données de période de cet inventaire sont incomplètes.",
        "INVENTORY_V2_PENDING_APPROVAL": "Inventaire affecté à une caissière : la validation V2 sera disponible après activation du circuit d'approbation.",
        "INVALID_CASHIER_SHIFT": "Session de caissière invalide.",
        "CASHIER_SHIFT_NOT_FOUND": "Aucune session de caissière active ne correspond à cette sélection.",
        "NOTE_TOO_LONG": "Une note dépasse 500 caractères.",
        "INVENTORY_SUBMITTED_LOCKED": "Ce comptage a été soumis. Demandez un recomptage pour le modifier.",
        "INVENTORY_RECOUNT_REQUIRED": "Des mouvements ou une référence de stock exigent un nouveau comptage.",
        "INVENTORY_COUNT_INCOMPLETE": "Tous les produits doivent être comptés avant soumission.",
        "INVENTORY_JUSTIFICATION_REQUIRED": "Justifiez chaque écart physique avant soumission.",
        "INVENTORY_REVIEW_STATE": "Cet inventaire ne se trouve pas dans un état autorisant cette action.",
        "INVENTORY_FINANCE_DEBTS_PENDING": "Traitez les demandes de dette avant validation.",
        "INVENTORY_CASH_SESSION_REQUIRED": "Aucune session de caisse ouverte.",
        "INVENTORY_CASH_REASON_REQUIRED": "Justifiez la différence de caisse.",
        "INVENTORY_CASH_SESSION_MISMATCH": "La session de caisse a changé.",
        "INVENTORY_DEBT_ALREADY_DECIDED": "Demande déjà traitée.",
        "INVENTORY_DEBT_ORDER_INVALID": "Facture invalide ou mauvais client.",
        "INVENTORY_DEBT_AMOUNT_MISMATCH": "Dette différente du solde réel de la facture.",
        "INVENTORY_EXPENSE_ALREADY_RECORDED": "Dépense déjà enregistrée.",
        "INVENTORY_OWNER_APPROVAL_REQUIRED": "Les plafonds sont dépassés ou il s’agit du premier inventaire : approbation du propriétaire obligatoire.",
        "INVENTORY_SELF_APPROVAL": "La caissière ne peut pas valider son propre inventaire.",
        "INVENTORY_REASON_REQUIRED": "Une justification obligatoire ne doit pas dépasser 400 caractères.",
        "INVALID_INVENTORY_LIMIT": "Les plafonds sont invalides ou supérieurs aux limites de sécurité.",
        "NOT_FOUND": "Inventaire ou produit introuvable.",
        "FORBIDDEN": "Vous n'êtes pas autorisé à gérer cet inventaire.",
    }
    return messages.get(str(code), "Impossible d'effectuer cette opération d'inventaire.")


def _decimal(value):
    return Decimal(value or 0)


def payload(inv):
    cashier_assignment = db.session.scalar(
        select(InventoryCashierAssignment).where(
            InventoryCashierAssignment.bar_id == inv.bar_id,
            InventoryCashierAssignment.inventory_id == inv.id,
        )
    )
    period = db.session.scalar(
        select(InventoryPeriodSnapshot).where(
            InventoryPeriodSnapshot.bar_id == inv.bar_id,
            InventoryPeriodSnapshot.inventory_id == inv.id,
        )
    )
    rows = db.session.execute(
        select(InventoryLine, Product)
        .join(Product, (Product.id == InventoryLine.product_id) & (Product.bar_id == InventoryLine.bar_id))
        .where(InventoryLine.inventory_id == inv.id, InventoryLine.bar_id == inv.bar_id)
        .order_by(product_order_expression(Product.name), Product.name, Product.id)
    ).all()
    line_ids = [line.id for line, _ in rows]
    count_states = {
        state.inventory_line_id: state
        for state in db.session.scalars(
            select(InventoryLineCountState).where(
                InventoryLineCountState.bar_id == inv.bar_id,
                InventoryLineCountState.inventory_line_id.in_(line_ids or [-1]),
            )
        )
    }
    snapshots = {
        item.inventory_line_id: item
        for item in db.session.scalars(
            select(InventoryLineSnapshot).where(
                InventoryLineSnapshot.bar_id == inv.bar_id,
                InventoryLineSnapshot.inventory_line_id.in_(line_ids or [-1]),
            )
        )
    }

    lines = []
    complete = True
    for line, product in rows:
        count_state = count_states.get(line.id)
        snap = snapshots.get(line.id)
        counted = line.counted_quantity
        complete = complete and counted is not None
        theoretical = _decimal(snap.theoretical_quantity) if snap else _decimal(line.expected_quantity_snapshot)
        sold = _decimal(snap.theoretical_sold_quantity) if snap and counted is not None else None
        sales_amount = _decimal(snap.theoretical_sales_amount) if snap and counted is not None else None
        difference = _decimal(counted) - _decimal(line.expected_quantity_snapshot) if counted is not None else None
        case_size = product.units_per_case if product.units_per_case and product.units_per_case > 1 else None
        counted_cases = (int(Decimal(counted) // case_size)
                         if counted is not None and case_size else None)
        counted_units = (Decimal(counted) - counted_cases * case_size
                         if counted is not None and case_size else counted)
        lines.append(
            {
                "line_id": str(line.id),
                "product_id": str(line.product_id),
                "product_name": product.name,
                "sku": product.sku,
                "unit": product.base_unit,
                "units_per_case": product.units_per_case if product.units_per_case and product.units_per_case > 1 else None,
                "counted_cases": str(counted_cases) if counted_cases is not None else None,
                "counted_units": str(counted_units) if counted_units is not None else None,
                "count_revision": count_state.revision if count_state else 0,
                "last_counted_at": count_state.counted_at.isoformat() if count_state and count_state.counted_at else None,
                "sale_price": str(snap.sale_price_snapshot if snap else product.sale_price),
                "opening_quantity": str(snap.opening_quantity if snap else line.expected_quantity_snapshot),
                "purchase_quantity": str(snap.purchase_quantity if snap else 0),
                "theoretical_quantity": str(theoretical),
                "machine_quantity": str(line.expected_quantity_snapshot),
                "physical_quantity": str(counted) if counted is not None else None,
                "sold_quantity": str(sold) if sold is not None else None,
                "theoretical_sales_amount": str(sales_amount) if sales_amount is not None else None,
                "difference_quantity": str(difference) if difference is not None else None,
                "note": snap.note if snap else None,
            }
        )

    difference = _decimal(period.cash_difference_amount) if period else Decimal("0")
    return {
        "id": str(inv.id),
        "reference": inv.reference,
        "v2_assigned": cashier_assignment is not None,
        "financial_metrics_available": cashier_assignment is None,  # V1 only; V2 has a separate stock report
        "cashier_user_id": str(cashier_assignment.cashier_user_id) if cashier_assignment else None,
        "cashier_shift_id": str(cashier_assignment.cashier_shift_id) if cashier_assignment else None,
        "cashier_name": cashier_assignment.cashier_name_snapshot if cashier_assignment else None,
        "reason": inv.reason,
        "status": inv.status,
        "counted_at": inv.counted_at.isoformat() if inv.counted_at else None,
        "posted_at": inv.posted_at.isoformat() if inv.posted_at else None,
        "cancelled_at": inv.cancelled_at.isoformat() if inv.cancelled_at else None,
        "period_start_at": period.period_start_at.isoformat() if period and period.period_start_at else None,
        "period_end_at": period.period_end_at.isoformat() if period else None,
        "theoretical_sales_amount": str(period.theoretical_sales_amount) if period else "0",
        "expenses_amount": str(period.expenses_amount) if period else "0",
        "credit_sales_amount": str(period.credit_sales_amount) if period else "0",
        "expected_cash_amount": str(period.expected_cash_amount) if period else "0",
        "recorded_net_amount": str(period.recorded_net_amount) if period else "0",
        "cash_difference_amount": str(difference),
        "cash_shortage_amount": str(max(-difference, Decimal("0"))),
        "cash_surplus_amount": str(max(difference, Decimal("0"))),
        "complete": bool(lines) and complete,
        "lines": lines,
    }


def body():
    value = request.get_json()
    if not isinstance(value, dict):
        raise ValueError("INVALID_REQUEST")
    return value


def result(inv, status=200):
    db.session.commit()
    return jsonify(success=True, data=payload(inv), meta={}), status


@inventories_bp.get("")
@login_required
def listing(bar_id):
    permissions.require(current_user, "inventory.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    query = Inventory.query.filter_by(bar_id=bar_id)
    if inventory_service._is_cashier(current_user, bar_id):
        query = query.join(
            InventoryCashierAssignment,
            (InventoryCashierAssignment.bar_id == Inventory.bar_id)
            & (InventoryCashierAssignment.inventory_id == Inventory.id),
        ).filter(InventoryCashierAssignment.cashier_user_id == current_user.id)
    rows = query.order_by(Inventory.created_at.desc(), Inventory.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False
    )
    period_by_inventory = {
        item.inventory_id: item
        for item in db.session.scalars(
            select(InventoryPeriodSnapshot).where(
                InventoryPeriodSnapshot.bar_id == bar_id,
                InventoryPeriodSnapshot.inventory_id.in_([row.id for row in rows.items] or [-1]),
            )
        )
    }
    ids = [row.id for row in rows.items] or [-1]
    v2_assignments = {
        item.inventory_id: item
        for item in db.session.scalars(select(InventoryCashierAssignment).where(
            InventoryCashierAssignment.bar_id == bar_id,
            InventoryCashierAssignment.inventory_id.in_(ids),
        ))
    }
    reviews = {
        item.inventory_id: item
        for item in db.session.scalars(select(InventoryApproval).where(
            InventoryApproval.bar_id == bar_id,
            InventoryApproval.inventory_id.in_(ids),
        ))
    }
    return render_template(
        "inventories.html",
        rows=rows,
        v2_assignments=v2_assignments,
        approval_by_inventory=reviews,
        bar=bar,
        bar_id=bar_id,
        period_by_inventory=period_by_inventory,
        can_adjust=permissions.evaluate(current_user, "inventory.adjust", bar_id).allowed,
        can_create=permissions.evaluate(current_user, "inventory.create", bar_id).allowed,
    )


@inventories_bp.route("/new", methods=["GET", "POST"])
@login_required
def new(bar_id):
    permissions.require(current_user, "inventory.create", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")
    local_now = datetime.now(ZoneInfo(bar.timezone))
    defaults = {
        "period_end": local_now.date().isoformat(),
        "reference": f"INV-{local_now:%Y%m%d-%H%M}",
        "reason": "Inventaire physique",
    }
    product_count = db.session.scalar(
        select(db.func.count(Product.id)).where(Product.bar_id == bar_id, Product.is_active.is_(True))
    ) or 0
    previous = db.session.scalar(
        select(Inventory)
        .where(Inventory.bar_id == bar_id, Inventory.status == "POSTED")
        .order_by(Inventory.posted_at.desc(), Inventory.id.desc())
        .limit(1)
    )

    if request.method == "POST":
        defaults.update(
            period_end=request.form.get("period_end", "").strip(),
            reference=request.form.get("reference", "").strip(),
            reason=request.form.get("reason", "").strip(),
        )
        try:
            inv = inventory_service.create(
                current_user,
                bar_id,
                defaults["reference"],
                None,
                defaults["reason"],
                defaults["period_end"],
                request.form.get("cashier_shift_id") or None,
            )
            db.session.commit()
            flash("Inventaire créé. Comptez maintenant les bouteilles réellement présentes.", "success")
            return redirect(url_for("inventories.detail", bar_id=bar_id, inventory_id=inv.id))
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            flash("Référence déjà utilisée." if isinstance(exc, IntegrityError) else _message(exc), "danger")

    cashier_shifts = db.session.execute(
        select(EmployeeShift, User)
        .join(StaffAssignment,
              (StaffAssignment.bar_id == EmployeeShift.bar_id)
              & (StaffAssignment.id == EmployeeShift.staff_assignment_id))
        .join(User, User.id == StaffAssignment.user_id)
        .where(EmployeeShift.bar_id == bar_id,
               EmployeeShift.status == "OPEN",
               EmployeeShift.role_snapshot == "CASHIER",
               StaffAssignment.role == "CASHIER",
               StaffAssignment.ended_at.is_(None),
               User.is_active.is_(True))
        .order_by(EmployeeShift.started_at, EmployeeShift.id)
    ).all()
    return render_template(
        "inventory_new.html",
        cashier_shifts=cashier_shifts,
        bar=bar,
        bar_id=bar_id,
        defaults=defaults,
        product_count=product_count,
        previous=previous,
    )


@inventories_bp.route("/<int:inventory_id>", methods=["GET", "POST"])
@login_required
def detail(bar_id, inventory_id):
    inv = inventory_service.get(current_user, bar_id, inventory_id)
    bar = db.session.get(Bar, bar_id)
    can_adjust = permissions.evaluate(current_user, "inventory.adjust", bar_id).allowed
    can_count = permissions.evaluate(current_user, "inventory.count", bar_id).allowed

    if request.method == "GET" and db.session.scalar(
        select(InventoryCashierAssignment.id).where(
            InventoryCashierAssignment.bar_id == bar_id,
            InventoryCashierAssignment.inventory_id == inv.id,
        )
    ) is not None:
        endpoint = "inventories.v2_count" if inv.status == "DRAFT" else "inventories.v2_report"
        return redirect(url_for(endpoint, bar_id=bar_id, inventory_id=inv.id))

    if request.method == "POST":
        action = request.form.get("action")
        if db.session.scalar(
            select(InventoryCashierAssignment.id).where(
                InventoryCashierAssignment.bar_id == bar_id,
                InventoryCashierAssignment.inventory_id == inv.id,
            )
        ) is not None and action in {"counts", "post"}:
            flash("Utilisez le comptage V2 sécurisé et sa sauvegarde progressive.", "warning")
            return redirect(url_for("inventories.v2_count", bar_id=bar_id, inventory_id=inv.id))
        try:
            if action in {"counts", "post"}:
                quantities = {
                    key[9:]: (value.strip() or "0")
                    for key, value in request.form.items()
                    if key.startswith("quantity_")
                }
                notes = {
                    key[5:]: value
                    for key, value in request.form.items()
                    if key.startswith("note_")
                }
                if quantities:
                    inventory_service.count(current_user, bar_id, inv.id, quantities, notes)
                if action == "post":
                    inventory_service.post(current_user, bar_id, inv.id)
                    flash("Inventaire validé. Le stock physique devient la nouvelle référence.", "success")
                else:
                    flash("Comptage enregistré.", "success")
            elif action == "cancel":
                inventory_service.cancel(current_user, bar_id, inv.id)
                flash("Brouillon d'inventaire annulé.", "success")
            else:
                raise ValueError("INVALID_ACTION")
            db.session.commit()
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")
        return redirect(url_for("inventories.detail", bar_id=bar_id, inventory_id=inv.id))

    return render_template(
        "inventory_detail.html",
        inventory=payload(inv),
        finance=finance_view(current_user, bar_id, inv.id),
        finance_expense_nonce=secrets.token_hex(12),
        bar=bar,
        bar_id=bar_id,
        can_adjust=can_adjust,
        can_count=can_count,
    )


@inventories_bp.route("/<int:inventory_id>/v2-count", methods=["GET", "POST"])
@login_required
def v2_count(bar_id, inventory_id):
    inv = inventory_service.get(current_user, bar_id, inventory_id)
    assigned = db.session.scalar(
        select(InventoryCashierAssignment.id).where(
            InventoryCashierAssignment.bar_id == bar_id,
            InventoryCashierAssignment.inventory_id == inv.id,
        )
    )
    if assigned is None:
        raise LookupError("NOT_FOUND")
    if inv.status != "DRAFT":
        return redirect(url_for("inventories.detail", bar_id=bar_id, inventory_id=inv.id))
    permissions.require(current_user, "inventory.count", bar_id)
    review = db.session.scalar(select(InventoryApproval).where(
        InventoryApproval.bar_id == bar_id,
        InventoryApproval.inventory_id == inv.id,
    ))
    if request.method == "POST":
        is_json = request.is_json
        if is_json:
            value = request.get_json(silent=True)
            entries = value.get("entries") if isinstance(value, dict) else None
        else:
            # The normal HTML form remains usable if JavaScript is disabled.
            product_ids = [
                line.product_id for line in db.session.scalars(
                    select(InventoryLine).where(
                        InventoryLine.bar_id == bar_id,
                        InventoryLine.inventory_id == inv.id,
                    )
                )
            ]
            entries = [
                {
                    "product_id": pid,
                    "cases": request.form.get(f"cases_{pid}", ""),
                    "units": request.form.get(f"units_{pid}", ""),
                    "note": request.form.get(f"note_{pid}", ""),
                    "revision": request.form.get(f"revision_{pid}", "0"),
                }
                for pid in product_ids
            ]
        try:
            inventory_service.count_v2_entries(current_user, bar_id, inv.id, entries)
            db.session.commit()
            data = payload(inv)
            if is_json:
                return jsonify(success=True, data={
                    "complete": data["complete"],
                    "counted": sum(line["physical_quantity"] is not None for line in data["lines"]),
                    "total": len(data["lines"]),
                    "lines": [
                        {"product_id": line["product_id"],
                         "physical_quantity": line["physical_quantity"],
                         "count_revision": line["count_revision"]}
                        for line in data["lines"]
                    ],
                }), 200
            flash("Comptage enregistré. Les quantités non saisies restent non comptées.", "success")
        except (PermissionError, LookupError, ValueError, TypeError, IntegrityError) as exc:
            db.session.rollback()
            if is_json:
                message = _message(exc)
                status = 409 if str(exc) == "INVENTORY_COUNT_CONFLICT" else (
                    403 if isinstance(exc, PermissionError) else 422
                )
                return jsonify(success=False, error={"code": str(exc), "message": message}), status
            flash(_message(exc), "danger")
        return redirect(url_for("inventories.v2_count", bar_id=bar_id, inventory_id=inv.id))
    return render_template(
        "inventory_count_v2.html",
        inventory=payload(inv),
        finance=finance_view(current_user, bar_id, inv.id),
        finance_expense_nonce=secrets.token_hex(12),
        reconciliation=inventory_live_reconciliation(inv),
        approval=evaluation(current_user, bar_id, inv.id),
        can_submit=permissions.evaluate(current_user, "inventory.submit", bar_id).allowed,
        bar=db.session.get(Bar, bar_id),
        bar_id=bar_id,
    )


@inventories_bp.route("/v2-policy", methods=["GET", "POST"])
@login_required
def v2_policy(bar_id):
    permissions.require(current_user, "inventory.policy", bar_id)
    if request.method == "POST":
        try:
            update_policy(
                current_user, bar_id,
                max_unit_difference=request.form.get("max_unit_difference"),
                max_product_value=request.form.get("max_product_value"),
                max_total_value=request.form.get("max_total_value"),
            )
            db.session.commit()
            flash("Plafonds d'inventaire enregistrés.", "success")
        except (ValueError, PermissionError, IntegrityError) as exc:
            db.session.rollback()
            flash(_message(exc), "danger")
        return redirect(url_for("inventories.v2_policy", bar_id=bar_id))
    return render_template("inventory_policy_v2.html", bar=db.session.get(Bar, bar_id),
                           bar_id=bar_id, policy=policy_data(bar_id))


@inventories_bp.post("/<int:inventory_id>/v2-submit")
@login_required
def v2_submit(bar_id, inventory_id):
    try:
        submit(current_user, bar_id, inventory_id)
        db.session.commit()
        flash("Comptage soumis au responsable. Modifications suspendues.", "success")
    except (PermissionError, LookupError, ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(_message(exc), "danger")
    return redirect(url_for("inventories.v2_count", bar_id=bar_id, inventory_id=inventory_id))


@inventories_bp.post("/<int:inventory_id>/v2-decision")
@login_required
def v2_decision(bar_id, inventory_id):
    try:
        choice = request.form.get("decision")
        decide(current_user, bar_id, inventory_id,
               decision=choice, reason=request.form.get("reason"))
        db.session.commit()
        flash("Inventaire validé et stock ajusté." if choice == "approve"
              else "Recomptage demandé à la caissière.", "success")
    except (PermissionError, LookupError, ValueError, IntegrityError) as exc:
        db.session.rollback()
        flash(_message(exc), "danger")
    return redirect(url_for("inventories.v2_count", bar_id=bar_id, inventory_id=inventory_id)
                    if request.form.get("decision") != "approve"
                    else url_for("inventories.detail", bar_id=bar_id, inventory_id=inventory_id))


@api_inventories_bp.post("/<int:inventory_id>/submit")
@api_required
def api_v2_submit(bar_id, inventory_id):
    review = submit(request.api_user, bar_id, inventory_id)
    db.session.commit()
    return jsonify(success=True, data={
        "inventory_id": str(review.inventory_id), "status": review.status,
    }, meta={})


@api_inventories_bp.post("/<int:inventory_id>/decision")
@api_required
def api_v2_decision(bar_id, inventory_id):
    value = body()
    review = decide(request.api_user, bar_id, inventory_id,
                    decision=value.get("decision"), reason=value.get("reason"))
    db.session.commit()
    return jsonify(success=True, data={
        "inventory_id": str(review.inventory_id), "status": review.status,
    }, meta={})


@api_inventories_bp.get("/<int:inventory_id>/approval")
@api_required
def api_v2_approval(bar_id, inventory_id):
    return jsonify(success=True, data=evaluation(request.api_user, bar_id, inventory_id), meta={})


@api_inventories_bp.route("/approval-policy", methods=["GET", "PUT"])
@api_required
def api_v2_policy(bar_id):
    permissions.require(request.api_user, "inventory.policy", bar_id)
    if request.method == "PUT":
        value = body()
        update_policy(request.api_user, bar_id,
                      max_unit_difference=value.get("max_unit_difference"),
                      max_product_value=value.get("max_product_value"),
                      max_total_value=value.get("max_total_value"))
        db.session.commit()
    return jsonify(success=True, data=policy_data(bar_id), meta={})


@inventories_bp.get("/<int:inventory_id>/v2-reconciliation")
@login_required
def v2_reconciliation(bar_id, inventory_id):
    inv = inventory_service.get(current_user, bar_id, inventory_id)
    return jsonify(success=True, data=inventory_live_reconciliation(inv), meta={})


@api_inventories_bp.get("/<int:inventory_id>/reconciliation")
@api_required
def api_reconciliation(bar_id, inventory_id):
    inv = inventory_service.get(request.api_user, bar_id, inventory_id)
    return jsonify(success=True, data=inventory_live_reconciliation(inv), meta={})


@inventories_bp.get("/<int:inventory_id>/v2-report")
@login_required
def v2_report(bar_id, inventory_id):
    data = inventory_v2_report(current_user, bar_id, inventory_id)
    bar = db.session.get(Bar, bar_id)
    template = "inventory_v2_print.html" if request.args.get("print") == "1" else "inventory_v2_report.html"
    return render_template(template, report=data, bar=bar, bar_id=bar_id,
                           finance=finance_view(current_user, bar_id, inventory_id),
                           finance_expense_nonce=secrets.token_hex(12))


@api_inventories_bp.get("/<int:inventory_id>/report")
@api_required
def api_v2_report(bar_id, inventory_id):
    return jsonify(success=True, data=inventory_v2_report(request.api_user, bar_id, inventory_id), meta={})


@inventories_bp.get("/<int:inventory_id>/print")
@login_required
def printable(bar_id, inventory_id):
    inv = inventory_service.get(current_user, bar_id, inventory_id)
    if db.session.scalar(select(InventoryCashierAssignment.id).where(
        InventoryCashierAssignment.bar_id == bar_id,
        InventoryCashierAssignment.inventory_id == inv.id,
    )) is not None:
        return redirect(url_for("inventories.v2_report", bar_id=bar_id,
                                inventory_id=inv.id, print=1))
    bar = db.session.get(Bar, bar_id)
    return render_template(
        "inventory_print.html",
        inventory=payload(inventory_service.get(current_user, bar_id, inventory_id)),
        bar=bar,
    )


@api_inventories_bp.get("")
@api_required
def api_list(bar_id):
    permissions.require(request.api_user, "inventory.read", bar_id)
    query = Inventory.query.filter_by(bar_id=bar_id)
    if inventory_service._is_cashier(request.api_user, bar_id):
        query = query.join(
            InventoryCashierAssignment,
            (InventoryCashierAssignment.bar_id == Inventory.bar_id)
            & (InventoryCashierAssignment.inventory_id == Inventory.id),
        ).filter(InventoryCashierAssignment.cashier_user_id == request.api_user.id)
    rows = query.order_by(Inventory.created_at.desc(), Inventory.id.desc()).paginate(
        page=request.args.get("page", 1, type=int), per_page=20, error_out=False
    )
    return jsonify(success=True, data=[payload(inv) for inv in rows.items], meta={"page": rows.page, "pages": rows.pages, "total": rows.total})


@api_inventories_bp.get("/<int:inventory_id>")
@api_required
def api_detail(bar_id, inventory_id):
    return jsonify(success=True, data=payload(inventory_service.get(request.api_user, bar_id, inventory_id)), meta={})


@api_inventories_bp.post("")
@api_required
def create(bar_id):
    value = body()
    return result(
        inventory_service.create(
            request.api_user,
            bar_id,
            value.get("reference"),
            value.get("product_ids"),
            value.get("reason", "Inventaire physique"),
            value.get("period_end"),
            value.get("cashier_shift_id"),
        ),
        201,
    )


@api_inventories_bp.patch("/<int:inventory_id>/counts")
@api_required
def counts(bar_id, inventory_id):
    value = body()
    inv = inventory_service.get(request.api_user, bar_id, inventory_id)
    v2 = db.session.scalar(
        select(InventoryCashierAssignment.id).where(
            InventoryCashierAssignment.bar_id == bar_id,
            InventoryCashierAssignment.inventory_id == inv.id,
        )
    ) is not None
    if v2:
        # V2 clients must supply the per-line revision; legacy raw quantities
        # would bypass the optimistic conflict guard.
        return result(inventory_service.count_v2_entries(
            request.api_user, bar_id, inventory_id, value.get("entries"),
        ))
    return result(
        inventory_service.count(
            request.api_user, bar_id, inventory_id,
            value.get("quantities"), value.get("notes"),
        )
    )


@api_inventories_bp.post("/<int:inventory_id>/post")
@api_required
def post(bar_id, inventory_id):
    try:
        return result(inventory_service.post(request.api_user, bar_id, inventory_id))
    except ValueError as exc:
        db.session.rollback()
        return jsonify(success=False, error={"code": str(exc), "message": _message(exc), "details": None}), 409


@api_inventories_bp.post("/<int:inventory_id>/cancel")
@api_required
def cancel(bar_id, inventory_id):
    return result(inventory_service.cancel(request.api_user, bar_id, inventory_id))
