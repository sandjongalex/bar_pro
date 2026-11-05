import re

from sqlalchemy import select

from app.extensions import db
from app.models import Product, ProductCategory
from test_workflows import env


ASYNC_HEADERS = {
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
    "X-BarPro-Async": "1",
}


def _csrf(page):
    match = re.search(
        r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']',
        page.text,
    )
    assert match is not None, page.text
    return match.group(1)


def _login(client, email):
    page = client.get("/login")
    return client.post(
        "/login",
        data={
            "email": email,
            "password": "test-password",
            "csrf_token": _csrf(page),
        },
        follow_redirects=False,
    )


def _catalog_urls(page):
    products = re.search(r'data-products-url=["\']([^"\']+)["\']', page.text)
    categories = re.search(r'data-categories-url=["\']([^"\']+)["\']', page.text)
    assert products and categories, page.text
    return products.group(1), categories.group(1)


def test_catalog_inline_product_update_returns_json_without_redirect(env):
    app, owner, bar, _, product, *_ = env
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/catalog")
    products_url, _ = _catalog_urls(page)
    response = client.post(
        products_url,
        headers=ASYNC_HEADERS,
        data={
            "csrf_token": _csrf(page),
            "product_id": str(product.id),
            "name": "Water modifiée",
            "sku": product.sku,
            "category_id": str(product.category_id),
            "sale_price": "250",
            "valuation_unit_cost": "90",
            "base_unit": product.base_unit,
            "units_per_case": "12",
            "stock_alert_threshold": "2",
            "is_active": "true",
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.is_json and response.json["success"] is True
    db.session.refresh(product)
    assert product.name == "Water modifiée"
    assert str(product.sale_price).startswith("250")
    assert product.units_per_case == 12


def test_catalog_product_can_be_inserted_between_existing_products(env):
    app, owner, bar, _, anchor, *_ = env
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/catalog")
    products_url, _ = _catalog_urls(page)
    response = client.post(
        products_url,
        headers=ASYNC_HEADERS,
        data={
            "csrf_token": _csrf(page),
            "name": "Produit inséré",
            "sku": "INSERTED-BETWEEN",
            "category_id": str(anchor.category_id),
            "sale_price": "500",
            "valuation_unit_cost": "300",
            "base_unit": "bouteille",
            "units_per_case": "12",
            "stock_alert_threshold": "0",
            "initial_quantity": "0",
            "insert_before_id": str(anchor.id),
        },
    )

    assert response.status_code == 201, response.get_data(as_text=True)
    assert response.json["success"] is True
    inserted = db.session.scalar(
        select(Product).where(
            Product.bar_id == bar.id,
            Product.sku == "INSERTED-BETWEEN",
        )
    )
    db.session.refresh(anchor)
    assert inserted is not None
    assert inserted.display_order < anchor.display_order


def test_catalog_category_can_be_renamed_without_redirect(env):
    app, owner, bar, _, product, *_ = env
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/catalog")
    _, categories_url = _catalog_urls(page)
    category = db.session.get(ProductCategory, product.category_id)
    response = client.post(
        categories_url,
        headers=ASYNC_HEADERS,
        data={
            "csrf_token": _csrf(page),
            "category_id": str(category.id),
            "name": "Boissons renommées",
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    assert response.json["success"] is True
    db.session.refresh(category)
    assert category.name == "Boissons renommées"


def test_catalog_manager_renders_all_products_without_pagination(env):
    app, owner, bar, _, product, *_ = env
    category_id = product.category_id
    for index in range(105):
        db.session.add(
            Product(
                bar_id=bar.id,
                category_id=category_id,
                sku=f"CATALOG-ALL-{index:03d}",
                name=f"Produit catalogue {index:03d}",
                base_unit="unité",
                sale_price=100,
                valuation_unit_cost=50,
                stock_alert_threshold=0,
                display_order=(index + 10) * 1000,
            )
        )
    db.session.commit()

    client = app.test_client()
    assert _login(client, owner.email).status_code == 302
    page = client.get(f"/bars/{bar.id}/catalog")
    assert page.status_code == 200
    assert "Produit catalogue 104" in page.text
    assert "catalog-pagination" not in page.text
