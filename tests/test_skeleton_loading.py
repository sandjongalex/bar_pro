"""Skeleton loading UI contracts: Jinja, theme, AJAX and real Flask responses."""

from pathlib import Path
import re

from jinja2 import Environment

from test_workflows import env


ROOT = Path(__file__).resolve().parents[1]


def test_skeleton_assets_in_shared_layout_and_correct_defer_order():
    layout = (ROOT / "app/templates/layout.html").read_text(encoding="utf-8")
    Environment().parse(layout)
    assert "filename='skeleton_loading.css'" in layout
    assert "filename='skeleton_loading.js'" in layout
    assert layout.index("dark_theme.css") < layout.index("skeleton_loading.css")
    assert layout.index("skeleton_loading.js") < layout.index("cashier_live_ui.js")
    assert layout.index("skeleton_loading.js") < layout.index("order_edit_ui.js")


def test_skeleton_shimmer_reserves_shape_and_adapts_to_dark_and_reduced_motion():
    css = (ROOT / "app/static/skeleton_loading.css").read_text(encoding="utf-8")
    assert "@keyframes shimmer" in css
    assert ".bp-skeleton-editor" in css
    assert "min-height: 228px" in css
    assert ".bp-skeleton-queue" in css
    assert "min-height: 84px" in css
    assert ".bp-skeleton-unpaid-grid" in css
    assert "min-height: 238px" in css
    assert '[data-bs-theme="dark"]' in css
    assert "prefers-reduced-motion: reduce" in css
    assert "forced-colors: active" in css


def test_async_screen_scripts_keep_existing_data_and_error_recovery():
    editor = (ROOT / "app/static/order_edit_ui.js").read_text(encoding="utf-8")
    cashier = (ROOT / "app/static/cashier_live_ui.js").read_text(encoding="utf-8")
    unpaid = (ROOT / "app/static/unpaid_orders_ui.js").read_text(encoding="utf-8")
    skeleton = (ROOT / "app/static/skeleton_loading.js").read_text(encoding="utf-8")

    assert "window.BarProSkeleton" in editor
    assert "aria-busy" in editor
    assert "renderEditor(host, payload, context)" in editor
    assert "renderLocked(host, payload.error" in editor
    assert "host.removeAttribute('aria-busy')" in editor

    assert "!queue.querySelector('.cashier-order-link')" in cashier
    assert "!initialLoadAttempted" in cashier
    assert "bp-skeleton-loading" in cashier
    assert "section.removeAttribute('aria-busy')" in cashier
    assert "section.querySelector('[data-skeleton-placeholder]')?.remove()" in cashier

    assert "!grid.querySelector('[data-unpaid-order-id]')" in unpaid
    assert "!initialLoadAttempted" in unpaid
    assert "window.BarProSkeleton.unpaid()" in unpaid
    assert "if (empty) empty.hidden = false" in unpaid
    assert "grid.removeAttribute('aria-busy')" in unpaid

    for template in ("editor", "queue", "unpaid"):
        assert "function " + template + "()" in skeleton
    assert 'role="status"' in skeleton
    assert "visually-hidden" in skeleton
    assert "window.BarProSkeleton = Object.freeze" in skeleton


def _csrf(response):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', response.text)
    assert match is not None
    return match.group(1)


def test_authenticated_cashier_and_unpaid_views_keep_server_rendered_data(env):
    app, owner, bar, *_ = env
    client = app.test_client()
    login = client.get("/login")
    response = client.post(
        "/login",
        data={"email": owner.email, "password": "test-password", "csrf_token": _csrf(login)},
    )
    assert response.status_code == 302

    unpaid = client.get(f"/bars/{bar.id}/unpaid-orders")
    assert unpaid.status_code == 200
    assert 'data-unpaid-grid' in unpaid.text
    assert 'data-unpaid-empty' in unpaid.text
    assert 'skeleton_loading.js' in unpaid.text
    assert 'skeleton_loading.css' in unpaid.text
    assert 'unpaid_orders_ui.js' in unpaid.text

    dashboard = client.get("/dashboard")
    assert dashboard.status_code == 200
    assert 'skeleton_loading.js' in dashboard.text
    assert 'skeleton_loading.css' in dashboard.text
