"""Authentication landing contracts: valid roles always have a usable home.

Off-duty and suspended employees must not acquire operational permissions;
cross-tenant URLs continue to return 404.
"""
import re

import pytest

from app.cash_services import cash_service
from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.permissions import permissions
from app.shift_service import end_shift
from test_employee_tenant_auth import _employee, _login
from test_workflows import env


@pytest.mark.parametrize(
    "role, expected_path",
    [
        ("CASHIER", "/checkout"),
        ("SERVER", "/orders/new"),
        ("BAR_ADMIN", "/dashboard?bar_id="),
    ],
)
def test_employee_login_root_and_reentry_reach_their_real_home(env, role, expected_path):
    app, owner, bar, foreign, *_ = env
    employee, assignment = _employee(bar, role, f"landing-{role.lower()}")
    if role == "CASHIER":
        # The shift is OPEN; the cash drawer is still CLOSED. The entry must
        # lead to the session-opening screen, never a forbidden workspace.
        assert permissions.evaluate(employee, "payments.read", bar.id).allowed

    client = app.test_client()
    response = _login(client, employee.email)
    assert response.status_code == 302
    assert expected_path in response.headers["Location"]
    landing = client.get(response.headers["Location"], follow_redirects=True)
    assert landing.status_code == 200
    assert "Accès interdit" not in landing.text
    assert "Requête refusée" not in landing.text

    root = client.get("/", follow_redirects=True)
    assert root.status_code == 200
    assert root.text.startswith("<!doctype html>")
    assert "Accès interdit" not in root.text
    relogin = client.get("/login", follow_redirects=True)
    assert relogin.status_code == 200
    assert "Accès interdit" not in relogin.text

    # Session hints do not set authorization: only the active assignment does.
    with client.session_transaction() as sess:
        sess["current_bar_id"] = foreign.id
        sess["current_role"] = "OWNER"
    assert client.get(f"/bars/{foreign.id}/orders/new").status_code == 404
    with client.session_transaction() as sess:
        assert sess["current_bar_id"] == bar.id
        assert sess["current_role"] == role
        assert sess["current_assignment_id"] == assignment.id


@pytest.mark.parametrize("role", ["CASHIER", "SERVER"])
def test_employee_off_duty_lands_on_guidance_not_denied_page(env, role):
    app, owner, bar, foreign, *_ = env
    user = User(
        email=f"landing-off-duty-{role.lower()}@example.invalid",
        display_name="Compte valide hors service",
        category="EMPLOYEE",
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    db.session.add(StaffAssignment(
        bar_id=bar.id, user_id=user.id, role=role, started_at=utcnow()
    ))
    db.session.commit()
    action = "payments.read" if role == "CASHIER" else "orders.create"
    assert permissions.evaluate(user, action, bar.id).reason == "OFF_DUTY"

    client = app.test_client()
    login = _login(client, user.email)
    assert login.status_code == 302
    assert login.headers["Location"].endswith("/dashboard")
    for url in ("/", "/login", "/dashboard"):
        page = client.get(url, follow_redirects=True)
        assert page.status_code == 200
        assert "Vous n&#39;êtes pas en service" in page.text or "Vous n'êtes pas en service" in page.text
        assert "Accès interdit" not in page.text

    own = (f"/bars/{bar.id}/checkout" if role == "CASHIER"
           else f"/bars/{bar.id}/orders/new")
    assert client.get(own).status_code == 302
    assert client.get(own, follow_redirects=True).status_code == 200
    assert client.get(f"/bars/{foreign.id}/orders/new").status_code == 404
    assert permissions.evaluate(user, action, bar.id).reason == "OFF_DUTY"


@pytest.mark.parametrize("role", ["CASHIER", "SERVER", "BAR_ADMIN"])
def test_suspended_bar_employees_get_informational_home_only(env, role):
    app, owner, bar, foreign, *_ = env
    user, assignment = _employee(bar, role, f"suspended-{role.lower()}")
    bar.status = "SUSPENDED"
    db.session.commit()
    client = app.test_client()
    login = _login(client, user.email)
    assert login.status_code == 302
    assert login.headers["Location"].endswith("/dashboard")
    for url in ("/", "/login", "/dashboard", f"/dashboard?bar_id={bar.id}"):
        landing = client.get(url, follow_redirects=True)
        assert landing.status_code == 200
        assert "Établissement temporairement indisponible" in landing.text
        assert "Se déconnecter" in landing.text
        assert "Accès interdit" not in landing.text
    assert client.get(f"/bars/{bar.id}/orders/new").status_code == 302
    assert client.get(f"/bars/{bar.id}/orders/new", follow_redirects=True).status_code == 200
    assert client.get(f"/bars/{foreign.id}/orders/new").status_code == 404
    # Mutating a suspended venue is never permitted by the login fix.
    assert client.post(f"/bars/{bar.id}/orders/new").status_code in (400, 404)
    assert not permissions.evaluate(user, "orders.create", bar.id).allowed


def test_shift_closed_after_login_root_falls_back_to_off_duty_home(env):
    app, owner, bar, _, _, server, *_ = env
    from legacy_shift import activate_employee
    activate_employee(bar, server)
    client = app.test_client()
    assert _login(client, server.email).status_code == 302
    assert client.get("/", follow_redirects=True).status_code == 200
    assignment = db.session.query(StaffAssignment).filter_by(
        user_id=server.id, bar_id=bar.id, ended_at=None
    ).one()
    end_shift(owner, bar.id, assignment.id)
    db.session.commit()
    response = client.get("/", follow_redirects=True)
    assert response.status_code == 200
    assert "Vous n&#39;êtes pas en service" in response.text or "Vous n'êtes pas en service" in response.text
    assert permissions.evaluate(server, "orders.create", bar.id).reason == "OFF_DUTY"


@pytest.mark.parametrize("category", ["OWNER", "SUPER_ADMIN"])
def test_management_accounts_have_working_home_without_employee_role(env, category):
    app, owner, bar, foreign, *_ = env
    if category == "SUPER_ADMIN":
        user = User(
            email="landing-super-admin@example.invalid",
            display_name="Super administrateur",
            category="SUPER_ADMIN",
        )
        user.set_password("test-password")
        db.session.add(user)
        db.session.commit()
    else:
        user = owner
    client = app.test_client()
    logged = _login(client, user.email)
    assert logged.status_code == 302
    assert logged.headers["Location"].endswith("/dashboard")
    for path in ("/dashboard", "/", "/login"):
        response = client.get(path, follow_redirects=True)
        assert response.status_code == 200
        assert "Bonjour" in response.text
        assert "Accès interdit" not in response.text
