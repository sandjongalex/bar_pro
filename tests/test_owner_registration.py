"""15-day first-bar trial: immediate login, expiry, renewed access and CSRF."""
from datetime import timedelta
from decimal import Decimal
import re

from flask_migrate import upgrade
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Bar, Plan, Subscription, User, utcnow
from app.saas_governance_models import BarGovernance, OwnerGovernance
from app.saas_governance import trial_expired
from app.permissions import permissions
from app.subscription_services import subscription_service


def _app(tmp_path, csrf=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    app = create_app("testing", {
        "SECRET_KEY": "test-secret-for-owner-signup",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'signup.sqlite'}",
        "WTF_CSRF_ENABLED": csrf,
    })
    with app.app_context():
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        admin = User(
            email="platform@example.invalid", display_name="Plateforme",
            category="SUPER_ADMIN", is_active=True,
        )
        admin.set_password("Admin-secret-password-123")
        db.session.add(admin)
        db.session.commit()
    return app


def _form(email="owner@example.invalid", phone="+237 699 555 001"):
    return {
        "display_name": "Propriétaire Test", "email": email,
        "phone": phone, "password": "Asecurepass-12345",
        "password_confirm": "Asecurepass-12345",
        "bar_name": "BAR TEST", "address": "Yaoundé",
        "confirm_authority": "1",
    }


def test_register_auto_activates_first_bar_for_exactly_15_days(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    assert client.get("/register-owner").status_code == 200
    response = client.post("/register-owner", data=_form())
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/dashboard?bar_id=")

    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == "owner@example.invalid"))
        bar = db.session.scalar(select(Bar).where(Bar.owner_id == owner.id))
        assert owner.is_active
        assert owner.category == "OWNER"
        assert owner.check_password("Asecurepass-12345")
        assert bar.status == "ACTIVE"
        assert bar.phone == "+237699555001"
        assert db.session.get(BarGovernance, bar.id).status == "APPROVED"
        assert db.session.get(OwnerGovernance, owner.id).status == "APPROVED"
        trial = db.session.scalar(select(Subscription).where(Subscription.bar_id == bar.id))
        assert trial is not None
        assert trial.status == "ACTIVE"
        assert trial.plan_code_snapshot == "BARPRO_TRIAL_15D"
        assert trial.duration_days_snapshot == 15
        assert trial.price_amount_snapshot == Decimal(0)
        assert (trial.ends_at - trial.starts_at).total_seconds() == 15 * 86400
        assert not trial_expired(bar.id)
        assert permissions.evaluate(owner, "orders.create", bar.id).allowed

    # No second login required; the owner can see the bar immediately.
    with client.session_transaction() as sess:
        assert sess.get("_user_id") == str(owner.id)
    assert client.get("/bars/").status_code == 200
    assert client.get("/bars/1/subscription").status_code == 200
    assert client.get("/platform/owner-registrations").status_code == 403


def test_end_of_trial_blocks_writes_but_preserves_read_access(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    assert client.post("/register-owner", data=_form()).status_code == 302
    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == "owner@example.invalid"))
        bar = db.session.scalar(select(Bar).where(Bar.owner_id == owner.id))
        trial = db.session.scalar(select(Subscription).where(Subscription.bar_id == bar.id))
        trial.starts_at = utcnow() - timedelta(days=16)
        trial.ends_at = utcnow() - timedelta(days=1)
        db.session.commit()
        assert trial_expired(bar.id)
        assert not permissions.evaluate(owner, "orders.create", bar.id).allowed
        assert not permissions.evaluate(owner, "inventory.create", bar.id).allowed
        assert permissions.evaluate(owner, "reports.read", bar.id).allowed
    assert client.post("/bars/1/orders/new", data={}).status_code == 403
    assert client.get("/bars/1/subscription").status_code == 200
    assert client.get("/bars/").status_code == 200


def test_paid_subscription_restores_access_after_expiry(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    assert client.post("/register-owner", data=_form()).status_code == 302
    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == "owner@example.invalid"))
        bar = db.session.scalar(select(Bar).where(Bar.owner_id == owner.id))
        trial = db.session.scalar(select(Subscription).where(Subscription.bar_id == bar.id))
        trial.starts_at = utcnow() - timedelta(days=16)
        trial.ends_at = utcnow() - timedelta(days=1)
        plan = Plan(code="MONTHLY-TEST", name="Abonnement test",
                    price_amount=Decimal("10000"), currency="XAF",
                    duration_days=30, is_active=True)
        db.session.add(plan)
        db.session.flush()
        pending = Subscription(
            bar_id=bar.id, plan_id=plan.id, reference="MONTHLY-1",
            status="PENDING", plan_code_snapshot=plan.code,
            plan_name_snapshot=plan.name, price_amount_snapshot=plan.price_amount,
            duration_days_snapshot=30, currency="XAF",
            starts_at=utcnow(), ends_at=utcnow()+timedelta(days=30),
            created_by_id=owner.id,
        )
        db.session.add(pending)
        db.session.commit()
        assert trial_expired(bar.id)
        admin = db.session.scalar(select(User).where(User.category == "SUPER_ADMIN"))
        subscription_service.pay(admin, bar.id, pending.id,
                                 "PAYMENT-1", Decimal("10000"))
        db.session.commit()
        assert not trial_expired(bar.id)
        assert permissions.evaluate(owner, "orders.create", bar.id).allowed
        assert db.session.get(Subscription, pending.id).status == "ACTIVE"
        assert db.session.get(Subscription, trial.id).status == "EXPIRED"


def test_second_bar_requires_payment_and_legacy_bars_stay_usable(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    assert client.post("/register-owner", data=_form()).status_code == 302
    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == "owner@example.invalid"))
        second = Bar(owner_id=owner.id, name="BAR 2",
                     timezone="Africa/Douala", currency="XAF")
        legacy_owner = User(email="legacy@example.invalid",
                            display_name="Historique", category="OWNER")
        legacy_owner.set_password("Legacy-password-123")
        db.session.add_all([second, legacy_owner])
        db.session.flush()
        legacy_bar = Bar(owner_id=legacy_owner.id, name="BAR ANCIEN",
                         timezone="Africa/Douala", currency="XAF")
        db.session.add(legacy_bar)
        db.session.commit()
        assert trial_expired(second.id)
        assert not permissions.evaluate(owner, "orders.create", second.id).allowed
        assert not trial_expired(legacy_bar.id)


def test_invalid_duplicate_and_csrf_protections(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    assert client.post("/register-owner", data=_form("bad@example.invalid", "123")).status_code == 400
    with app.app_context():
        assert db.session.scalar(select(User).where(User.email == "bad@example.invalid")) is None
    assert client.post("/register-owner", data=_form()).status_code == 302
    assert client.post("/logout").status_code == 302
    assert client.post("/register-owner", data=_form()).status_code == 400

    secured = _app(tmp_path / "csrf", csrf=True)  # independent database
    secured_client = secured.test_client()
    assert secured_client.post("/register-owner", data=_form()).status_code == 400
    page = secured_client.get("/register-owner").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = secured_client.post(
        "/register-owner", data={**_form(), "csrf_token": token}
    )
    assert response.status_code == 302


def test_signup_is_blocked_when_disabled_and_login_hides_cta(tmp_path):
    app = _app(tmp_path / "disabled")
    app.config["OWNER_SELF_SIGNUP_ENABLED"] = False
    client = app.test_client()
    assert client.get("/register-owner").status_code == 404
    assert client.post("/register-owner", data=_form()).status_code == 404
    login = client.get("/login")
    assert login.status_code == 200
    assert "Créer un compte propriétaire" not in login.get_data(as_text=True)
    with app.app_context():
        assert db.session.scalar(select(User).where(User.email == "owner@example.invalid")) is None
