"""Server POS AJAX/idempotency integration tests: no JWT shortcuts."""
import re
from pathlib import Path

from flask import g

from sqlalchemy import select, func

from app.extensions import db
from app.models import Order, StaffAssignment, User, utcnow
from app.shift_service import start_shift
from test_workflows import env


def _login(client, user):
    page = client.get("/login")
    csrf = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert csrf is not None
    response = client.post("/login", data={
        "email": user.email, "password": "test-password", "csrf_token": csrf.group(1),
    })
    assert response.status_code == 302


def _session(env):
    app, owner, bar, foreign, product, server, _, _ = env
    cashier = User(email="async-cashier@example.invalid", display_name="Cashier", category="EMPLOYEE")
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    cash_assignment = StaffAssignment(
        bar_id=bar.id, user_id=cashier.id, role="CASHIER", started_at=utcnow()
    )
    db.session.add(cash_assignment)
    db.session.flush()
    waitress_assignment = db.session.scalar(select(StaffAssignment).where(
        StaffAssignment.bar_id == bar.id, StaffAssignment.user_id == server.id
    ))
    start_shift(owner, bar.id, cash_assignment.id)
    start_shift(cashier, bar.id, waitress_assignment.id)
    db.session.commit()

    client = app.test_client()
    _login(client, server)
    page = client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    key = re.search(r'name="idempotency_key" value="([^"]+)"', page.text)
    assert csrf and key
    return client, cashier, csrf.group(1), key.group(1)


def _headers(csrf):
    return {
        "X-CSRFToken": csrf, "X-BarPro-Async": "1",
        "X-Requested-With": "XMLHttpRequest", "Accept": "application/json",
    }


def _order_payload(product, key, quantity="2", name="Client-test"):
    return {
        "action": "create", "idempotency_key": key,
        "product_id": str(product.id), "quantity": quantity,
        "invoice_name": name,
    }


def test_json_creation_replay_and_lookup_share_single_order(env):
    app, owner, bar, foreign, product, server, _, _ = env
    client, cashier, csrf, key = _session(env)
    url = f"/bars/{bar.id}/orders/new"
    first = client.post(url, data=_order_payload(product, key), headers=_headers(csrf))
    assert first.status_code == 201, first.text
    oid = first.json["data"]["id"]
    assert first.json["data"]["reference"].startswith("CMD-")
    assert first.json["data"]["invoice_name"] == "Client-test"
    assert first.json["data"]["replayed"] is False

    again = client.post(url, data=_order_payload(product, key), headers=_headers(csrf))
    assert again.status_code == 200, again.text
    assert again.json["data"]["id"] == oid
    assert again.json["data"]["replayed"] is True
    assert db.session.scalar(select(func.count()).select_from(Order).where(
        Order.bar_id == bar.id, Order.created_by_id == server.id
    )) == 1

    recover = client.get(f"/bars/{bar.id}/orders/async-intents/{key}")
    assert recover.status_code == 200
    assert recover.json["data"]["id"] == oid

    live = client.get(f"/bars/{bar.id}/live/orders")
    assert live.status_code == 200
    assert live.json["orders"][0]["id"] == oid
    assert live.json["orders"][0]["display_name"] == "Client-test"

    html = client.post(url, data={"action": "note", "order_id": oid,
                                  "note": "Exige du frais", "csrf_token": csrf})
    assert html.status_code == 302  # Old HTML fallback is untouched.


def test_conflict_validation_missing_csrf_and_cross_bar_recovery(env):
    app, owner, bar, foreign, product, server, _, _ = env
    client, cashier, csrf, key = _session(env)
    url = f"/bars/{bar.id}/orders/new"
    invalid = client.post(url, data=_order_payload(product, key, quantity="-5"), headers=_headers(csrf))
    assert invalid.status_code == 422
    assert db.session.scalar(select(func.count()).select_from(Order).where(
        Order.bar_id == bar.id
    )) == 0

    first = client.post(url, data=_order_payload(product, key), headers=_headers(csrf))
    assert first.status_code == 201, first.text
    different = client.post(url, data=_order_payload(product, key, quantity="3"), headers=_headers(csrf))
    assert different.status_code == 409
    assert different.json["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    no_csrf = client.post(url, data=_order_payload(product, "abcde12345abcde12345"), headers={
        "Accept": "application/json", "X-BarPro-Async": "1",
        "X-Requested-With": "XMLHttpRequest",
    })
    assert no_csrf.status_code == 400
    assert client.get(f"/bars/{foreign.id}/orders/async-intents/{key}").status_code in (403, 404)


def test_other_waitress_cannot_read_update_or_note_original_order(env):
    app, owner, bar, foreign, product, server, _, _ = env
    client, cashier, csrf, key = _session(env)
    first = client.post(f"/bars/{bar.id}/orders/new",
                        data=_order_payload(product, key), headers=_headers(csrf))
    assert first.status_code == 201, first.text
    oid = first.json["data"]["id"]

    other = User(email="async-other-server@example.invalid", display_name="Other",
                 category="EMPLOYEE")
    other.set_password("test-password")
    db.session.add(other)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id, user_id=other.id, role="SERVER", started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    start_shift(cashier, bar.id, assignment.id)
    db.session.commit()

    another_client = app.test_client()
    # Isolate authorization checks from the login endpoint's IP rate limiter.
    with another_client.session_transaction() as session:
        session["_user_id"] = str(other.id)
        session["_fresh"] = True
        session["credentials_version"] = other.credentials_version
    # The legacy env fixture holds one Flask app_context throughout the test.
    # Flush flask-login's cached user from g when switching test_client identity.
    g.pop("_login_user", None)
    g.pop("csrf_token", None)
    page = another_client.get(f"/bars/{bar.id}/orders/new")
    other_csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert other_csrf is not None

    assert another_client.get(f"/bars/{bar.id}/orders/async-intents/{key}").status_code == 404
    assert another_client.get(f"/bars/{bar.id}/order-edits/{oid}").status_code == 403
    response = another_client.post(f"/bars/{bar.id}/orders/new",
        data={"action": "note", "order_id": oid, "note": "Other's note"},
        headers=_headers(other_csrf.group(1)))
    assert response.status_code == 403


def test_order_edit_ajax_and_html_fallback_and_template_contract(env):
    app, owner, bar, foreign, product, server, _, _ = env
    client, _, csrf, key = _session(env)
    first = client.post(f"/bars/{bar.id}/orders/new",
                        data=_order_payload(product, key), headers=_headers(csrf))
    assert first.status_code == 201
    oid = first.json["data"]["id"]
    editor = client.get(f"/bars/{bar.id}/order-edits/{oid}")
    assert editor.status_code == 200
    revision = editor.json["order"]["revision"]
    change = client.post(f"/bars/{bar.id}/order-edits/{oid}",
        data={"order_revision": revision, "product_id": product.id, "quantity": "3"},
        headers=_headers(csrf))
    assert change.status_code == 200, change.text
    assert change.json["data"]["id"] == oid
    assert change.json["success"] is True

    template = Path("app/templates/order_quick.html").read_text(encoding="utf-8")
    assert 'data-server-orders-grid' in template
    assert '{% if is_server %}' in template
    assert 'name="idempotency_key"' in template
    assert 'data-server-order-id="{{ order.id }}"' in template

    script = Path("app/static/server_pos_ui.js").read_text(encoding="utf-8")
    assert "makeLiveServerCard(order)" in script
    assert "list.insertBefore(card" in script
    assert "setupInlineOrderActions()" in script
    assert "window.BarProOrderEdit?.setupServerEditors?.()" in script


def test_two_waitresses_have_independent_intent_scope_and_live_lists(env):
    app, owner, bar, _, product, first_waitress, _, _ = env
    first_client, cashier, first_csrf, same_key = _session(env)
    first = first_client.post(
        f"/bars/{bar.id}/orders/new",
        data=_order_payload(product, same_key, name="Facture A"),
        headers=_headers(first_csrf),
    )
    assert first.status_code == 201

    second_waitress = User(
        email="async-third-server@example.invalid",
        display_name="Second waitress", category="EMPLOYEE",
    )
    second_waitress.set_password("test-password")
    db.session.add(second_waitress)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id, user_id=second_waitress.id,
        role="SERVER", started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    start_shift(cashier, bar.id, assignment.id)
    db.session.commit()

    second_client = app.test_client()
    with second_client.session_transaction() as session:
        session["_user_id"] = str(second_waitress.id)
        session["_fresh"] = True
        session["credentials_version"] = second_waitress.credentials_version
    g.pop("_login_user", None)
    g.pop("csrf_token", None)
    page = second_client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert csrf is not None

    # Reusing the exact key on another authenticated actor must not replay
    # the first waitress's order; uniqueness is (bar, actor, operation, key).
    second = second_client.post(
        f"/bars/{bar.id}/orders/new",
        data=_order_payload(product, same_key, name="Facture B"),
        headers=_headers(csrf.group(1)),
    )
    assert second.status_code == 201, second.text
    assert second.json["data"]["id"] != first.json["data"]["id"]
    own_live = second_client.get(f"/bars/{bar.id}/live/orders")
    assert [order["id"] for order in own_live.json["orders"]] == [second.json["data"]["id"]]
