"""Cashier delivery CSRF refresh: stale sessions must not double-deduct stock."""
from decimal import Decimal

from sqlalchemy import select
from flask import g
from werkzeug.exceptions import TooManyRequests

from app.extensions import db
from app.models import StockBalance
from app.order_suborder_models import OrderSuborder
from app.order_suborder_service import order_suborder_service
from test_async_suborders import _scenario
from test_cashier_workspace_async import _async_headers, _csrf, _login
from test_workflows import env


def _stock(bar_id, product_id):
    return db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar_id, StockBalance.product_id == product_id,
    ))


def test_cashier_unpaid_delivery_recovers_from_rotated_csrf_without_replaying_stock(env):
    app, bar, foreign, product, server, cashier, order = _scenario(env)
    suborder = order_suborder_service.create_server_addition(
        server, bar.id, order.id, [{"product_id": product.id, "quantity": 2}],
    )
    db.session.commit()
    stock_before = _stock(bar.id, product.id)

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    monitor_url = f"/bars/{bar.id}/unpaid-orders"
    confirm_url = (
        f"/bars/{bar.id}/unpaid-orders/{order.id}/"
        f"suborders/{suborder.id}/confirm-delivery"
    )
    page = client.get(monitor_url)
    assert page.status_code == 200
    stale_token = _csrf(page)
    assert "unpaid-delivery-form" in page.text

    data_url = f"{monitor_url}/data"
    live = client.get(data_url)
    assert live.status_code == 200
    assert live.headers.get("Cache-Control", "").find("no-store") >= 0
    first_token = live.headers.get("X-BarPro-CSRF")
    assert first_token
    assert live.get_json()["orders"][0]["pending_deliveries"][0]["confirm_url"] == confirm_url

    unchanged = client.get(data_url, headers={"If-None-Match": live.headers["ETag"]})
    assert unchanged.status_code == 304
    assert unchanged.headers.get("X-BarPro-CSRF")
    assert unchanged.headers.get("ETag") == live.headers["ETag"]

    # Simulates a newly authenticated session/rotated credential after a tab
    # has remained open: old POST token must fail BEFORE the business service.
    with client.session_transaction() as session:
        session["csrf_token"] = "server-side-session-rotation-for-this-test"
    blocked = client.post(
        confirm_url,
        data={"csrf_token": stale_token},
        headers=_async_headers(stale_token),
        follow_redirects=False,
    )
    assert blocked.status_code == 400
    assert blocked.is_json
    assert blocked.get_json()["error"]["code"] == "CSRF_REJECTED"
    assert "Actualisez la page" in blocked.get_json()["error"]["message"]
    assert _stock(bar.id, product.id) == stock_before
    db.session.refresh(suborder)
    assert suborder.delivery_status == "PENDING"

    # The legacy test fixture retains one Flask app_context across synthetic
    # requests. In production g is request-local; clear its cached CSRF token
    # to model the real boundary after the simulated session rotation.
    g.pop("csrf_token", None)

    # Even an unchanged ETag response provides fresh, session-valid CSRF.
    rotated = client.get(data_url, headers={"If-None-Match": live.headers["ETag"]})
    assert rotated.status_code == 304
    fresh_token = rotated.headers["X-BarPro-CSRF"]
    assert fresh_token != stale_token
    success = client.post(
        confirm_url,
        data={"csrf_token": fresh_token},
        headers=_async_headers(fresh_token),
        follow_redirects=False,
    )
    assert success.status_code == 200
    assert success.get_json()["success"] is True
    db.session.refresh(suborder)
    assert suborder.delivery_status == "DELIVERED"
    assert _stock(bar.id, product.id) == stock_before - Decimal("2")

    duplicate = client.post(
        confirm_url,
        data={"csrf_token": fresh_token},
        headers=_async_headers(fresh_token),
        follow_redirects=False,
    )
    assert duplicate.status_code == 409
    assert duplicate.get_json()["error"]["code"] == "SUBORDER_ALREADY_DELIVERED"
    assert _stock(bar.id, product.id) == stock_before - Decimal("2")


def test_unauthorized_unpaid_data_cannot_supply_valid_csrf_for_other_bar(env):
    app, bar, foreign, product, server, cashier, order = _scenario(env)
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    cross_bar = client.get(f"/bars/{foreign.id}/unpaid-orders/data")
    assert cross_bar.status_code == 404
    assert not cross_bar.headers.get("X-BarPro-CSRF")


def test_rate_limit_error_is_distinct_from_csrf_and_never_confirms_delivery(env):
    app = env[0]
    with app.test_request_context(
        "/bars/1/unpaid-orders", headers={"Accept": "application/json"}
    ):
        result, status = app.handle_http_exception(TooManyRequests())
        assert status == 429
        assert result.get_json()["error"]["code"] == "RATE_LIMITED"
        assert "Patientez" in result.get_json()["error"]["message"]
