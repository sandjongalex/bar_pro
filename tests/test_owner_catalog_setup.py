import re

from sqlalchemy import select

from app.bar_services import create_bar
from app.extensions import db
from app.models import Product, ProductCategory, StockBalance, User
from test_workflows import env


def _csrf(page):
    match = re.search(r'name=["\']csrf_token["\'][^>]*value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": "test-password", "csrf_token": _csrf(page)},
        follow_redirects=False,
    )


def test_owner_configures_empty_bar_catalog_during_first_dashboard_entry(env):
    app, owner, existing_bar, _, _, _, _, _ = env
    admin = User(
        email="catalog-setup-admin@example.invalid",
        display_name="Catalog Setup Admin",
        category="SUPER_ADMIN",
    )
    admin.set_password("test-password")
    db.session.add(admin)
    db.session.flush()

    bar = create_bar(
        admin,
        owner.id,
        {
            "name": "Bar à configurer",
            "address": "Yaoundé",
            "phone": "+237699000000",
            "timezone": "Africa/Douala",
            "currency": "XAF",
            "stock_alert_threshold": "0",
            "credit_sales_enabled": False,
        },
    )
    db.session.commit()

    assert bar.catalog_configured is False
    assert db.session.scalar(select(ProductCategory.id).where(ProductCategory.bar_id == bar.id)) is None
    assert db.session.scalar(select(Product.id).where(Product.bar_id == bar.id)) is None

    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    dashboard = client.get(f"/dashboard?bar_id={bar.id}", follow_redirects=False)
    assert dashboard.status_code == 302
    assert dashboard.headers["Location"].endswith(f"/bars/{bar.id}/catalog/setup")

    categories_page = client.get(dashboard.headers["Location"])
    assert categories_page.status_code == 200
    category_csrf = _csrf(categories_page)
    next_step = client.post(
        f"/bars/{bar.id}/catalog/setup",
        data={
            "csrf_token": category_csrf,
            "category_names": ["Bières", "Eaux minérales"],
            "custom_categories": "Cocktails",
        },
        follow_redirects=False,
    )
    assert next_step.status_code == 302
    assert next_step.headers["Location"].endswith(f"/bars/{bar.id}/catalog/setup/products")

    products_page = client.get(next_step.headers["Location"])
    assert products_page.status_code == 200
    product_csrf = _csrf(products_page)
    categories = {
        category.name: category.id
        for category in db.session.scalars(
            select(ProductCategory).where(ProductCategory.bar_id == bar.id)
        )
    }
    saved = client.post(
        f"/bars/{bar.id}/catalog/setup/products",
        data={
            "csrf_token": product_csrf,
            "product_name": ["33 Export", "Eau minérale"],
            "category_id": [str(categories["Bières"]), str(categories["Eaux minérales"])],
            "sale_price": ["800", "500"],
            "valuation_unit_cost": ["550", "250"],
            "base_unit": ["bouteille", "bouteille"],
            "units_per_case": ["12", "6"],
            "stock_alert_threshold": ["12", "6"],
        },
        follow_redirects=False,
    )
    assert saved.status_code == 302
    db.session.refresh(bar)
    assert bar.catalog_configured is True

    products = list(db.session.scalars(select(Product).where(Product.bar_id == bar.id).order_by(Product.name)))
    assert len(products) == 2
    assert {item.name for item in products} == {"33 Export", "Eau minérale"}
    beer = next(item for item in products if item.name == "33 Export")
    assert str(beer.sale_price) == "800.0000"
    assert str(beer.valuation_unit_cost) == "550.0000"
    assert beer.units_per_case == 12
    assert beer.sku.startswith("SET-")
    balances = {
        item.product_id: item.quantity
        for item in db.session.scalars(select(StockBalance).where(StockBalance.bar_id == bar.id))
    }
    assert len(balances) == 2
    assert all(quantity == 0 for quantity in balances.values())

    dashboard_after_setup = client.get(f"/dashboard?bar_id={bar.id}", follow_redirects=False)
    assert dashboard_after_setup.status_code == 200
