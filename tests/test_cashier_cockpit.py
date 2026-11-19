"""Cockpit 2026 regression: backend is authoritative for money and stock."""
from datetime import timedelta
from pathlib import Path
import re

from jinja2 import Environment
from sqlalchemy import func, select

from app.cash_services import cash_service
from app.change_voucher_models import ChangeVoucher
from app.extensions import db
from app.models import Order, Payment, StockBalance, utcnow
from test_cashier_workspace import _cashier, _login, _csrf
from test_workflows import env


def _csrf_cockpit(response):
    match = re.search(r'id="cockpitCsrf"[^>]+value="([^"]+)"', response.text)
    assert match, response.text
    return match.group(1)


def _invoice(client, bar, product, name):
    page = client.get(f"/bars/{bar.id}/cashier/workspace?sale=1")
    result = client.post(f"/bars/{bar.id}/cashier/workspace", data={
        "csrf_token": _csrf(page),
        "action": "create_sale",
        "product_id": str(product.id),
        "quantity": "1",
        "invoice_name": name,
    }, follow_redirects=False)
    assert result.status_code == 302
    invoice = db.session.scalar(select(Order).where(
        Order.bar_id == bar.id, Order.customer_name_snapshot == name,
    ).order_by(Order.id.desc()))
    assert invoice is not None
    return invoice


def test_cockpit_isolation_stock_and_open_session_survive_midnight(env):
    app, _, bar, foreign, product, *_ = env
    cashier = _cashier(bar)
    opened = cash_service.open(cashier, bar.id, "COCKPIT-NIGHT", 5000)
    opened.opened_at = utcnow() - timedelta(days=1)
    db.session.commit()

    client = app.test_client()
    login = _login(client, cashier.email)
    assert login.status_code == 302
    assert f"/bars/{bar.id}/cashier/cockpit" in login.headers["Location"]

    page = client.get(f"/bars/{bar.id}/cashier/cockpit")
    assert page.status_code == 200
    assert "COCKPIT-NIGHT" in page.text
    assert "Clôturer la session &amp; Inventaire" in page.text
    assert 'id="cashierCockpit"' in page.text
    assert 'id="bpWorkspace"' in page.text

    feed = client.get(f"/bars/{bar.id}/cashier/cockpit/orders")
    assert feed.status_code == 200
    assert feed.json["success"] is True

    stock = client.get(f"/bars/{bar.id}/cashier/cockpit/stock")
    assert stock.status_code == 200
    assert stock.json["success"] is True
    assert str(product.id) in {str(item["id"]) for item in stock.json["data"]["items"]}
    # An open cash session is not automatically reset on the civil date change.
    db.session.refresh(opened)
    assert opened.status == "OPEN"

    # Same authenticated cashier must not inspect another establishment.
    assert client.get(f"/bars/{foreign.id}/cashier/cockpit").status_code == 404


def test_cockpit_mixed_payment_partial_then_settled_and_idempotent(env):
    app, _, bar, _, product, *_ = env
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "COCKPIT-MIXED", 0)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/cashier/cockpit")
    token = _csrf_cockpit(page)
    invoice = _invoice(client, bar, product, "Mixte cockpit")
    amount_due = int(invoice.total_amount)

    # Missing CSRF must never create a payment.
    route = "/api/caisse/encaisser"
    bad = client.post(route, json={
        "bar_id": bar.id, "order_id": invoice.id, "expected_due": amount_due,
        "especes": amount_due, "mtn": 0, "orange": 0,
        "cash_change_given": 0, "issue_voucher": False,
        "retain_advance": False, "idempotency_key": "COCKPIT-NO-CSRF",
    })
    assert bad.status_code == 400

    headers = {"X-CSRFToken": token, "Accept": "application/json"}
    partial = client.post(route, json={
        "bar_id": bar.id, "order_id": invoice.id, "expected_due": amount_due,
        "especes": 10, "mtn": 20, "orange": 10,
        "cash_change_given": 0, "issue_voucher": False,
        "retain_advance": False, "idempotency_key": "COCKPIT-MIXED-PARTIAL-1",
    }, headers=headers)
    assert partial.status_code == 200, partial.text
    assert partial.json["success"] is True
    assert partial.json["data"]["payment_finalized"] is False
    assert int(float(partial.json["data"]["remaining"])) == amount_due - 40
    db.session.refresh(invoice)
    assert invoice.payment_status == "PARTIAL"

    final = client.post(route, json={
        "bar_id": bar.id, "order_id": invoice.id, "expected_due": amount_due - 40,
        "especes": amount_due - 40, "mtn": 0, "orange": 0,
        "cash_change_given": 0, "issue_voucher": False,
        "retain_advance": False, "idempotency_key": "COCKPIT-MIXED-FINAL-2",
    }, headers=headers)
    assert final.status_code == 200, final.text
    assert final.json["data"]["payment_finalized"] is True
    db.session.refresh(invoice)
    assert invoice.payment_status == "PAID"
    assert invoice.closed_at is not None
    count_before = db.session.scalar(select(func.count()).select_from(Payment).where(Payment.order_id == invoice.id))
    replay = client.post(route, json={
        "bar_id": bar.id, "order_id": invoice.id, "expected_due": amount_due - 40,
        "especes": amount_due - 40, "mtn": 0, "orange": 0,
        "cash_change_given": 0, "issue_voucher": False,
        "retain_advance": False, "idempotency_key": "COCKPIT-MIXED-FINAL-2",
    }, headers=headers)
    assert replay.status_code >= 400
    assert db.session.scalar(select(func.count()).select_from(Payment).where(Payment.order_id == invoice.id)) == count_before


def test_cockpit_change_voucher_preserves_customer_liability(env):
    app, _, bar, _, product, *_ = env
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "COCKPIT-VOUCHER", 0)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302
    token = _csrf_cockpit(client.get(f"/bars/{bar.id}/cashier/cockpit"))
    invoice = _invoice(client, bar, product, "Bon cockpit")
    due = int(invoice.total_amount)
    reply = client.post("/api/caisse/encaisser", json={
        "bar_id": bar.id, "order_id": invoice.id, "expected_due": due,
        "especes": due + 50, "mtn": 0, "orange": 0,
        "cash_change_given": 0, "issue_voucher": True,
        "retain_advance": False, "idempotency_key": "COCKPIT-BON-DE-MONNAIE",
    }, headers={"X-CSRFToken": token, "Accept": "application/json"})
    assert reply.status_code == 200, reply.text
    assert reply.json["data"]["payment_finalized"] is True
    assert reply.json["data"]["voucher_url"]
    voucher = db.session.scalar(select(ChangeVoucher).where(
        ChangeVoucher.bar_id == bar.id, ChangeVoucher.origin_order_id == invoice.id,
    ))
    assert voucher is not None and voucher.code.startswith("BM-")
    assert int(voucher.balance_amount) == 50
    # Customer's 50 FCFA remains a liability, not a second sale.


def test_cockpit_static_templates_parse_and_no_warehouse_return_button():
    root = Path(__file__).resolve().parents[1]
    template = (root / "app/templates/dashboard_caissiere.html").read_text(encoding="utf-8")
    css = (root / "app/static/css/caissiere.css").read_text(encoding="utf-8")
    script = (root / "app/static/js/caissiere.js").read_text(encoding="utf-8")
    Environment().parse(template)
    assert "row g-3" in template
    assert "#0b0f19" in css and "#111827" in css
    assert "tabular-nums" in css
    assert "MAIN → TRANSIT → CAISSE" in template
    assert "return_to_main" not in template
    assert "/api/caisse/encaisser" in script
    assert "X-CSRFToken" in script
    assert "payment_finalized" in script
