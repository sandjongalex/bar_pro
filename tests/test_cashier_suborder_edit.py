"""Cashier edits one round and live server snapshots see the correction."""
from decimal import Decimal
from sqlalchemy import select

from app.extensions import db
from app.cash_services import cash_service
from app.models import AuditLog, OrderLine, StockBalance
from app.order_services import order_service
from app.order_suborder_models import OrderSuborderLine
from app.order_suborder_service import order_suborder_service
from app.suborder_edit_service import edit_cashier_suborder
from test_cashier_workspace_async import _async_headers, _cashier, _csrf, _login
from test_receipt_suborders import _put_server_on_duty
from test_workflows import env


def stock(bar_id, product_id):
    return Decimal(db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar_id, StockBalance.product_id == product_id)) or 0)


def setup(env):
    app, owner, bar, _, product, server, _, _ = env
    _put_server_on_duty(owner, bar, server)
    cashier = _cashier(bar)
    order = order_service.create(
        server, bar.id, "ROUND-EDIT-PARENT", [{"product_id": product.id, "quantity": 1}])
    order_service.confirm(owner, bar.id, order.id)
    db.session.commit()
    return app, owner, bar, product, server, cashier, order


def test_cashier_can_edit_round_before_and_after_delivery_and_server_sees_changes(env):
    app, owner, bar, product, server, cashier, order = setup(env)
    sub = order_suborder_service.create_server_addition(
        server, bar.id, order.id, [{"product_id": product.id, "quantity": 2}])
    db.session.commit()
    initial_stock = stock(bar.id, product.id)

    # A cashier's workspace requires a legitimately opened cash session.
    # Keep that production guard intact; establish the missing test precondition.
    cash_service.open(cashier, bar.id, "ROUND-EDIT-CASH", 0)
    db.session.commit()

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier/workspace?order_id={order.id}")
    state_url = f"/bars/{bar.id}/order-edits/{order.id}"
    payload = client.get(state_url).get_json()
    assert payload["success"]
    assert len(payload["suborders"]) == 1
    assert payload["suborders"][0]["lines"][0]["quantity"] == "2"
    url = state_url + f"/suborders/{sub.id}"
    csrf = _csrf(page)
    revision = payload["order"]["revision"]
    response = client.post(url, headers=_async_headers(csrf), data={
        "csrf_token": csrf, "order_revision": revision,
        "product_id": product.id, "quantity": 1,
        "reason": "Une seule bouteille demandée",
    })
    assert response.status_code == 200
    db.session.refresh(order)
    db.session.refresh(sub)
    assert order.total_amount == Decimal("200")
    assert sub.total_amount == Decimal("100")
    assert stock(bar.id, product.id) == initial_stock

    # The shared fixture holds an outer Flask app context; a nested context
    # isolates flask.g's login cache for this second independent browser.
    with app.app_context():
        server_client = app.test_client()
        assert _login(server_client, server.email).status_code == 302
        follow = server_client.get(f"/bars/{bar.id}/orders/{order.id}/suborders/data")
        assert follow.status_code == 200
        round_row = next(item for item in follow.get_json()["suborders"] if item["id"] == sub.id)
        assert round_row["lines"][0]["quantity"] == "1"
        assert Decimal(round_row["total"]) == 100

    delivered = order_suborder_service.deliver_by_cashier(cashier, bar.id, order.id, sub.id)
    db.session.commit()
    assert delivered.delivery_status == "DELIVERED"
    assert stock(bar.id, product.id) == initial_stock - 1

    next_state = client.get(state_url).get_json()
    response = client.post(url, headers=_async_headers(csrf), data={
        "csrf_token": csrf, "order_revision": next_state["order"]["revision"],
        "product_id": product.id, "quantity": 2,
        "reason": "Une autre bouteille livrée",
    })
    assert response.status_code == 200
    db.session.refresh(sub)
    db.session.refresh(order)
    assert sub.total_amount == Decimal("200")
    assert order.total_amount == Decimal("300")
    assert stock(bar.id, product.id) == initial_stock - 2
    assert db.session.scalar(select(OrderLine.quantity).where(
        OrderLine.bar_id == bar.id, OrderLine.order_id == order.id)) == 1

    # A replay of the previous revision cannot move stock or change the total.
    replay = client.post(url, headers=_async_headers(csrf), data={
        "csrf_token": csrf, "order_revision": next_state["order"]["revision"],
        "product_id": product.id, "quantity": 3,
    })
    assert replay.status_code == 409
    assert stock(bar.id, product.id) == initial_stock - 2
    logs = list(db.session.scalars(select(AuditLog).where(
        AuditLog.bar_id == bar.id,
        AuditLog.action == "orders.suborder.edit", AuditLog.target_id == sub.id,
    )))
    assert len(logs) == 2
    assert Decimal(logs[0].changes["before"][0]["quantity"]) == 2
    assert Decimal(logs[1].changes["after"][0]["quantity"]) == 2


def test_cashier_edits_own_delivered_round_before_server_validation(env):
    app, _, bar, product, server, cashier, order = setup(env)
    sub = order_suborder_service.create_cashier_addition(
        cashier, bar.id, order.id, [{"product_id": product.id, "quantity": 2}])
    db.session.commit()
    before = stock(bar.id, product.id)
    initial_total = Decimal(order.total_amount)
    from app.order_concurrency import order_revision
    edit_cashier_suborder(cashier, bar.id, order.id, sub.id,
                          [{"product_id": product.id, "quantity": 1}],
                          expected_revision=order_revision(order),
                          reason="Client demande une seule bouteille")
    db.session.commit()
    assert stock(bar.id, product.id) == before + 1
    assert sub.status == "PENDING_VALIDATION"
    db.session.refresh(order)
    assert order.total_amount == initial_total

    client = app.test_client()
    assert _login(client, server.email).status_code == 302
    pending = client.get(f"/bars/{bar.id}/orders/suborders/pending/data")
    assert pending.status_code == 200
    entry = next(item for item in pending.get_json()["pending"] if item["id"] == sub.id)
    assert Decimal(entry["lines"][0]["quantity"]) == 1

    validated = order_suborder_service.validate_by_server(server, bar.id, order.id, sub.id)
    db.session.commit()
    assert validated.status == "VALIDATED"
    db.session.refresh(order)
    assert order.total_amount == initial_total + Decimal("100")
    assert db.session.scalar(select(OrderSuborderLine.quantity).where(
        OrderSuborderLine.order_suborder_id == sub.id)) == 1


def test_cashier_original_line_edit_keeps_validated_round_amount(env):
    _, _, bar, product, server, cashier, order = setup(env)
    sub = order_suborder_service.create_server_addition(
        server, bar.id, order.id, [{"product_id": product.id, "quantity": 1}])
    order_suborder_service.deliver_by_cashier(cashier, bar.id, order.id, sub.id)
    db.session.commit()
    from app.order_edit_service import order_edit_service
    from app.order_concurrency import order_revision
    order_edit_service.edit(cashier, bar.id, order.id,
                            [{"product_id": product.id, "quantity": 2}],
                            expected_revision=order_revision(order))
    db.session.commit()
    db.session.refresh(order)
    assert order.total_amount == Decimal("300")
    db.session.refresh(sub)
    assert sub.total_amount == Decimal("100")


def test_editing_draft_original_lines_keeps_server_round_total(env):
    app, owner, bar, _, product, server, _, _ = env
    _put_server_on_duty(owner, bar, server)
    cashier = _cashier(bar)
    parent = order_service.create(
        server, bar.id, "ROUND-EDIT-DRAFT", [{"product_id": product.id, "quantity": 1}])
    sub = order_suborder_service.create_server_addition(
        server, bar.id, parent.id, [{"product_id": product.id, "quantity": 2}])
    db.session.commit()
    assert Decimal(parent.total_amount) == 300
    from app.order_concurrency import order_revision
    from app.order_edit_service import order_edit_service
    order_edit_service.edit(
        cashier, bar.id, parent.id, [{"product_id": product.id, "quantity": 3}],
        expected_revision=order_revision(parent),
    )
    db.session.commit()
    db.session.refresh(parent)
    assert parent.status == "DRAFT"
    assert Decimal(parent.total_amount) == 500
    assert Decimal(sub.total_amount) == 200
