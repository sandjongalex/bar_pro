"""Optional integration test using a *separate, empty* MySQL test database.

Never points at the app's existing MySQL production database, never drops
tables, and refuses a nonempty database to avoid damaging real business data.
Run with OWNER_SIGNUP_MYSQL_TEST_URI and OWNER_SIGNUP_MYSQL_TEST_CONFIRM.
"""
from datetime import timedelta
from decimal import Decimal
import os

import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db
from app.models import Bar, Plan, Subscription, User, utcnow
from app.permissions import permissions
from app.saas_governance import trial_expired
from app.subscription_services import subscription_service


def _isolated_mysql_url():
    uri = os.environ.get("OWNER_SIGNUP_MYSQL_TEST_URI")
    if not uri:
        pytest.skip("MySQL integration disabled; configure OWNER_SIGNUP_MYSQL_TEST_URI.")
    if os.getenv("OWNER_SIGNUP_MYSQL_TEST_CONFIRM") != "ISOLATED_MYSQL_TEST":
        pytest.fail("Isolation not confirmed: set OWNER_SIGNUP_MYSQL_TEST_CONFIRM=ISOLATED_MYSQL_TEST.")
    try:
        url = make_url(uri)
    except Exception:
        pytest.fail("Invalid OWNER_SIGNUP_MYSQL_TEST_URI.")
    # A deliberately unmistakable name, e.g. sandjong$barpro_test.
    if url.drivername != "mysql+pymysql" or not url.database or not (
        url.database == "barpro_test"
        or url.database.endswith("$barpro_test")
        or url.database.endswith("_barpro_test")
    ):
        pytest.fail("Refusing MySQL test URI: database must be named barpro_test (or end in $barpro_test / _barpro_test).")
    prod_uri = os.environ.get("SQLALCHEMY_DATABASE_URI")
    if prod_uri:
        prod = make_url(prod_uri)
        if (url.host, url.port or 3306, url.database) == (
            prod.host, prod.port or 3306, prod.database
        ):
            pytest.fail("Refusing to use the production database for tests.")
    return uri


@pytest.mark.mysql_integration
def test_owner_trial_lifecycle_on_isolated_mysql():
    uri = _isolated_mysql_url()
    app = create_app("testing", {
        "SECRET_KEY": "isolated-test-only-secret-do-not-use-in-production",
        "SQLALCHEMY_DATABASE_URI": uri,
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        # No cleanup and no drop: never run migrations over existing data.
        existing_tables = inspect(db.engine).get_table_names()
        if existing_tables:
            pytest.fail(
                "MySQL test schema is not empty; refusing to migrate/modify it. "
                "Create a new, empty, dedicated MySQL schema ending in barpro_test."
            )
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])

        admin = User(
            email="platform.owner.mysql@example.invalid",
            display_name="Super Admin TEST", category="SUPER_ADMIN", is_active=True,
        )
        admin.set_password("SuperAdmin-Test-Strong-2026")
        db.session.add(admin)
        db.session.commit()

    client = app.test_client()
    form = {
        "display_name": "Propriétaire MySQL", "email": "owner.mysql@example.invalid",
        "phone": "+237699555001", "password": "Owner-Test-Strong-2026",
        "password_confirm": "Owner-Test-Strong-2026",
        "bar_name": "BAR MYSQL TEST", "address": "Yaoundé",
        "confirm_authority": "1",
    }
    registration = client.post("/register-owner", data=form)
    assert registration.status_code == 302, registration.get_data(as_text=True)
    assert "/dashboard" in registration.headers["Location"]

    with app.app_context():
        owner = db.session.scalar(select(User).where(User.email == form["email"]))
        assert owner and owner.is_active and owner.category == "OWNER"
        bar = db.session.scalar(select(Bar).where(Bar.owner_id == owner.id))
        assert bar and bar.status == "ACTIVE"
        trial = db.session.scalar(
            select(Subscription).where(Subscription.bar_id == bar.id)
        )
        assert trial and trial.plan_code_snapshot == "BARPRO_TRIAL_15D"
        assert trial.status == "ACTIVE"
        assert trial.price_amount_snapshot == Decimal("0")
        assert (trial.ends_at - trial.starts_at).total_seconds() == 15 * 86400
        assert permissions.evaluate(owner, "orders.create", bar.id).allowed
        bar_id = bar.id
        trial_id = trial.id

    assert client.get(f"/bars/{bar_id}/subscription").status_code == 200
    assert client.get("/platform/owner-registrations").status_code == 403

    with app.app_context():
        trial = db.session.get(Subscription, trial_id)
        trial.starts_at = utcnow() - timedelta(days=16)
        trial.ends_at = utcnow() - timedelta(days=1)
        db.session.commit()
        assert trial_expired(bar_id)
        assert not permissions.evaluate(owner, "orders.create", bar_id).allowed
        assert permissions.evaluate(owner, "reports.read", bar_id).allowed

    assert client.post(f"/bars/{bar_id}/orders/new", data={}).status_code == 403
    assert client.get(f"/bars/{bar_id}/subscription").status_code == 200

    with app.app_context():
        plan = Plan(
            code="PAID-MYSQL-TEST", name="Abonnement de test",
            price_amount=Decimal("10000"), currency="XAF",
            duration_days=30, is_active=True,
        )
        db.session.add(plan)
        db.session.flush()
        now = utcnow()
        subscription = Subscription(
            bar_id=bar_id, plan_id=plan.id,
            reference="PAYMENT-PENDING-MYSQL-TEST", status="PENDING",
            plan_code_snapshot=plan.code, plan_name_snapshot=plan.name,
            price_amount_snapshot=plan.price_amount,
            duration_days_snapshot=30, currency="XAF",
            starts_at=now, ends_at=now + timedelta(days=30),
            created_by_id=owner.id,
        )
        db.session.add(subscription)
        db.session.commit()
        # Only a paid and confirmed subscription can restore writes.
        assert trial_expired(bar_id)
        admin = db.session.scalar(select(User).where(User.category == "SUPER_ADMIN"))
        subscription_service.pay(
            admin, bar_id, subscription.id,
            "RECEIPT-PAID-MYSQL-TEST", Decimal("10000"),
        )
        db.session.commit()
        assert not trial_expired(bar_id)
        assert permissions.evaluate(owner, "orders.create", bar_id).allowed
        assert db.session.get(Subscription, subscription.id).status == "ACTIVE"
    # Data intentionally retained in the dedicated, non-production test DB.
