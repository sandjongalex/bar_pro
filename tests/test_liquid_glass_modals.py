"""Liquid Glass modal UI contracts without changes to payment or exchange APIs."""
from pathlib import Path
import re

from jinja2 import Environment

from app.extensions import db
from app.order_services import order_service
from test_workflows import env

ROOT = Path(__file__).resolve().parents[1]


def _template(path):
    source = (ROOT / "app/templates" / path).read_text(encoding="utf-8")
    Environment().parse(source)
    return source


def test_liquid_glass_css_has_backdrop_fallback_and_accessibility():
    css = (ROOT / "app/static/app.css").read_text(encoding="utf-8")
    assert ".modal-content.modal-liquid-glass" in css
    assert "background: rgba(255,255,255,.45)" in css
    assert "-webkit-backdrop-filter: blur(12px) saturate(180%)" in css
    assert "backdrop-filter: blur(12px) saturate(180%)" in css
    assert 'prefers-reduced-transparency' in css
    assert '[data-bs-theme="dark"]' in css
    assert "@supports not" in css


def test_payment_templates_keep_post_contract_and_new_dialog_triggers():
    for path, form_id, modal_id in (
        ("checkout.html", "checkoutForm", "checkoutPaymentModal"),
        ("cashier_workspace.html", "cashierPaymentForm", "cashierPaymentModal"),
    ):
        template = _template(path)
        assert template.count(f'id="{form_id}"') == 1
        assert template.count(f'id="{modal_id}"') == 1
        assert f'data-bs-target="#{modal_id}"' in template
        assert 'class="modal-content modal-liquid-glass"' in template
        assert 'modal-dialog-scrollable' in template
        assert 'data-bs-dismiss="modal"' in template
        assert 'name="csrf_token"' in template
        assert 'name="action" value="payment"' in template
        assert 'name="order_id"' in template
        assert 'method="post"' in template


def test_exchange_and_refund_templates_keep_post_actions_and_tabs():
    template = _template("cashier_exchanges_v2.html")
    for form_id, modal_id, action in (
        ("exchangeForm", "exchangeOperationModal", "exchange"),
        ("refundForm", "refundOperationModal", "refund"),
    ):
        assert template.count(f'id="{form_id}"') == 1
        assert template.count(f'id="{modal_id}"') == 1
        assert f'data-bs-target="#{modal_id}"' in template
        assert f'name="action" value="{action}"' in template
        assert 'name="csrf_token"' in template
    assert template.count('class="modal-content modal-liquid-glass"') == 2
    assert 'data-operation-panel="exchange"' in template
    assert 'data-operation-panel="refund"' in template
    assert 'data-operation="exchange"' in template
    assert 'data-operation="refund"' in template
    assert 'id="returnedValue"' in template
    assert 'id="refundAmount"' in template


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match, page.text
    return match.group(1)


def test_actual_owner_views_render_payment_and_exchange_modals(env):
    app, owner, bar, *_ = env
    order = order_service.create(
        owner, bar.id, "LIQUID-PAYMENT", [{"product_id": env[4].id, "quantity": 1}]
    )
    order_service.confirm(owner, bar.id, order.id)
    db.session.commit()

    client = app.test_client()
    login = client.get("/login")
    response = client.post("/login", data={
        "csrf_token": _csrf(login),
        "email": owner.email,
        "password": "test-password",
    })
    assert response.status_code == 302

    checkout = client.get(f"/bars/{bar.id}/checkout?order_id={order.id}")
    assert checkout.status_code == 200
    assert 'id="checkoutPaymentModal"' in checkout.text
    assert 'id="checkoutForm"' in checkout.text
    assert 'name="csrf_token"' in checkout.text

    exchanges = client.get(f"/bars/{bar.id}/cashier-exchanges")
    assert exchanges.status_code == 200
    assert 'id="exchangeOperationModal"' in exchanges.text
    assert 'id="refundOperationModal"' in exchanges.text
    assert 'id="exchangeForm"' in exchanges.text
    assert 'id="refundForm"' in exchanges.text
