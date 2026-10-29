"""Public owner registration and supervised onboarding for BAR_PRO.

No ownership, staff or commercial privileges are granted before platform approval.
E-mail and phone checks are performed manually by the platform during this first
release; no SMS/email delivery provider is assumed.
"""
from datetime import timedelta
import re

from flask import Blueprint, abort, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import record
from app.default_catalog import seed_default_catalog
from app.extensions import db, limiter
from app.models import Bar, Plan, Subscription, User, utcnow
from app.saas_governance_models import BarGovernance, OwnerGovernance

bp = Blueprint("owner_signup", __name__)

TRIAL_CODE = "BARPRO_TRIAL_14D"


def _normalize_phone(value):
    digits = re.sub(r"[^0-9]", "", value or "")
    if len(digits) == 9 and digits.startswith("6"):
        return "+237" + digits
    if len(digits) == 12 and digits.startswith("2376"):
        return "+" + digits
    raise ValueError("Numéro camerounais invalide (exemple : +237 6XX XXX XXX).")


def _check_signup(form):
    name = (form.get("display_name") or "").strip()
    email = (form.get("email") or "").strip().lower()
    password = form.get("password") or ""
    confirm = form.get("password_confirm") or ""
    bar_name = (form.get("bar_name") or "").strip()
    address = (form.get("address") or "").strip()
    phone = _normalize_phone(form.get("phone"))
    if not name or len(name) > 120:
        raise ValueError("Le nom doit contenir entre 1 et 120 caractères.")
    if not email or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ValueError("Adresse e-mail invalide.")
    if len(password) < 12 or len(password) > 256:
        raise ValueError("Le mot de passe doit contenir entre 12 et 256 caractères.")
    if password != confirm:
        raise ValueError("Les mots de passe ne correspondent pas.")
    if not bar_name or len(bar_name) > 160:
        raise ValueError("Le nom du bar est obligatoire (160 caractères maximum).")
    if len(address) > 500:
        raise ValueError("L'adresse est trop longue.")
    if form.get("confirm_authority") != "1":
        raise ValueError("Confirmez être autorisé à gérer cet établissement.")
    return name, email, password, bar_name, address, phone


@bp.route("/register-owner", methods=["GET", "POST"])
@limiter.limit("3 per hour", methods=["POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("web.dashboard"))
    if request.method == "GET":
        return render_template("owner_registration.html", error=None, values={})
    values = {key: request.form.get(key, "") for key in (
        "display_name", "email", "phone", "bar_name", "address"
    )}
    try:
        name, email, password, bar_name, address, phone = _check_signup(request.form)
        if db.session.scalar(select(User.id).where(User.email == email)):
            raise ValueError("Cette adresse e-mail est déjà enregistrée.")

        owner = User(email=email, display_name=name, category="OWNER", is_active=False)
        owner.set_password(password)
        db.session.add(owner)
        db.session.flush()

        bar = Bar(
            owner_id=owner.id, name=bar_name, address=address or None,
            phone=phone, timezone="Africa/Douala", currency="XAF"
        )
        db.session.add(bar)
        db.session.flush()
        now = utcnow()
        db.session.add(OwnerGovernance(
            owner_id=owner.id, status="PENDING", reason="Inscription publique à vérifier",
            changed_by_id=owner.id, changed_at=now
        ))
        db.session.add(BarGovernance(
            bar_id=bar.id, status="PENDING", suspension_level=0,
            reason="En attente de vérification et validation de l'établissement",
            changed_by_id=owner.id, changed_at=now
        ))
        seed_default_catalog(bar.id)
        record(owner, bar.id, "owners.self_register", "bars", bar.id,
               "Demande publique en attente d'approbation")
        db.session.commit()
        return redirect(url_for("owner_signup.registered"))
    except ValueError as exc:
        db.session.rollback()
        error = str(exc)
    except IntegrityError:
        db.session.rollback()
        error = "Ce compte existe déjà. Utilisez une autre adresse e-mail."
    return render_template("owner_registration.html", error=error, values=values), 400


@bp.get("/register-owner/sent")
def registered():
    return render_template("owner_registration_sent.html")


def _super_admin_only():
    if not current_user.is_authenticated or current_user.category != "SUPER_ADMIN":
        abort(403)


@bp.get("/platform/owner-requests")
@login_required
def requests_list():
    _super_admin_only()
    requests = list(db.session.scalars(
        select(Bar)
        .join(BarGovernance, BarGovernance.bar_id == Bar.id)
        .where(BarGovernance.status == "PENDING")
        .order_by(Bar.created_at.asc())
    ))
    return render_template("owner_registration_review.html", requests=requests)


@bp.post("/platform/owner-requests/<int:bar_id>/<action>")
@login_required
def review(bar_id, action):
    _super_admin_only()
    if action not in {"approve", "reject"}:
        abort(404)
    bar = db.session.scalar(select(Bar).where(Bar.id == bar_id).with_for_update())
    state = db.session.scalar(select(BarGovernance).where(BarGovernance.bar_id == bar_id).with_for_update())
    owner = db.session.scalar(select(User).where(User.id == bar.owner_id).with_for_update()) if bar else None
    owner_state = db.session.scalar(select(OwnerGovernance).where(OwnerGovernance.owner_id == owner.id).with_for_update()) if owner else None
    if not bar or not owner or not state or not owner_state or state.status != "PENDING" or owner_state.status != "PENDING":
        db.session.rollback()
        abort(409, "La demande n'est plus en attente.")
    if action == "approve" and (
        request.form.get("email_checked") != "1" or request.form.get("phone_checked") != "1"
    ):
        db.session.rollback()
        abort(400, "Vérifiez d'abord personnellement l'e-mail et le téléphone.")
    now = utcnow()
    approved = action == "approve"
    status = "APPROVED" if approved else "REJECTED"
    reason = "Coordonnées vérifiées manuellement par le Super Admin" if approved else (
        (request.form.get("reason") or "").strip()[:500] or "Demande rejetée après examen"
    )
    state.status = status
    state.reason = reason
    state.changed_by_id = current_user.id
    state.changed_at = now
    owner_state.status = status
    owner_state.reason = reason
    owner_state.changed_by_id = current_user.id
    owner_state.changed_at = now
    owner.is_active = approved

    if approved:
        # First bar only. The trial begins on approval, never on sign-up.
        other_bar = db.session.scalar(select(Bar.id).where(Bar.owner_id == owner.id, Bar.id != bar.id).limit(1))
        if other_bar is None:
            trial = db.session.scalar(select(Plan).where(Plan.code == TRIAL_CODE))
            if trial is None:
                trial = Plan(
                    code=TRIAL_CODE, name="Essai gratuit de 14 jours",
                    price_amount=0, currency="XAF", duration_days=14, is_active=True
                )
                db.session.add(trial)
                db.session.flush()
            db.session.add(Subscription(
                bar_id=bar.id, plan_id=trial.id,
                reference="TRIAL-14D-" + str(bar.id),
                status="ACTIVE", plan_code_snapshot=trial.code,
                plan_name_snapshot=trial.name, price_amount_snapshot=0,
                duration_days_snapshot=14, currency="XAF",
                starts_at=now, ends_at=now + timedelta(days=14),
                activated_at=now, created_by_id=current_user.id
            ))
    record(current_user, bar.id,
           "owners.registration.approve" if approved else "owners.registration.reject",
           "bars", bar.id, reason)
    db.session.commit()
    return redirect(url_for("owner_signup.requests_list"))
