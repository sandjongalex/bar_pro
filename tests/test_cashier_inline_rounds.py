"""Regression: cashier queue keeps suborder history, sums only once and blocks payment."""
import base64
import html
import re

from sqlalchemy import select

from app.cash_services import cash_service
from app.extensions import db
from app.models import Payment, StockBalance
from app.order_services import order_service
from app.order_suborder_service import order_suborder_service
from test_cashier_workspace_async import _async_headers, _cashier, _csrf, _login
from test_receipt_suborders import _put_server_on_duty
from test_workflows import env


def _setup(env):
    app, owner, bar, _, product, server, _, _ = env
    _put_server_on_duty(owner, bar, server)
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "INLINE-ROUNDS-CASH", 0)
    order = order_service.create(
        server, bar.id, "INLINE-ROUNDS-ORDER", [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, order.id)
    addition = order_suborder_service.create_server_addition(
        server, bar.id, order.id, [{"product_id": product.id, "quantity": 2}],
        note="Service suivant",
    )
    db.session.commit()
    return app, owner, bar, product, cashier, order, addition


def _stock(bar_id, product_id):
    return db.session.scalar(select(StockBalance.quantity).where(
        StockBalance.bar_id == bar_id, StockBalance.product_id == product_id,
    ))


def _rawbt_detailed(page):
    encoded = html.unescape(re.search(r'id="print-detailed"[^>]*data-rawbt-intent="([^"]+)"', page.text).group(1))
    payload = encoded.removeprefix("intent:base64,").split("#Intent;")[0]
    return base64.b64decode(payload)[8:].decode("ascii")


def test_cashier_inline_round_delivery_preserves_lines_totals_stock_and_invoice_modes(env):
    app, _, bar, product, cashier, order, addition = _setup(env)
    initial_total = order.total_amount
    assert initial_total == 300
    stock_before = _stock(bar.id, product.id)

    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    url = f"/bars/{bar.id}/cashier/workspace"
    page = client.get(url + f"?order_id={order.id}")
    assert page.status_code == 200
    assert "Sous-commande n°1" in page.text
    assert "cashier-round-delivery-form" in page.text
    assert "data-cashier-round-id=" in page.text
    assert "Livraison à confirmer" in page.text
    assert 'id="cashierPaymentModal"' not in page.text
    assert f"/unpaid-orders/{order.id}/suborders/{addition.id}/confirm-delivery" in page.text

    live = client.get(f"/bars/{bar.id}/live/orders")
    assert live.status_code == 200
    row = next(x for x in live.get_json()["orders"] if x["id"] == order.id)
    assert row["pending_delivery"] is True
    assert "Sous-commande n°1" in row["actions_html"]
    assert "cashier-round-delivery-form" in row["actions_html"]
    assert "Livraison à confirmer" in row["actions_html"]

    # Credit must be blocked too, even though it does not call PaymentService.
    token = _csrf(page)
    blocked = client.post(url, data={
        "csrf_token": token, "action": "payment",
        "order_id": str(order.id), "method": "CREDIT",
        "reference": "INLINE-CREDIT-MUST-BLOCK",
    }, headers=_async_headers(token))
    assert blocked.status_code == 409
    assert blocked.get_json()["error"]["code"] == "ORDER_NOT_PAYABLE"
    assert db.session.scalar(select(Payment.id).where(Payment.order_id == order.id)) is None

    confirmed = client.post(
        f"/bars/{bar.id}/unpaid-orders/{order.id}/suborders/{addition.id}/confirm-delivery",
        data={"csrf_token": token},
        headers=_async_headers(token),
    )
    assert confirmed.status_code == 200
    assert confirmed.get_json()["success"] is True
    db.session.refresh(order)
    db.session.refresh(addition)
    assert addition.delivery_status == "DELIVERED"
    assert order.total_amount == initial_total
    assert _stock(bar.id, product.id) == stock_before - 2

    live = client.get(f"/bars/{bar.id}/live/orders")
    row = next(x for x in live.get_json()["orders"] if x["id"] == order.id)
    assert row["pending_delivery"] is False
    assert "Sous-commande n°1" in row["actions_html"]
    assert "Livraison confirmée" in row["actions_html"]
    assert "cashier-round-delivery-form" not in row["actions_html"]
    assert ">Payer</a>" in row["actions_html"]

    workspace = client.get(url + f"?order_id={order.id}")
    assert 'id="cashierPaymentModal"' in workspace.text
    detail = client.get(f"/bars/{bar.id}/orders/{order.id}/detail")
    assert detail.status_code == 200
    assert 'data-server-invoice-mode="cumulative"' in detail.text
    assert 'data-server-invoice-mode="detailed"' in detail.text
    assert "server_invoice_preview.js" in detail.text

    ticket = client.get(f"/bars/{bar.id}/checkout/orders/{order.id}/receipt")
    assert ticket.status_code == 200
    assert "Commande initiale" in ticket.text
    assert "Sous-commande n°1" in ticket.text
    assert 'data-receipt-mode="cumulative"' in ticket.text
    assert 'data-receipt-mode="detailed"' in ticket.text
    detailed = _rawbt_detailed(ticket)
    assert "Commande initiale" in detailed
    assert "Sous-commande 1 - Livree" in detailed
    assert "Total commande: 300 FCFA" in detailed

    again = client.post(
        f"/bars/{bar.id}/unpaid-orders/{order.id}/suborders/{addition.id}/confirm-delivery",
        data={"csrf_token": token}, headers=_async_headers(token),
    )
    assert again.status_code == 409
    assert _stock(bar.id, product.id) == stock_before - 2
    db.session.refresh(order)
    assert order.total_amount == initial_total
