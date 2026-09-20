import re

from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from app.payment_services import payment_service
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


def _employee(bar, role, email):
    user = User(email=email, display_name=f"{role} Monitor", category="EMPLOYEE")
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


def test_server_unpaid_monitor_only_shows_own_delivered_orders(env):
    app, owner, bar, _, product, server, _, _ = env
    other_server, _ = _employee(bar, "SERVER", "other-monitor-server@example.invalid")

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

    payload = client.get(f"/bars/{bar.id}/unpaid-orders/data").get_json()
    assert payload["mode"] == "SERVER"
    assert payload["stats"]["count"] == 1
    assert [item["reference"] for item in payload["orders"]] == ["UNPAID-OWN"]
    assert payload["orders"][0]["action_url"] is None


def test_cashier_unpaid_monitor_shows_all_delivered_orders_and_payment_removes_paid(env):
    app, owner, bar, _, product, server, _, _ = env
    cashier, _ = _employee(bar, "CASHIER", "monitor-cashier@example.invalid")

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
