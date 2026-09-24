import re

from sqlalchemy import select

from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from app.payment_services import payment_service
from app.shift_service import start_shift
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def _employee(bar, role, email, display_name=None):
    user = User(
        email=email,
        display_name=display_name or f"{role} Monitor",
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


def _active_assignment(bar, user, role):
    return db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar.id,
            StaffAssignment.user_id == user.id,
            StaffAssignment.role == role,
            StaffAssignment.ended_at.is_(None),
        )
    )


def test_server_unpaid_monitor_only_shows_own_delivered_orders(env):
    app, owner, bar, _, product, server, _, _ = env
    other_server, other_assignment = _employee(
        bar, "SERVER", "other-monitor-server@example.invalid"
    )
    manager, manager_assignment = _employee(
        bar, "CASHIER", "monitor-manager@example.invalid", "Manager"
    )
    server_assignment = _active_assignment(bar, server, "SERVER")
    assert server_assignment is not None

    start_shift(owner, bar.id, manager_assignment.id)
    start_shift(manager, bar.id, server_assignment.id)
    start_shift(manager, bar.id, other_assignment.id)
    db.session.commit()

    own = order_service.create(server, bar.id, "UNPAID-OWN", [{"product_id": product.id, "quantity": 1}])
    other = order_service.create(other_server, bar.id, "UNPAID-OTHER", [{"product_id": product.id, "quantity": 1}])
    order_service.confirm(owner, bar.id, own.id)
    order_service.confirm(owner, bar.id, other.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/unpaid-orders")
    assert page.status_code == 200
    assert "Mes commandes livrées non payées" in page.text
    assert "UNPAID-OWN" in page.text
    assert "UNPAID-OTHER" not in page.text
    assert "unpaid_orders_ui.js" in page.text
    assert "Impayées" in page.text
    assert 'id="staff"' not in page.text

    payload = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    assert payload["mode"] == "SERVER"
    assert payload["stats"]["count"] == 1
    assert [item["reference"] for item in payload["orders"]] == ["UNPAID-OWN"]
    assert payload["orders"][0]["action_url"] is None


def test_cashier_unpaid_monitor_shows_all_delivered_orders_and_payment_removes_paid(env):
    app, owner, bar, _, product, server, _, _ = env
    cashier, cashier_assignment = _employee(
        bar, "CASHIER", "monitor-cashier@example.invalid", "Alex"
    )
    server_assignment = _active_assignment(bar, server, "SERVER")
    assert server_assignment is not None

    start_shift(owner, bar.id, cashier_assignment.id)
    start_shift(cashier, bar.id, server_assignment.id)
    db.session.commit()

    first = order_service.create(server, bar.id, "UNPAID-CASH-1", [{"product_id": product.id, "quantity": 1}])
    second = order_service.create(server, bar.id, "UNPAID-CASH-2", [{"product_id": product.id, "quantity": 2}])
    order_service.confirm(owner, bar.id, first.id)
    order_service.confirm(owner, bar.id, second.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/unpaid-orders")
    assert page.status_code == 200
    assert "Commandes livrées non payées" in page.text
    assert "UNPAID-CASH-1" in page.text
    assert "UNPAID-CASH-2" in page.text
    assert 'id="staff"' in page.text
    assert "Personnel" in page.text

    payload = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    assert payload["mode"] == "CASHIER"
    assert payload["stats"]["count"] == 2
    assert all("cashier/workspace" in item["action_url"] for item in payload["orders"])

    payment_service.record(owner, bar.id, first.id, "MONITOR-PAID", "CARD", 100, 100)
    db.session.commit()

    refreshed = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    refs = {item["reference"] for item in refreshed["orders"]}
    assert "UNPAID-CASH-1" not in refs
    assert "UNPAID-CASH-2" in refs
    assert refreshed["stats"]["count"] == 1


def test_cashier_can_filter_unpaid_orders_by_cashier_or_server(env):
    app, owner, bar, _, product, server, _, _ = env
    alex, alex_assignment = _employee(
        bar, "CASHIER", "alex-filter@example.invalid", "Alex"
    )
    ashley, ashley_assignment = _employee(
        bar, "CASHIER", "ashley-filter@example.invalid", "Ashley"
    )
    server.display_name = "Marie"
    server_assignment = _active_assignment(bar, server, "SERVER")
    assert server_assignment is not None

    start_shift(owner, bar.id, alex_assignment.id)
    start_shift(owner, bar.id, ashley_assignment.id)
    start_shift(alex, bar.id, server_assignment.id)
    db.session.commit()

    marie_order = order_service.create(
        server,
        bar.id,
        "FILTER-MARIE",
        [{"product_id": product.id, "quantity": 1}],
    )
    alex_order = order_service.create(
        alex,
        bar.id,
        "FILTER-ALEX",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Comptoir Alex",
    )
    ashley_order = order_service.create(
        ashley,
        bar.id,
        "FILTER-ASHLEY",
        [{"product_id": product.id, "quantity": 1}],
        invoice_name="Comptoir Ashley",
    )
    for item in (marie_order, alex_order, ashley_order):
        order_service.confirm(owner, bar.id, item.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, alex.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/unpaid-orders")
    assert page.status_code == 200
    assert "Tout le personnel" in page.text
    assert "Caissiers / Caissières" in page.text
    assert "Serveurs / Serveuses" in page.text
    assert "Alex" in page.text
    assert "Ashley" in page.text
    assert "Marie" in page.text

    alex_only = client.get(
        f"/bars/{bar.id}/unpaid-orders?staff=person-{alex.id}"
    )
    assert alex_only.status_code == 200
    assert "FILTER-ALEX" in alex_only.text
    assert "FILTER-ASHLEY" not in alex_only.text
    assert "FILTER-MARIE" not in alex_only.text
    assert "Alex · Comptoir" in alex_only.text

    marie_only = client.get(
        f"/bars/{bar.id}/unpaid-orders?staff=person-{server.id}"
    )
    assert marie_only.status_code == 200
    assert "FILTER-MARIE" in marie_only.text
    assert "FILTER-ALEX" not in marie_only.text
    assert "FILTER-ASHLEY" not in marie_only.text

    ashley_payload = client.get(
        f"/bars/{bar.id}/unpaid-orders/data?staff=person-{ashley.id}"
    ).get_json()
    assert ashley_payload["staff_filter"] == f"person-{ashley.id}"
    assert ashley_payload["stats"]["count"] == 1
    assert [item["reference"] for item in ashley_payload["orders"]] == ["FILTER-ASHLEY"]
