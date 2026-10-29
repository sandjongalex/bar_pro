"""Public self-service onboarding: first bar usable immediately for a 15-day trial.

Approval is not a prerequisite for the trial. Subscription/tenant permissions
enforce read-only mode after the trial ends. Contact verification is not yet
automated and must not be represented as verified.
"""
from datetime import timedelta
import re

from flask import Blueprint, abort, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.audit import record
from app.default_catalog import seed_default_catalog
from app.extensions import db, limiter
from app.models import Bar, Plan, Subscription, User, utcnow
from app.saas_governance_models import BarGovernance, OwnerGovernance

bp = Blueprint("owner_signup", __name__)
TRIAL_CODE = "BARPRO_TRIAL_15D"
TRIAL_DAYS = 15


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


def _trial_plan():
    """One shared plan for all first bars; never expose it to manual checkout."""
    plan = db.session.scalar(select(Plan).where(Plan.code == TRIAL_CODE))
    if plan is None:
        try:
            # The unique code handles concurrent first-time registrations.
            with db.session.begin_nested():
                plan = Plan(
                    code=TRIAL_CODE,
                    name="Essai gratuit de 15 jours",
                    price_amount=0,
                    currency="XAF",
                    duration_days=TRIAL_DAYS,
                    is_active=True,
                )
                db.session.add(plan)
                db.session.flush()
        except IntegrityError:
            plan = db.session.scalar(select(Plan).where(Plan.code == TRIAL_CODE))
            if plan is None:
                raise
    return plan


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

        now = utcnow()
        owner = User(email=email, display_name=name, category="OWNER", is_active=True)
        owner.set_password(password)
        db.session.add(owner)
        db.session.flush()

        bar = Bar(
            owner_id=owner.id, name=bar_name, address=address or None,
            phone=phone, timezone="Africa/Douala", currency="XAF", status="ACTIVE",
        )
        db.session.add(bar)
        db.session.flush()
        # Approval is immediate for registration; the Super Admin retains
        # the power to suspend a suspicious account or bar afterwards.
        db.session.add(OwnerGovernance(
            owner_id=owner.id, status="APPROVED",
            reason="Inscription autonome avec essai immédiat de 15 jours",
            changed_by_id=owner.id, changed_at=now,
        ))
        db.session.add(BarGovernance(
            bar_id=bar.id, status="APPROVED", suspension_level=0,
            reason="Premier établissement activé en essai gratuit",
            changed_by_id=owner.id, changed_at=now,
        ))
        plan = _trial_plan()
        subscription = Subscription(
            bar_id=bar.id, plan_id=plan.id,
            reference=f"TRIAL-15D-{bar.id}", status="ACTIVE",
            plan_code_snapshot=plan.code, plan_name_snapshot=plan.name,
            price_amount_snapshot=0, duration_days_snapshot=TRIAL_DAYS,
            currency="XAF", starts_at=now, ends_at=now + timedelta(days=TRIAL_DAYS),
            activated_at=now, created_by_id=owner.id,
        )
        db.session.add(subscription)
        seed_default_catalog(bar.id)
        record(owner, bar.id, "owners.self_register", "bars", bar.id,
               "Inscription et premier essai activés immédiatement",
               changes={"trial_days": TRIAL_DAYS})
        db.session.commit()

        # Start using the bar straight away, without a second login form.
        login_user(owner)
        session["credentials_version"] = owner.credentials_version
        return redirect(url_for("web.dashboard", bar_id=bar.id))
    except ValueError as exc:
        db.session.rollback()
        error = str(exc)
    except IntegrityError:
        db.session.rollback()
        error = "Inscription impossible pour le moment. Vérifiez les informations ou réessayez."
    return render_template("owner_registration.html", error=error, values=values), 400


@bp.get("/register-owner/sent")
def registered():
    """Compatibility link for earlier drafts: no approval stage remains."""
    return redirect(url_for("auth.web_login"))


@bp.get("/platform/owner-registrations")
@login_required
def registrations_list():
    if current_user.category != "SUPER_ADMIN":
        abort(403)
    registrations = list(db.session.execute(
        select(Bar, Subscription)
        .join(Subscription, Subscription.bar_id == Bar.id)
        .where(Subscription.plan_code_snapshot == TRIAL_CODE)
        .order_by(Bar.created_at.desc()).limit(200)
    ))
    return render_template("owner_registration_review.html", registrations=registrations)


@bp.get("/platform/owner-requests")
@login_required
def legacy_registrations_url():
    return redirect(url_for("owner_signup.registrations_list"))


@bp.get("/bars/<int:bar_id>/subscription")
@login_required
def subscription_status(bar_id):
    if current_user.category != "OWNER":
        abort(403)
    bar = db.session.get(Bar, bar_id)
    if bar is None or bar.owner_id != current_user.id:
        abort(404)
    subscriptions = list(db.session.scalars(
        select(Subscription)
        .where(Subscription.bar_id == bar_id)
        .order_by(Subscription.created_at.desc())
    ))
    trial = next((item for item in subscriptions if item.plan_code_snapshot == TRIAL_CODE), None)
    now = utcnow().replace(tzinfo=None)
    current_paid = next((
        item for item in subscriptions
        if item.plan_code_snapshot != TRIAL_CODE
        and item.status == "ACTIVE" and item.starts_at <= now < item.ends_at
    ), None)
    trial_end = trial.ends_at if trial else None
    return render_template(
        "owner_subscription_status.html", bar=bar, trial=trial,
        current_paid=current_paid, trial_end=trial_end, now=now,
    )
