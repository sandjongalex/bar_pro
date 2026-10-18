"""Server-only standalone order list: SSR, filters, pagination and safe AJAX."""
import re

from flask import g
from sqlalchemy import select

from app.extensions import db
from app.models import Order, StaffAssignment, User, utcnow
from app.order_services import order_service
from app.shift_service import start_shift
from test_server_async_orders import _session, _headers, _order_payload
from test_workflows import env


def _create(client, bar, product, csrf, name, key):
    response = client.post(
        f"/bars/{bar.id}/orders/new",
        data=_order_payload(product, key, name=name),
        headers=_headers(csrf),
    )
    assert response.status_code == 201, response.text
    return response.get_json()["data"]["id"]


def test_server_history_renders_orders_and_six_filters_without_javascript(env):
    app, _, bar, _, product, _, _, _ = env
    client, _, csrf, key = _session(env)
    order_id = _create(client, bar, product, csrf, "Terrasse Alpha", key)
    page = client.get(f"/bars/{bar.id}/orders/mine")
    assert page.status_code == 200
    assert "Terrasse Alpha" in page.text
    assert f'data-server-order-id="{order_id}"' in page.text
    for filter_name in ("active", "waiting", "to_pay", "paid", "cancelled", "all"):
        assert f'data-server-mine-filter="{filter_name}"' in page.text
    assert 'data-bp-ajax="json"' in page.text
    assert 'server_orders_page.js' in page.text
    assert 'Détails' in page.text
    assert 'Ajouter' in page.text
    assert 'Annuler la commande' in page.text

    waiting = client.get(f"/bars/{bar.id}/orders/mine?filter=waiting")
    assert waiting.status_code == 200
    assert "Terrasse Alpha" in waiting.text
    paid = client.get(f"/bars/{bar.id}/orders/mine?filter=paid")
    assert paid.status_code == 200
    assert "Terrasse Alpha" not in paid.text
    assert "Aucune commande dans ce filtre" in paid.text


def test_server_history_snapshot_etag_and_new_orders(env):
    app, _, bar, _, product, _, _, _ = env
    client, _, csrf, key = _session(env)
    url = f"/bars/{bar.id}/orders/mine?filter=active&snapshot=1"
    first = client.get(url)
    assert first.status_code == 200
    assert first.is_json
    assert first.get_json()["stats"]["active"] == 0
    assert first.headers.get("ETag")
    assert client.get(url, headers={"If-None-Match": first.headers["ETag"]}).status_code == 304

    order_id = _create(client, bar, product, csrf, "Nouvelle commande", key)
    updated = client.get(url, headers={"If-None-Match": first.headers["ETag"]})
    assert updated.status_code == 200
    assert updated.get_json()["stats"]["active"] == 1
    assert f'data-server-order-id="{order_id}"' in updated.get_json()["html"]
    assert updated.headers["ETag"] != first.headers["ETag"]
    assert 'id="posForm"' not in updated.get_json()["html"]


def test_server_history_pagination_and_filtered_states(env, monkeypatch):
    app, _, bar, _, product, server, _, _ = env
    client, cashier, csrf, key = _session(env)
    monkeypatch.setattr("app.orders.SERVER_ORDER_PAGE_SIZE", 2)
    ids = [_create(client, bar, product, csrf, f"Table {i}", f"test-mine-paginated-key-{i:03d}")
           for i in range(3)]
    first = client.get(f"/bars/{bar.id}/orders/mine?filter=all&page=1")
    assert first.status_code == 200
    assert f'data-server-order-id="{ids[2]}"' in first.text
    assert f'data-server-order-id="{ids[0]}"' not in first.text
    second = client.get(f"/bars/{bar.id}/orders/mine?filter=all&page=2")
    assert second.status_code == 200
    assert f'data-server-order-id="{ids[0]}"' in second.text
    assert 'Page 2 sur 2' in second.text

    order_service.confirm(cashier, bar.id, ids[0])
    db.session.commit()
    to_pay = client.get(f"/bars/{bar.id}/orders/mine?filter=to_pay")
    assert to_pay.status_code == 200
    assert f'data-server-order-id="{ids[0]}"' in to_pay.text
    waiting = client.get(f"/bars/{bar.id}/orders/mine?filter=waiting")
    assert f'data-server-order-id="{ids[0]}"' not in waiting.text


def test_server_history_note_cancel_async_and_html_fallback(env):
    app, _, bar, _, product, server, _, _ = env
    client, _, csrf, key = _session(env)
    order_id = _create(client, bar, product, csrf, "À modifier", key)
    url = f"/bars/{bar.id}/orders/new"
    note = client.post(url, data={
        "csrf_token": csrf, "action": "note", "order_id": order_id,
        "note": "Servir sans glace", "return_to": "mine",
    }, headers=_headers(csrf))
    assert note.status_code == 200
    assert note.get_json()["success"]
    assert "Servir sans glace" in client.get(f"/bars/{bar.id}/orders/mine").text

    cancelled = client.post(url, data={
        "csrf_token": csrf, "action": "cancel", "order_id": order_id,
        "reason": "Client parti", "return_to": "mine",
    }, follow_redirects=False)
    assert cancelled.status_code == 302
    assert cancelled.headers["Location"].endswith(f"/bars/{bar.id}/orders/mine")
    assert f'data-server-order-id="{order_id}"' not in client.get(
        f"/bars/{bar.id}/orders/mine?filter=active"
    ).text
    assert f'data-server-order-id="{order_id}"' in client.get(
        f"/bars/{bar.id}/orders/mine?filter=cancelled"
    ).text


def test_server_history_blocks_other_server_and_non_server_roles(env):
    app, owner, bar, foreign, product, server, _, _ = env
    client, cashier, csrf, key = _session(env)
    order_id = _create(client, bar, product, csrf, "Commande privée", key)

    second = User(email="mine-other-server@example.invalid",
                  display_name="Autre serveuse", category="EMPLOYEE")
    second.set_password("test-password")
    db.session.add(second)
    db.session.flush()
    assignment = StaffAssignment(
        bar_id=bar.id, user_id=second.id, role="SERVER", started_at=utcnow(),
    )
    db.session.add(assignment)
    db.session.flush()
    start_shift(cashier, bar.id, assignment.id)
    db.session.commit()

    other_client = app.test_client()
    with other_client.session_transaction() as session:
        session["_user_id"] = str(second.id)
        session["_fresh"] = True
        session["credentials_version"] = second.credentials_version
    g.pop("_login_user", None)
    g.pop("csrf_token", None)
    page = other_client.get(f"/bars/{bar.id}/orders/mine")
    assert page.status_code == 200
    assert "Commande privée" not in page.text
    assert f'data-server-order-id="{order_id}"' not in page.text
    snapshot = other_client.get(f"/bars/{bar.id}/orders/mine?snapshot=1")
    assert snapshot.status_code == 200
    assert f'data-server-order-id="{order_id}"' not in snapshot.get_json()["html"]
    assert other_client.get(f"/bars/{foreign.id}/orders/mine").status_code in (302, 403, 404)

    owner_client = app.test_client()
    with owner_client.session_transaction() as session:
        session["_user_id"] = str(owner.id)
        session["_fresh"] = True
        session["credentials_version"] = owner.credentials_version
    g.pop("_login_user", None)
    g.pop("csrf_token", None)
    assert owner_client.get(f"/bars/{bar.id}/orders/mine").status_code in (302, 403, 404)
