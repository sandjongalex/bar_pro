"""Role-scoped async suborder regressions: totals, stock timing, replay and empty states."""
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import StockBalance
from app.order_services import order_service
from app.order_suborder_models import OrderSuborder
from app.order_suborder_service import order_suborder_service
from legacy_shift import activate_employee
from test_cashier_workspace_async import _async_headers, _cashier, _csrf, _login
from test_workflows import env


def _scenario(env):
    app, owner, bar, foreign, product, server, _, _ = env
    activate_employee(bar, server)
    cashier = _cashier(bar)
    order = order_service.create(
        server, bar.id, "ASYNC-SUB-PARENT",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(cashier, bar.id, order.id)
    cash_service.open(cashier, bar.id, "ASYNC-SUB-CASH", 0)
    db.session.commit()
    return app, bar, foreign, product, server, cashier, order


def _stock(bar_id, product_id):
    return db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar_id, StockBalance.product_id == product_id,
    ))


def test_server_create_async_tracks_pending_delivery_without_early_stock(env):
    app, bar, foreign, product, server, cashier, order = _scenario(env)
    client = app.test_client()
    assert _login(client, server.email).status_code == 302
    url = f"/bars/{bar.id}/orders/{order.id}/suborders/new"
    page = client.get(url)
    token = _csrf(page)
    assert "data-suborder-follow" in page.text
    initial = client.get(f"/bars/{bar.id}/orders/{order.id}/suborders/data")
    assert initial.status_code == 200
    assert initial.get_json()["suborders"] == []
    assert client.get(f"/bars/{bar.id}/orders/{order.id}/suborders/data",
                      headers={"If-None-Match": initial.headers["ETag"]}).status_code == 304

    stock_before = _stock(bar.id, product.id)
    posted = client.post(url, data={
        "csrf_token": token, "product_id": str(product.id), "quantity": "2",
        "note": "AJOUT-TEST",
    }, headers=_async_headers(token), follow_redirects=False)
    assert posted.status_code == 200
    assert posted.get_json()["success"] is True
    sid = posted.get_json()["data"]["id"]
    addition = db.session.get(OrderSuborder, sid)
    assert addition.status == "VALIDATED"
    assert addition.delivery_status == "PENDING"
    assert _stock(bar.id, product.id) == stock_before
    changed = client.get(f"/bars/{bar.id}/orders/{order.id}/suborders/data",
                         headers={"If-None-Match": initial.headers["ETag"]})
    assert changed.status_code == 200
    assert changed.get_json()["suborders"][0]["id"] == sid
    assert changed.get_json()["suborders"][0]["lines"][0]["quantity"] == "2.000000"
    assert client.get(f"/bars/{foreign.id}/orders/{order.id}/suborders/data").status_code in (403, 404)


def test_cashier_queue_live_from_empty_and_delivery_exactly_once(env):
    app, bar, foreign, product, server, cashier, order = _scenario(env)
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    queue_url = f"/bars/{bar.id}/cashier/suborders"
    page = client.get(queue_url)
    token = _csrf(page)
    assert "data-cash-sub-pending-empty" in page.text
    data_url = queue_url + "/data"
    initial = client.get(data_url)
    assert initial.status_code == 200
    assert initial.get_json()["pending"] == []
    assert client.get(data_url, headers={"If-None-Match": initial.headers["ETag"]}).status_code == 304

    stock_before = _stock(bar.id, product.id)
    addition = order_suborder_service.create_server_addition(
        server, bar.id, order.id,
        [{"product_id": product.id, "quantity": 2}],
    )
    db.session.commit()
    assert _stock(bar.id, product.id) == stock_before
    refreshed = client.get(data_url, headers={"If-None-Match": initial.headers["ETag"]})
    assert refreshed.status_code == 200
    assert refreshed.get_json()["pending"][0]["id"] == addition.id
    assert refreshed.get_json()["pending"][0]["lines"][0]["name"] == product.name

    body = {"csrf_token": token, "order_id": str(order.id), "suborder_id": str(addition.id)}
    done = client.post(queue_url, data=body, headers=_async_headers(token), follow_redirects=False)
    assert done.status_code == 200
    assert done.get_json()["data"]["delivery_status"] == "DELIVERED"
    assert _stock(bar.id, product.id) == stock_before - Decimal("2")
    replay = client.post(queue_url, data=body, headers=_async_headers(token), follow_redirects=False)
    assert replay.status_code == 409
    assert replay.get_json()["error"]["code"] == "SUBORDER_ALREADY_DELIVERED"
    assert _stock(bar.id, product.id) == stock_before - Decimal("2")
    after = client.get(data_url)
    assert after.get_json()["pending"] == []
    assert after.get_json()["active_orders"][0]["total"] == str(order.total_amount)


def test_cashier_served_addition_requires_server_validation_no_second_stock_move(env):
    app, bar, foreign, product, server, cashier, order = _scenario(env)
    server_client = app.test_client()
    assert _login(server_client, server.email).status_code == 302
    server_url = f"/bars/{bar.id}/orders/new"
    page = server_client.get(server_url)
    token = _csrf(page)
    assert 'data-server-pending-root' in page.text
    pending_url = f"/bars/{bar.id}/orders/suborders/pending/data"
    empty = server_client.get(pending_url)
    assert empty.status_code == 200
    assert empty.get_json()["pending"] == []

    stock_before = _stock(bar.id, product.id)
    addition = order_suborder_service.create_cashier_addition(
        cashier, bar.id, order.id,
        [{"product_id": product.id, "quantity": 1}],
    )
    db.session.commit()
    assert _stock(bar.id, product.id) == stock_before - Decimal("1")
    assert order.total_amount == Decimal("100")
    changed = server_client.get(pending_url, headers={"If-None-Match": empty.headers["ETag"]})
    assert changed.status_code == 200
    assert changed.get_json()["pending"][0]["id"] == addition.id

    post = {
        "csrf_token": token, "action": "validate_suborder",
        "order_id": str(order.id), "suborder_id": str(addition.id),
    }
    accepted = server_client.post(server_url, data=post, headers=_async_headers(token), follow_redirects=False)
    assert accepted.status_code == 200
    assert accepted.get_json()["data"]["status"] == "VALIDATED"
    db.session.refresh(order)
    assert order.total_amount == Decimal("200")
    assert _stock(bar.id, product.id) == stock_before - Decimal("1")
    repeat = server_client.post(server_url, data=post, headers=_async_headers(token), follow_redirects=False)
    assert repeat.status_code == 422
    db.session.refresh(order)
    assert order.total_amount == Decimal("200")
    assert _stock(bar.id, product.id) == stock_before - Decimal("1")
    assert server_client.get(pending_url).get_json()["pending"] == []


def test_suborder_live_script_and_form_fallback_present(env):
    app, bar, foreign, product, server, cashier, order = _scenario(env)
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    url = f"/bars/{bar.id}/orders/{order.id}/suborders/new"
    page = client.get(url)
    assert page.status_code == 200
    assert "suborders_live.js" in page.text
    token = _csrf(page)
    html = client.post(url, data={
        "csrf_token": token, "product_id": str(product.id), "quantity": "1",
    }, follow_redirects=False)
    assert html.status_code == 302
    assert Path("app/static/suborders_live.js").is_file()
