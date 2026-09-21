import re

from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    assert page.status_code == 200
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def _employee(bar, role, suffix):
    user = User(
        email=f"tenant-{suffix}@example.invalid",
        display_name=f"Tenant {role}",
        category="EMPLOYEE",
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id,
        user_id=user.id,
        role=role,
        started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.commit()
    return user, assignment


def test_cashier_login_redirects_directly_to_cashier_dashboard(env):
    app, _, bar, _, _, _, _, _ = env
    cashier, assignment = _employee(bar, "CASHIER", "cashier")
    client = app.test_client()

    response = _login(client, cashier.email)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/bars/{bar.id}/checkout")
    with client.session_transaction() as session:
        assert session["current_bar_id"] == bar.id
        assert session["current_role"] == "CASHIER"
        assert session["current_assignment_id"] == assignment.id


def test_server_login_redirects_directly_to_server_dashboard(env):
    app, _, bar, _, _, server, _, _ = env
    assignment = db.session.query(StaffAssignment).filter_by(user_id=server.id, ended_at=None).one()
    client = app.test_client()

    response = _login(client, server.email)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/bars/{bar.id}/orders/new")
    with client.session_transaction() as session:
        assert session["current_bar_id"] == bar.id
        assert session["current_role"] == "SERVER"
        assert session["current_assignment_id"] == assignment.id


def test_bar_admin_login_redirects_directly_to_bar_dashboard(env):
    app, _, bar, _, _, _, _, _ = env
    admin, assignment = _employee(bar, "BAR_ADMIN", "admin")
    client = app.test_client()

    response = _login(client, admin.email)

    assert response.status_code == 302
    assert response.headers["Location"].endswith(f"/dashboard?bar_id={bar.id}")
    with client.session_transaction() as session:
        assert session["current_bar_id"] == bar.id
        assert session["current_role"] == "BAR_ADMIN"
        assert session["current_assignment_id"] == assignment.id


def test_employee_login_requires_exactly_one_active_assignment(env):
    app, _, _, _, _, _, _, _ = env
    user = User(
        email="tenant-unassigned@example.invalid",
        display_name="Unassigned Employee",
        category="EMPLOYEE",
    )
    user.set_password("test-password")
    db.session.add(user)
    db.session.commit()
    client = app.test_client()

    response = _login(client, user.email)

    assert response.status_code == 403
    assert "Aucune affectation active" in response.text
    with client.session_transaction() as session:
        assert "current_bar_id" not in session
        assert "current_role" not in session
