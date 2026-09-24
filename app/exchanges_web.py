"""Server requests and cashier validation of independent bottle exchanges."""
import uuid

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased, joinedload

from app.cashier_returns_web import _local_display
from app.exchange_services import exchange_service
from app.extensions import db
from app.models import Bar, BeverageExchange, Product, StaffAssignment, User
from app.permissions import permissions

bp = Blueprint("exchanges_web", __name__, url_prefix="/bars/<int:bar_id>/exchanges")
MESSAGES = {
    "CHEAPER_EXCHANGE_NOT_SUPPORTED": "Le remplacement coûte moins cher. La règle de remboursement reste à définir ; cet échange ne peut pas être enregistré.",
    "SUPPLEMENT_REQUIRED": "Encaissez exactement le supplément indiqué avant de valider.",
    "BOTTLES_CHECK_REQUIRED": "Confirmez la réception de bouteilles fermées et revendables.",
    "INSUFFICIENT_STOCK": "Stock insuffisant pour la boisson de remplacement. Aucun mouvement enregistré.",
    "CASH_SESSION_NOT_OPEN": "Ouvrez une session de caisse avant d'encaisser le supplément.",
    "DIFFERENT_PRODUCTS_REQUIRED": "Choisissez deux boissons différentes.",
    "WHOLE_BOTTLES_REQUIRED": "Saisissez un nombre entier de bouteilles.",
    "EXCHANGE_NOT_PENDING": "Cet échange a déjà été traité.",
    "NOT_FOUND": "Boisson, serveuse ou échange indisponible dans cet établissement.",
}


@bp.route("", methods=["GET", "POST"])
@login_required
def manage(bar_id):
    permissions.require(current_user, "orders.read", bar_id)
    can_post = permissions.evaluate(current_user, "exchanges.post", bar_id).allowed
    bar = db.session.get(Bar, bar_id)
    if request.method == "POST":
        try:
            action = request.form.get("action")
            if action == "request":
                staff_id = request.form.get("staff_id", type=int)
                if not can_post:
                    staff_id = db.session.scalar(select(StaffAssignment.id).where(
                        StaffAssignment.bar_id == bar_id, StaffAssignment.user_id == current_user.id,
                        StaffAssignment.ended_at.is_(None), StaffAssignment.role == "SERVER"))
                exchange_service.request(
                    current_user, bar_id, request.form.get("reference"), staff_id,
                    request.form.get("returned_id", type=int), request.form.get("replacement_id", type=int),
                    request.form.get("returned_quantity"), request.form.get("replacement_quantity"),
                    request.form.get("reason", ""))
                message = "Demande d'échange enregistrée. En attente de validation par la caisse."
            elif action == "post":
                exchange_service.post(current_user, bar_id, request.form.get("exchange_id", type=int),
                                      request.form.get("amount_received"), request.form.get("bottles_checked") == "yes")
                message = "Échange validé : stock mis à jour et supplément éventuel encaissé."
            elif action == "cancel":
                exchange_service.cancel(current_user, bar_id, request.form.get("exchange_id", type=int))
                message = "Demande annulée."
            else:
                raise ValueError("INVALID_ACTION")
            db.session.commit()
            flash(message, "success")
        except PermissionError:
            db.session.rollback()
            raise
        except (ValueError, LookupError, IntegrityError) as exc:
            db.session.rollback()
            flash(MESSAGES.get(str(exc), "Opération refusée. Vérifiez les boissons, les quantités et les champs obligatoires."), "danger")
        return redirect(url_for("exchanges_web.manage", bar_id=bar_id))

    staff_query = select(StaffAssignment).options(joinedload(StaffAssignment.user)).where(
        StaffAssignment.bar_id == bar_id, StaffAssignment.role == "SERVER",
        StaffAssignment.ended_at.is_(None)).join(User).where(User.is_active.is_(True))
    if not can_post:
        staff_query = staff_query.where(StaffAssignment.user_id == current_user.id)
    staff = list(db.session.scalars(staff_query.order_by(StaffAssignment.id)))
    products = list(db.session.scalars(select(Product).where(
        Product.bar_id == bar_id, Product.is_active.is_(True)).order_by(Product.name)))
    server_user, cashier_user = aliased(User), aliased(User)
    query = (select(BeverageExchange, server_user.display_name, cashier_user.display_name)
             .join(StaffAssignment, StaffAssignment.id == BeverageExchange.staff_assignment_id)
             .join(server_user, server_user.id == StaffAssignment.user_id)
             .outerjoin(cashier_user, cashier_user.id == BeverageExchange.decided_by_id)
             .where(BeverageExchange.bar_id == bar_id))
    if not can_post:
        query = query.where(StaffAssignment.user_id == current_user.id)
    page = max(1, request.args.get("page", 1, type=int))
    rows = db.session.execute(query.order_by(BeverageExchange.created_at.desc(), BeverageExchange.id.desc())
                              .offset((page - 1) * 30).limit(31)).all()
    return render_template("exchanges.html", bar=bar, can_post=can_post, staff=staff, products=products,
                           rows=rows[:30], has_next=len(rows) > 30, page=page,
                           reference="ECH-" + uuid.uuid4().hex,
                           local_display=lambda value: _local_display(value, bar.timezone))
