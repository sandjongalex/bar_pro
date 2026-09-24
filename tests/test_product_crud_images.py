from io import BytesIO
import re

from app.extensions import db
from app.models import Product
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


def _product_form(page, product, **changes):
    data = {
        "csrf_token": _csrf(page),
        "action": "product_update",
        "product_id": str(product.id),
        "category_id": str(product.category_id),
        "name": product.name,
        "sku": product.sku,
        "sale_price": str(product.sale_price),
        "valuation_unit_cost": str(product.valuation_unit_cost),
        "base_unit": product.base_unit,
        "units_per_case": product.units_per_case or "",
        "stock_alert_threshold": str(product.stock_alert_threshold),
    }
    data.update(changes)
    return data


def test_owner_updates_product_and_uploads_replaces_removes_image(env):
    app, owner, bar, foreign, product, *_ = env
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/catalog")
    assert page.status_code == 200
    assert "Modifier" in page.text
    assert "Photo du produit" in page.text

    png = b"\x89PNG\r\n\x1a\n" + b"product-photo"
    data = _product_form(
        page,
        product,
        name="Water Premium",
        sku="WATER-PREMIUM",
        sale_price="250",
        valuation_unit_cost="90",
        stock_alert_threshold="3",
        units_per_case="12",
    )
    data["image"] = (BytesIO(png), "water.png")
    response = client.post(f"/bars/{bar.id}/catalog", data=data, follow_redirects=True)
    assert response.status_code == 200
    assert "a été modifié avec succès" in response.text

    db.session.refresh(product)
    assert product.name == "Water Premium"
    assert product.sku == "WATER-PREMIUM"
    assert str(product.sale_price).startswith("250")
    assert product.units_per_case == 12
    assert product.image_key and product.image_key.endswith(".png")
    first_key = product.image_key

    image = client.get(f"/bars/{bar.id}/catalog/images/{first_key}")
    assert image.status_code == 200
    assert image.data == png

    # Tenant isolation applies to image delivery too.
    assert client.get(f"/bars/{foreign.id}/catalog/images/{first_key}").status_code == 404

    # Removing the photo keeps the product and its history intact.
    page = client.get(f"/bars/{bar.id}/catalog")
    data = _product_form(page, product, remove_image="yes")
    response = client.post(f"/bars/{bar.id}/catalog", data=data, follow_redirects=True)
    assert response.status_code == 200
    db.session.refresh(product)
    assert product.image_key is None
    assert client.get(f"/bars/{bar.id}/catalog/images/{first_key}").status_code == 404


def test_product_archive_and_reactivate_are_soft_delete(env):
    app, owner, bar, _, product, *_ = env
    client = app.test_client()
    _login(client, owner.email)

    page = client.get(f"/bars/{bar.id}/catalog")
    response = client.post(
        f"/bars/{bar.id}/catalog",
        data={
            "csrf_token": _csrf(page),
            "action": "product_disable",
            "product_id": str(product.id),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    db.session.refresh(product)
    assert product.is_active is False
    assert db.session.get(Product, product.id) is not None

    page = client.get(f"/bars/{bar.id}/catalog?active=false")
    response = client.post(
        f"/bars/{bar.id}/catalog",
        data={
            "csrf_token": _csrf(page),
            "action": "product_enable",
            "product_id": str(product.id),
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    db.session.refresh(product)
    assert product.is_active is True


def test_invalid_product_image_is_rejected_without_changing_product(env):
    app, owner, bar, _, product, *_ = env
    client = app.test_client()
    _login(client, owner.email)
    page = client.get(f"/bars/{bar.id}/catalog")

    data = _product_form(page, product, name="Should Not Persist")
    data["image"] = (BytesIO(b"not an image"), "malware.png")
    response = client.post(f"/bars/{bar.id}/catalog", data=data, follow_redirects=True)
    assert response.status_code == 200
    assert "n'est pas une image valide" in response.text

    db.session.refresh(product)
    assert product.name != "Should Not Persist"
    assert product.image_key is None
