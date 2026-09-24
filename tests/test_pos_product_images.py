import re
from pathlib import Path

from app.extensions import db
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def test_product_image_map_exposes_active_bar_product_images(env):
    app, owner, bar, _, product, *_ = env
    product.image_key = "beer-photo.jpg"
    db.session.commit()

    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    response = client.get(f"/bars/{bar.id}/catalog/image-map")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["images"][str(product.id)].endswith(
        f"/bars/{bar.id}/catalog/images/beer-photo.jpg"
    )


def test_product_image_ui_targets_server_cashier_and_suborder_cards():
    script = Path("app/static/product_images_ui.js").read_text(encoding="utf-8")
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")

    assert "product_images_ui.js" in layout
    assert ".pos-product[data-product-id]" in script
    assert ".suborder-product[data-product-id]" in script
    assert "/catalog/image-map" in script
    assert "product-image-slot" in script
    assert "has-image" in script
