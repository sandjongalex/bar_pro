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
from app.inventory_services import inventory_service


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
    # Cover the actual new-inventory form and legacy inventory counting page,
    # which were not part of the previous responsive test.
    source_product = env[4]
    legacy = inventory_service.create(
        owner, bar.id, "V1-MOBILE-VIEW",
        [source_product.id], "Contrôle affichage sans sortie de stock",
    )
    db.session.commit()
    legacy_id = legacy.id
    client = app.test_client()
    _login_owner(client, owner)

    root = "http://inventory.test"
    paths = [
        f"/bars/{bar.id}/inventories/{inv.id}/v2-count",
        f"/bars/{bar.id}/inventories/{inv.id}/v2-report",
        f"/bars/{bar.id}/inventories/v2-policy",
        f"/bars/{bar.id}/inventories",
        f"/bars/{bar.id}/inventories/new",
        f"/bars/{bar.id}/inventories/{legacy_id}",
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
            for width in (320, 360, 390, 600, 768, 800, 1024, 1366):
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
                    # Creation fields / CTA must remain physically visible.
                    page.goto(root + f"/bars/{bar.id}/inventories/new")
                    assert page.locator(".inventory-new-layout").count() == 1
                    assert page.locator("#reference").is_visible()
                    assert page.locator("#period_end").is_visible()
                    assert page.locator(".inventory-start-button").is_visible()
                    bounds = page.locator(".inventory-start-button").bounding_box()
                    assert bounds and bounds["x"] >= -1
                    assert bounds["x"] + bounds["width"] <= width + 1

                    # V1 table is a 1450px table on desktop, but should be
                    # product cards on phones/tablets with usable count inputs.
                    page.goto(root + f"/bars/{bar.id}/inventories/{legacy_id}")
                    if width < 1200:
                        assert page.locator(".inventory-table tbody tr").count() > 0
                        assert page.locator(".inventory-table").evaluate(
                            "(node) => getComputedStyle(node).minWidth === '0px'"
                        )
                        assert page.locator(".inventory-table tbody tr").first.evaluate(
                            "(node) => getComputedStyle(node).display === 'grid'"
                        )
                        count_input = page.locator(".inventory-count-input").first
                        assert count_input.is_visible()
                        input_box = count_input.bounding_box()
                        assert input_box and input_box["x"] >= -1
                        assert input_box["x"] + input_box["width"] <= width + 1
                        # Compact redesign: important count fields visible,
                        # calculations expandable and action bar not overlaying cards.
                        toggle = page.locator(".inventory-details-toggle").first
                        assert toggle.is_visible()
                        first_card = page.locator(".inventory-table tbody tr").first
                        assert first_card.locator('td[data-label="Stock machine"]').is_visible()
                        assert first_card.locator('td[data-label="Écart machine"]').is_visible()
                        assert not first_card.locator('td[data-label="Achats"]').is_visible()
                        assert toggle.get_attribute("aria-expanded") == "false"
                        assert page.locator(".inventory-actions").evaluate(
                            "(el) => getComputedStyle(el).position === 'static'"
                        )
                        box_before = first_card.bounding_box()
                        assert box_before and box_before["height"] < (410 if width <= 390 else 345)
                        toggle.click()
                        assert toggle.get_attribute("aria-expanded") == "true"
                        assert first_card.locator('td[data-label="Achats"]').is_visible()
                        assert first_card.locator('td[data-label="Montant ventes"]').is_visible()
                        toggle.click()
                        assert toggle.get_attribute("aria-expanded") == "false"
                        assert not first_card.locator('td[data-label="Achats"]').is_visible()

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
