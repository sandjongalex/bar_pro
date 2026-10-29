"""Owner self-registration: no implicit tenant access, manual approval and trial."""
from datetime import timedelta

from flask_migrate import upgrade
from sqlalchemy import select

from app import create_app
from app.extensions import db
from app.models import Bar, Subscription, User, utcnow
from app.saas_governance_models import BarGovernance, OwnerGovernance
from app.saas_governance import trial_expired
from app.permissions import permissions


def _app(tmp_path, csrf=False):
    application = create_app("testing", {
        "SECRET_KEY": "test-secret-for-owner-signup",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'signup.sqlite'}",
        "WTF_CSRF_ENABLED": csrf,
    })
    with application.app_context():
        upgrade(directory=application.config["MIGRATIONS_DIRECTORY"])
        admin = User(
            email="platform@example.invalid", display_name="Plateforme",
            category="SUPER_ADMIN", is_active=True
        )
        admin.set_password("Admin-secret-password-123")
        db.session.add(admin)
        db.session.commit()
    return application


def _form(email="owner@example.invalid", phone="+237 699 555 001"):
    return {
        "display_name": "Propriétaire Test", "email": email,
        "phone": phone, "password": "Asecurepass-12345",
        "password_confirm": "Asecurepass-12345", "bar_name": "BAR TEST",
        "address": "Yaoundé", "confirm_authority": "1",
    }


def test_public_signup_requires_validation_and_trial_starts_only_on_approval(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    assert client.get("/register-owner").status_code == 200
    assert client.post("/register-owner", data=_form(), follow_redirects=False).status_code == 302
    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == "owner@example.invalid"))
        bar = db.session.scalar(select(Bar).where(Bar.owner_id == owner.id))
        bar_id = bar.id
        assert not owner.is_active
        assert owner.category == "OWNER"
        assert bar.phone == "+237699555001"
        assert db.session.get(BarGovernance, bar.id).status == "PENDING"
        assert db.session.get(OwnerGovernance, owner.id).status == "PENDING"
        assert not db.session.scalar(select(Subscription.id).where(Subscription.bar_id == bar.id))
    assert client.post("/login", data={
        "email": "owner@example.invalid", "password": "Asecurepass-12345"
    }).status_code == 401
    assert client.post("/register-owner", data=_form()).status_code == 400

    assert client.post("/login", data={
        "email": "platform@example.invalid", "password": "Admin-secret-password-123"
    }).status_code == 302
    assert client.get("/platform/owner-requests").status_code == 200
    assert client.post(f"/platform/owner-requests/{bar_id}/approve", data={}).status_code == 400
    assert client.post(f"/platform/owner-requests/{bar_id}/approve", data={
        "email_checked": "1", "phone_checked": "1"
    }).status_code == 302
    assert client.post(f"/platform/owner-requests/{bar_id}/approve", data={
        "email_checked": "1", "phone_checked": "1"
    }).status_code == 409

    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == "owner@example.invalid"))
        assert owner.is_active
        assert db.session.get(BarGovernance, bar_id).status == "APPROVED"
        trial = db.session.scalar(select(Subscription).where(Subscription.bar_id == bar_id))
        assert trial.status == "ACTIVE"
        assert trial.plan_code_snapshot == "BARPRO_TRIAL_14D"
        assert abs((trial.ends_at - trial.starts_at).total_seconds() - 14 * 86400) < 1
        assert not trial_expired(bar_id)
        assert permissions.evaluate(owner, "orders.create", bar_id).allowed
        # Make the trial expired without violating the date constraint.
        trial.starts_at = utcnow() - timedelta(days=15)
        trial.ends_at = utcnow() - timedelta(days=1)
        db.session.commit()
        assert trial_expired(bar_id)
        assert not permissions.evaluate(owner, "orders.create", bar_id).allowed
        assert permissions.evaluate(owner, "reports.read", bar_id).allowed


def test_reject_and_invalid_registration_do_not_activate_owner(tmp_path):
    app = _app(tmp_path)
    client = app.test_client()
    bad = _form("bad@example.invalid", phone="123")
    assert client.post("/register-owner", data=bad).status_code == 400
    with app.app_context():
        assert db.session.scalar(select(User).where(User.email == "bad@example.invalid")) is None

    assert client.post("/register-owner", data=_form("second@example.invalid")).status_code == 302
    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == "second@example.invalid"))
        bar = db.session.scalar(select(Bar).where(Bar.owner_id == owner.id))
        bar_id = bar.id
    assert client.post("/login", data={
        "email": "platform@example.invalid", "password": "Admin-secret-password-123"
    }).status_code == 302
    assert client.post(f"/platform/owner-requests/{bar_id}/reject", data={
        "reason": "Coordonnées non vérifiables"
    }).status_code == 302
    with app.app_context():
        assert db.session.get(BarGovernance, bar_id).status == "REJECTED"
        owner = db.session.scalar(select(User).where(User.email == "second@example.invalid"))
        assert not owner.is_active
