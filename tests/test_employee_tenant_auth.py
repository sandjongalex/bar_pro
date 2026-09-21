import re

from app.extensions import db
from app.models import Bar, Order, StaffAssignment, User, utcnow
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


def test_employee_session_is_bound_to_assigned_bar_and_other_bar_is_404(env):
    app, _, bar, foreign, _, server, _, _ = env
    assignment = db.session.query(StaffAssignment).filter_by(user_id=server.id, ended_at=None).one()
    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    # Simulate stale/tampered browser tenant data. The server must repair it from StaffAssignment.
    with client.session_transaction() as session:
        session["current_bar_id"] = foreign.id
        session["current_role"] = "CASHIER"
        session["current_assignment_id"] = 999999

    own_page = client.get(f"/bars/{bar.id}/orders/new")
    assert own_page.status_code == 200
    with client.session_transaction() as session:
        assert session["current_bar_id"] == bar.id
        assert session["current_role"] == "SERVER"
        assert session["current_assignment_id"] == assignment.id

    foreign_page = client.get(f"/bars/{foreign.id}/orders/new")
    assert foreign_page.status_code == 404


def test_bar_admin_cannot_open_bar_list_or_see_bar_list_navigation(env):
    app, _, bar, _, _, _, _, _ = env
    admin, _ = _employee(bar, "BAR_ADMIN", "bar-list")
    client = app.test_client()
    assert _login(client, admin.email).status_code == 302

    dashboard = client.get(f"/dashboard?bar_id={bar.id}")
    assert dashboard.status_code == 200
    assert '<span class="nav-link-text">Établissements</span>' not in dashboard.text

    listing = client.get("/bars/")
    assert listing.status_code == 404


def test_bar_admin_dashboard_is_bound_to_assigned_bar(env):
    app, _, bar, foreign, _, _, _, _ = env
    admin, _ = _employee(bar, "BAR_ADMIN", "dashboard-bound")
    client = app.test_client()
    assert _login(client, admin.email).status_code == 302

    dashboard = client.get(f"/dashboard?bar_id={bar.id}")
    assert dashboard.status_code == 200
    assert bar.name in dashboard.text
    assert foreign.name not in dashboard.text
    assert 'class="dashboard-bar-selector"' not in dashboard.text

    foreign_dashboard = client.get(f"/dashboard?bar_id={foreign.id}")
    assert foreign_dashboard.status_code == 404


def test_employee_api_login_resolves_bar_automatically(env):
    app, _, bar, _, _, server, _, _ = env
    client = app.test_client()

    response = client.post(
        "/api/v1/auth/tokens",
        json={"email": server.email, "password": "test-password"},
    )

    assert response.status_code == 200
    access_token = response.get_json()["data"]["access_token"]
    bars = client.get(
        "/api/v1/bars",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert bars.status_code == 200
    payload = bars.get_json()
    assert len(payload["data"]) == 1
    assert payload["data"][0]["id"] == str(bar.id)


def test_employee_cannot_reach_second_bar_of_same_owner(env):
    app, owner, bar, _, _, server, _, _ = env
    second_bar = Bar(
        owner_id=owner.id,
        name="Same Owner Hidden",
        timezone="Africa/Douala",
        currency="XAF",
    )
    db.session.add(second_bar)
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    # Same owner must not weaken employee tenant isolation.
    assert client.get(f"/bars/{second_bar.id}/orders/new").status_code == 404

    forced_token = client.post(
        "/api/v1/auth/tokens",
        json={
            "email": server.email,
            "password": "test-password",
            "bar_id": second_bar.id,
        },
    )
    assert forced_token.status_code == 404

    own_token = client.post(
        "/api/v1/auth/tokens",
        json={"email": server.email, "password": "test-password"},
    )
    assert own_token.status_code == 200
    access_token = own_token.get_json()["data"]["access_token"]
    bars = client.get(
        "/api/v1/bars",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert bars.status_code == 200
    payload = bars.get_json()
    assert [item["id"] for item in payload["data"]] == [str(bar.id)]


def test_cashier_cannot_read_other_bar_order(env):
    app, owner, bar, _, _, _, _, _ = env
    cashier, _ = _employee(bar, "CASHIER", "foreign-order-read")
    second_bar = Bar(
        owner_id=owner.id,
        name="Hidden Orders Bar",
        timezone="Africa/Douala",
        currency="XAF",
    )
    db.session.add(second_bar)
    db.session.flush()
    foreign_order = Order(
        bar_id=second_bar.id,
        reference="HIDDEN-ORDER",
        status="DRAFT",
        payment_status="UNPAID",
        currency="XAF",
        subtotal_amount=0,
        discount_amount=0,
        tax_amount=0,
        total_amount=0,
        created_by_id=owner.id,
    )
    db.session.add(foreign_order)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    response = client.get(f"/bars/{bar.id}/order-edits/{foreign_order.id}")
    assert response.status_code == 404
    assert "HIDDEN-ORDER" not in response.text


def test_cashier_cannot_modify_other_bar_order(env):
    app, owner, bar, _, _, _, _, _ = env
    cashier, _ = _employee(bar, "CASHIER", "foreign-order-write")
    second_bar = Bar(
        owner_id=owner.id,
        name="Hidden Order Mutation Bar",
        timezone="Africa/Douala",
        currency="XAF",
    )
    db.session.add(second_bar)
    db.session.flush()
    foreign_order = Order(
        bar_id=second_bar.id,
        reference="HIDDEN-MUTATION",
        status="DRAFT",
        payment_status="UNPAID",
        currency="XAF",
        subtotal_amount=0,
        discount_amount=0,
        tax_amount=0,
        total_amount=0,
        created_by_id=owner.id,
    )
    db.session.add(foreign_order)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    csrf_page = client.get(f"/bars/{bar.id}/cashier-session")
    assert csrf_page.status_code == 200

    response = client.post(
        f"/bars/{bar.id}/order-edits/{foreign_order.id}",
        data={
            "csrf_token": _csrf(csrf_page),
            "order_revision": "foreign-order-must-not-resolve",
            "reason": "cross tenant attempt",
        },
        follow_redirects=False,
    )
    assert response.status_code == 404

    db.session.expire_all()
    unchanged = db.session.get(Order, foreign_order.id)
    assert unchanged.bar_id == second_bar.id
    assert unchanged.reference == "HIDDEN-MUTATION"
    assert unchanged.status == "DRAFT"
    assert unchanged.payment_status == "UNPAID"


def test_server_cannot_read_other_bar_order(env):
    app, owner, bar, _, _, server, _, _ = env
    second_bar = Bar(
        owner_id=owner.id,
        name="Hidden Server Order Bar",
        timezone="Africa/Douala",
        currency="XAF",
    )
    db.session.add(second_bar)
    db.session.flush()
    foreign_order = Order(
        bar_id=second_bar.id,
        reference="SERVER-HIDDEN-ORDER",
        status="DRAFT",
        payment_status="UNPAID",
        currency="XAF",
        subtotal_amount=0,
        discount_amount=0,
        tax_amount=0,
        total_amount=0,
        created_by_id=owner.id,
    )
    db.session.add(foreign_order)
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    response = client.get(f"/bars/{bar.id}/order-edits/{foreign_order.id}")
    assert response.status_code == 404
    assert "SERVER-HIDDEN-ORDER" not in response.text
