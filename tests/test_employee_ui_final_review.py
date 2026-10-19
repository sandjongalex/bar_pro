"""Final employee UI contracts: role navigation, progressive refresh and safe assets.
Rendering checks complement (not replace) manual visual tests on physical devices.
"""
from pathlib import Path
import re

from jinja2 import Environment

from app.cash_services import cash_service
from app.extensions import db
from test_cashier_workspace import _cashier, _csrf, _login
from test_workflows import env

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_employee_templates_compile_and_render_one_primary_navigation(env):
    app, _, bar, foreign, *_ = env
    cashier = _cashier(bar)
    cash_service.open(cashier, bar.id, "EMPLOYEE-UI-FINAL", 1000)
    db.session.commit()
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    for path in (
        f"/bars/{bar.id}/cashier/workspace",
        f"/bars/{bar.id}/cashier/invoices",
        f"/bars/{bar.id}/cashier/suborders",
        f"/bars/{bar.id}/cashier-session",
    ):
        response = client.get(path)
        assert response.status_code == 200, (path, response.status_code)
        html = response.text
        assert html.count('data-employee-primary-nav') == 1, path
        assert 'data-employee-role="CASHIER"' in html, path
        assert 'cashier-mobile-bar' not in html
        assert 'invoice-mobile-nav' not in html
        assert 'employee_mobile_nav.js' in html
        assert 'employee_ui_review.css' in html

    workspace = client.get(f"/bars/{bar.id}/cashier/workspace")
    assert workspace.text.count('id="mobileQuickSale"') == 1
    # The payment form exists only after selecting a payable order.
    # With an empty queue, the new-sale controls must still be accessible.
    assert 'id="quickSaleForm"' in workspace.text
    assert 'id="openQuickSale"' in workspace.text or 'id="emptyQuickSale"' in workspace.text
    invoice = client.get(f"/bars/{bar.id}/cashier/invoices")
    assert 'invoiceFilters' in invoice.text
    assert 'Imprimer' in invoice.text or 'invoiceResults' in invoice.text
    assert client.get(f"/bars/{foreign.id}/cashier/workspace").status_code in (403, 404)


def test_server_navigation_does_not_duplicate_cashier_main_bar(env):
    from legacy_shift import activate_employee
    app, _, bar, _, _, server, *_ = env
    activate_employee(bar, server)
    client = app.test_client()
    assert _login(client, server.email).status_code == 302
    for path in (f"/bars/{bar.id}/orders/new", f"/bars/{bar.id}/orders/mine"):
        html = client.get(path).text
        assert html.count('data-employee-primary-nav') == 1
        assert 'data-employee-role="SERVER"' in html
        assert 'data-server-bottom-link="new"' in html
        assert 'data-server-bottom-link="orders"' in html
        assert 'mobileQuickSale' not in html


def test_main_templates_and_styles_keep_critical_actions_and_responsive_coverage():
    layout = read("app/templates/layout.html")
    styles = read("app/static/employee_ui_review.css")
    nav = read("app/templates/_context_navigation.html")
    for path in (
        "app/templates/layout.html", "app/templates/_app_navigation.html",
        "app/templates/_context_navigation.html", "app/templates/cashier_workspace.html",
        "app/templates/cashier_workspace_v2.html", "app/templates/cashier_invoices.html",
        "app/templates/cashier_suborders.html", "app/templates/cashier_history.html",
    ):
        Environment().parse(read(path))
    assert layout.count('class="global-mobile-bottom d-md-none"') == 1
    assert "endpoint != 'cashier_workspace_web.workspace'" not in layout
    assert "cashier_workspace_web.workspace" in layout
    assert 'mobileQuickSale' in layout
    assert "body:has(.context-shortcuts) .pos-hero" not in nav
    assert ".cash-sub-mobile{display:block;position:fixed" not in read("app/templates/cashier_suborders.html")
    for size in ("320px", "360px", "430px", "768px", "1024px", "1366px"):
        assert size in styles, size
    assert "prefers-reduced-motion: reduce" in styles
    assert "bp-keyboard-open" in styles
    assert "safe-area-inset-bottom" in styles
    assert "data-bs-theme=\"dark\"" in styles
    assert ".modal-liquid-glass" in read("app/static/dark_theme.css")


def test_ajax_polls_preserve_focused_or_busy_cards_and_do_not_repost():
    invoices = read("app/static/cashier_invoices_ui.js")
    unpaid = read("app/static/unpaid_orders_ui.js")
    suborders = read("app/static/suborders_live.js")
    for script in (invoices, unpaid, suborders):
        assert "focused" in script
        assert "aria-busy" in script
        assert "replaceWith" in script
        assert "If-None-Match" in script or "etag" in script.lower()
    assert "card.contains(focused)" in suborders
    assert "node.contains?.(focused)" in unpaid
    assert "!node.contains(focused)" in invoices
    assert "paymentForm" in invoices
    assert "selectedIds" in unpaid
    assert "financial_complete" in read("app/cashier_financial_intents.py")


def test_pwa_printing_and_financial_guardrails_are_preserved():
    layout = read("app/templates/layout.html")
    assert "pwa_install.js" in layout and "serviceWorker.register" in layout
    assert 'manifest.webmanifest' in layout
    assert (ROOT / "app/static/icons/pwa-192.png").is_file()
    assert (ROOT / "app/static/icons/pwa-512.png").is_file()
    assert 'checkout_web.receipt' in read("app/templates/cashier_history.html")
    assert 'print_voucher' in read("app/templates/change_vouchers.html")
    assert "cashier_payment_idempotency" in read("app/cashier_invoices_web.py")
    assert 'financial_claim' in read("app/cashier_financial_intents.py")
    assert "WTF_CSRF_ENABLED" in read("tests/test_workflows.py")
