"""Fresh authenticated CSRF for long-running cashier AJAX forms, no business replay."""
from pathlib import Path

from flask import g
from sqlalchemy import func, select

from app.extensions import db
from app.models import Order, StockBalance
from test_cashier_workspace_async import _async_headers
from test_cashier_inline_tender import _cashier_client
from test_workflows import env


def test_session_csrf_requires_login_and_is_never_cached(env):
    app, _, bar, _, product, *_ = env
    anonymous = app.test_client()
    unauthorized = anonymous.get("/session/csrf", headers={"Accept": "application/json"})
    assert unauthorized.status_code == 401
    assert unauthorized.json["error"]["code"] == "SESSION_EXPIRED"
    assert "csrf_token" not in unauthorized.get_data(as_text=True)

    client, _, _ = _cashier_client(env)
    response = client.get("/session/csrf", headers={"Accept": "application/json"})
    assert response.status_code == 200, response.text
    assert response.json["success"] is True
    assert isinstance(response.json["data"]["csrf_token"], str)
    assert response.headers["Cache-Control"].find("no-store") >= 0
    assert response.headers.get("Vary") == "Cookie"


def test_rotated_csrf_does_not_save_a_sale_and_fresh_token_saves_it_once(env):
    app, _, bar, _, product, *_ = env
    client, _, old_token = _cashier_client(env)
    url = f"/bars/{bar.id}/cashier/workspace"
    fields = {
        "csrf_token": old_token,
        "action": "create_sale",
        "reference": "CASHIER-CSRF-ROTATED-ORDER",
        "product_id": str(product.id),
        "quantity": "2",
        "invoice_name": "Client CSRF",
    }

    before_stock = db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id,
    ))
    # A login from another tab can invalidate the form's session-bound token.
    with client.session_transaction() as session:
        session["csrf_token"] = "simulated-different-browser-session-token"
    # env deliberately keeps an app context across requests; production g
    # is request-local, so clear the fixture's cached generate_csrf value.
    g.pop("csrf_token", None)

    stale = client.post(url, data=fields, headers=_async_headers(old_token))
    assert stale.status_code == 400
    assert stale.json["error"]["code"] == "CSRF_REJECTED"
    assert db.session.scalar(select(Order.id).where(
        Order.bar_id == bar.id, Order.reference == fields["reference"],
    )) is None
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id,
    )) == before_stock

    g.pop("csrf_token", None)
    refreshed = client.get("/session/csrf", headers={"Accept": "application/json"})
    assert refreshed.status_code == 200
    fresh_token = refreshed.json["data"]["csrf_token"]
    assert fresh_token != old_token
    new_fields = dict(fields, csrf_token=fresh_token)
    confirmed = client.post(url, data=new_fields, headers=_async_headers(fresh_token))
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json["success"] is True
    order = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.reference == fields["reference"],
    ))
    assert order is not None
    assert order.customer_name_snapshot == "Client CSRF"
    assert db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar.id, StockBalance.product_id == product.id,
    )) == before_stock - 2

    duplicate = client.post(url, data=new_fields, headers=_async_headers(fresh_token))
    assert duplicate.status_code == 409
    assert db.session.scalar(select(func.count(Order.id)).where(
        Order.bar_id == bar.id, Order.reference == fields["reference"],
    )) == 1


def test_browser_refreshes_header_and_embedded_formdata_before_business_post():
    source = Path("app/static/barpro_async.js").read_text(encoding="utf-8")
    assert "await renewCsrf(controller.signal)" in source
    assert "root.fetch('/session/csrf'" in source
    assert "cache: 'no-store'" in source
    assert "headers.set('X-CSRFToken', csrf)" in source
    assert "options.body.set('csrf_token', csrf)" in source
    assert "doc.querySelectorAll('input[name=\"csrf_token\"]')" in source
    assert source.index("await renewCsrf(controller.signal)") < source.index("root.fetch(target.href")
    assert "redirectExpiredSession();" in source
