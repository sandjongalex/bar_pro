"""Chromium layout smoke tests against real rendered Flask inventory V2 pages.

This module is skipped in the ordinary pytest suite when the optional browser
dependency is not installed. A dedicated GitHub Action installs Chromium and
runs these viewport tests on a disposable SQLite test application.
"""
import re
from urllib.parse import urlparse

import pytest

from test_inventory_v2_approval import make_count
from test_workflows import env
from app.extensions import db
from app.inventory_approval import submit


def _login_owner(client, owner):
    login_page = client.get("/login")
    assert login_page.status_code == 200
    token = re.search(
        r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
        login_page.text,
    )
    assert token, "Login page CSRF field not found"
    response = client.post("/login", data={
        "email": owner.email,
        "password": "test-password",
        "csrf_token": token.group(1),
    })
    assert response.status_code == 302, response.text


def test_inventory_v2_pages_fit_mobile_tablet_and_desktop(env, tmp_path):
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    app, owner, bar, *_ = env
    cashier, inv = make_count(
        env, reference="V2-VIEWPORT", quantity="9",
        justification="Vérification du casier et des bouteilles",
    )
    client = app.test_client()
    _login_owner(client, owner)

    root = "http://inventory.test"
    paths = [
        f"/bars/{bar.id}/inventories/{inv.id}/v2-count",
        f"/bars/{bar.id}/inventories/{inv.id}/v2-report",
        f"/bars/{bar.id}/inventories/v2-policy",
        f"/bars/{bar.id}/inventories",
    ]

    def serve(route):
        url = urlparse(route.request.url)
        path = url.path + ("?" + url.query if url.query else "")
        if route.request.method != "GET":
            route.fulfill(status=405, body="GET-only browser smoke test")
            return
        response = client.get(path)
        headers = {}
        if response.status_code in (301, 302, 303, 307, 308):
            headers["location"] = response.headers["Location"]
        route.fulfill(
            status=response.status_code,
            body=response.data,
            content_type=response.content_type,
            headers=headers,
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            for width in (320, 360, 390, 768, 1366):
                context = browser.new_context(
                    viewport={"width": width, "height": 900},
                    device_scale_factor=1,
                )
                page = context.new_page()
                page.route(root + "/**", serve)
                try:
                    for route_path in paths:
                        response = page.goto(root + route_path, wait_until="domcontentloaded")
                        assert response is not None and response.status == 200, (
                            route_path, response.status if response else None
                        )
                        page.wait_for_timeout(80)
                        assert page.locator("body").is_visible()
                        assert page.evaluate(
                            "() => getComputedStyle(document.body).visibility !== 'hidden'"
                        )
                        scroll_width = page.evaluate(
                            "() => Math.max(document.documentElement.scrollWidth, "
                            "document.body.scrollWidth)"
                        )
                        if scroll_width > width + 1:
                            safe = route_path.replace("/", "_")
                            screenshot = tmp_path / f"overflow-{width}-{safe}.png"
                            page.screenshot(path=str(screenshot), full_page=True)
                        assert scroll_width <= width + 1, (
                            f"Horizontal document overflow {scroll_width}px at "
                            f"viewport {width}px on {route_path}"
                        )
                    if width <= 390:
                        report_path = f"/bars/{bar.id}/inventories/{inv.id}/v2-report"
                        page.goto(root + report_path, wait_until="domcontentloaded")
                        table = page.locator(".inv2-table-scroll")
                        assert table.count() == 1
                        assert table.evaluate(
                            "(node) => getComputedStyle(node).overflowX === 'auto'"
                        )
                finally:
                    context.close()

            # After submission, editing is locked but the preview remains
            # refreshable, even though the counting form is absent.
            submit(cashier, bar.id, inv.id)
            db.session.commit()
            context = browser.new_context(viewport={"width": 360, "height": 800})
            page = context.new_page()
            page.route(root + "/**", serve)
            try:
                response = page.goto(
                    root + f"/bars/{bar.id}/inventories/{inv.id}/v2-count",
                    wait_until="domcontentloaded",
                )
                assert response.status == 200
                assert page.locator("#inv2-form").count() == 0
                refresh = page.locator("#inv2-reconcile-refresh")
                assert refresh.count() == 1
                refresh.click()
                page.wait_for_function(
                    "() => document.querySelector('#inv2-reconcile-refresh').disabled === false"
                )
                assert page.locator("#inv2-reconcile-message").inner_text()
            finally:
                context.close()
        finally:
            browser.close()
