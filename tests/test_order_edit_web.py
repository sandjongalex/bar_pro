import re

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import OrderLine, Product, StaffAssignment, User, utcnow
from app.order_line_views import effective_lines_by_order
from app.order_services import order_service
from app.stock_service import stock_service
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


def _cashier(bar):
    user = User(email="web-edit-cashier@example.invalid", display_name="Cashier Web Edit", category="EMPLOYEE")
    user.set_password("test-password")
    db.session.add(user)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=user.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.flush()
    cash_service.open(user, bar.id, "WEB-EDIT-CASH", 0)
    db.session.commit()
    return user


def _second_product(env):
    _, owner, bar, _, product, _, _, _ = env
    second = Product(
        bar_id=bar.id,
        category_id=product.category_id,
        name="Juice Web Edit",
        sku="JUICE-WEB-EDIT",
        base_unit="bottle",
        sale_price=250,
        valuation_unit_cost=80,
    )
    db.session.add(second)
    db.session.flush()
    stock_service.move(owner, bar.id, second.id, "INITIAL", 5, "Web edit stock")
    db.session.commit()
    return second


def test_server_web_editor_can_replace_own_waiting_order(env):
    app, _, bar, _, product, server, _, _ = env
    second = _second_product(env)
    order = order_service.create(
        server,
        bar.id,
        "SERVER-WEB-EDIT",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    assert "order_edit_ui.js" in page.text
    assert "order_edit.css" in page.text

    state = client.get(f"/bars/{bar.id}/order-edits/{order.id}")
    assert state.status_code == 200
    payload = state.get_json()
    assert payload["success"] is True
    assert payload["order"]["editable"] is True
    assert payload["order"]["delivered"] is False
    assert payload["order"]["lines"][0]["quantity"] == "2"
    assert len(payload["order"]["revision"]) == 64
    assert any(item["id"] == second.id for item in payload["products"])

    response = client.post(
        f"/bars/{bar.id}/order-edits/{order.id}",
        data={
            "csrf_token": _csrf(page),
            "order_revision": payload["order"]["revision"],
            "product_id": str(second.id),
            "quantity": "1",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("#mes-commandes")

    lines = list(db.session.scalars(select(OrderLine).where(OrderLine.order_id == order.id)))
    assert len(lines) == 1
    assert lines[0].product_id == second.id
    assert lines[0].quantity == 1


def test_server_web_editor_locks_after_delivery(env):
    app, owner, bar, _, product, server, _, _ = env
    order = order_service.create(
        server,
        bar.id,
        "SERVER-WEB-LOCKED",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, order.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, server.email).status_code == 302
    state = client.get(f"/bars/{bar.id}/order-edits/{order.id}")
    assert state.status_code == 200
    payload = state.get_json()
    assert payload["order"]["delivered"] is True
    assert payload["order"]["editable"] is False


def test_cashier_web_editor_updates_delivered_unpaid_order(env):
    app, _, bar, _, product, server, _, _ = env
    cashier = _cashier(bar)
    second = _second_product(env)
    order = order_service.create(
        server,
        bar.id,
        "CASHIER-WEB-EDIT",
        [{"product_id": product.id, "quantity": 2}],
    )
    order_service.confirm(cashier, bar.id, order.id)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    workspace = client.get(f"/bars/{bar.id}/cashier/workspace?order_id={order.id}")
    assert workspace.status_code == 200
    assert "order_edit_ui.js" in workspace.text

    state = client.get(f"/bars/{bar.id}/order-edits/{order.id}")
    assert state.status_code == 200
    payload = state.get_json()
    assert payload["order"]["editable"] is True
    assert payload["order"]["delivered"] is True

    response = client.post(
        f"/bars/{bar.id}/order-edits/{order.id}",
        data={
            "csrf_token": _csrf(workspace),
            "order_revision": payload["order"]["revision"],
            "product_id": str(second.id),
            "quantity": "1",
            "reason": "Client change sa commande",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "cashier/workspace" in response.headers["Location"]
    assert response.headers["Location"].endswith("#paymentPanel")

    effective = effective_lines_by_order(bar.id, [order.id])[order.id]
    assert len(effective) == 1
    assert effective[0]["product_id"] == second.id
    assert effective[0]["quantity"] == 1


def test_stale_second_editor_cannot_overwrite_first_edit(env):
    app, _, bar, _, product, server, _, _ = env
    second = _second_product(env)
    order = order_service.create(
        server,
        bar.id,
        "SERVER-WEB-CONFLICT",
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.commit()

    first = app.test_client()
    second_client = app.test_client()
    assert _login(first, server.email).status_code == 302
    assert _login(second_client, server.email).status_code == 302

    first_page = first.get(f"/bars/{bar.id}/orders/new")
    second_page = second_client.get(f"/bars/{bar.id}/orders/new")
    first_state = first.get(f"/bars/{bar.id}/order-edits/{order.id}").get_json()
    second_state = second_client.get(f"/bars/{bar.id}/order-edits/{order.id}").get_json()
    assert first_state["order"]["revision"] == second_state["order"]["revision"]

    first_response = first.post(
        f"/bars/{bar.id}/order-edits/{order.id}",
        data={
            "csrf_token": _csrf(first_page),
            "order_revision": first_state["order"]["revision"],
            "product_id": str(product.id),
            "quantity": "3",
        },
        follow_redirects=False,
    )
    assert first_response.status_code == 302

    stale_response = second_client.post(
        f"/bars/{bar.id}/order-edits/{order.id}",
        data={
            "csrf_token": _csrf(second_page),
            "order_revision": second_state["order"]["revision"],
            "product_id": str(second.id),
            "quantity": "1",
        },
        follow_redirects=True,
    )
    assert stale_response.status_code == 200
    assert "Cette commande a changé sur un autre appareil" in stale_response.text

    lines = list(db.session.scalars(select(OrderLine).where(OrderLine.order_id == order.id)))
    assert len(lines) == 1
    assert lines[0].product_id == product.id
    assert lines[0].quantity == 3
