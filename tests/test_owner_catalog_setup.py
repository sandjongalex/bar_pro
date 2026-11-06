import re

from sqlalchemy import select

from app.bar_services import create_bar
from app.default_catalog import DEFAULT_CATALOG, seed_default_catalog
from app.extensions import db
from app.models import Bar, Product, ProductCategory, StockBalance, User
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


def test_owner_configures_empty_legacy_bar_catalog_during_first_dashboard_entry(env):
    app, owner, existing_bar, _, _, _, _, _ = env
    # Legacy bars without a catalogue still use the guided two-step setup.
    # New XAF bars are now seeded automatically through create_bar().
    bar = Bar(
        owner_id=owner.id,
        name="Bar à configurer",
        address="Yaoundé",
        timezone="Africa/Douala",
        currency="XAF",
    )
    db.session.add(bar)
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


def test_new_xaf_bars_receive_independent_editable_default_catalogs(env):
    _, owner, existing_bar, _, _, _, _, _ = env
    admin = User(
        email="catalog-default-admin@example.invalid",
        display_name="Catalog Default Admin",
        category="SUPER_ADMIN",
    )
    admin.set_password("test-password")
    db.session.add(admin)
    db.session.flush()

    def new_bar(name, **kwargs):
        return create_bar(admin, owner.id, {
            "name": name, "timezone": "Africa/Douala",
            "currency": "XAF",
        }, **kwargs)

    first = new_bar("Nouveau bar A")
    second = new_bar("Nouveau bar B")
    db.session.commit()

    for bar in (first, second):
        assert bar.catalog_configured is True
        categories = list(db.session.scalars(
            select(ProductCategory).where(ProductCategory.bar_id == bar.id)
        ))
        products = list(db.session.scalars(
            select(Product).where(Product.bar_id == bar.id)
        ))
        balances = list(db.session.scalars(
            select(StockBalance).where(StockBalance.bar_id == bar.id)
        ))
        assert len(categories) == 6
        assert {c.name for c in categories} == {c[0] for c in DEFAULT_CATALOG}
        assert len(products) == len(balances) == 86
        assert all(p.is_active and p.units_per_case is None for p in products)
        assert all(balance.quantity == 0 for balance in balances)
        can_beer = next(p for p in products if p.name == "33 canette")
        assert can_beer.base_unit == "canette"
        assert can_beer.sale_price == 1000
        assert {p.name for p in products}.issuperset({
            "Fanta", "Coca-Cola", "Elvino blanc", "Elvino rouge"
        })

    # Only the new bars are seeded. The existing tenant remains unchanged.
    assert len(list(db.session.scalars(
        select(Product).where(Product.bar_id == existing_bar.id)
    ))) == 1

    first_beer = db.session.scalar(select(Product).where(
        Product.bar_id == first.id, Product.name == "33 Export",
    ))
    second_beer = db.session.scalar(select(Product).where(
        Product.bar_id == second.id, Product.name == "33 Export",
    ))
    assert first_beer.id != second_beer.id
    first_beer.sale_price = 1250
    db.session.commit()
    db.session.refresh(second_beer)
    assert second_beer.sale_price == 900

    # An explicit second seed does not recreate or overwrite customized products.
    assert seed_default_catalog(first.id) == {
        "categories_created": 0, "products_created": 0,
    }
    db.session.refresh(first_beer)
    assert first_beer.sale_price == 1250

    # Copy-from remains an explicit separate path; it preserves the source
    # catalog prices but gives a new bar its own zero stock and independent IDs.
    clone = new_bar("Copie du bar A", copy_from_id=first.id)
    db.session.commit()
    clone_beer = db.session.scalar(select(Product).where(
        Product.bar_id == clone.id, Product.name == "33 Export",
    ))
    assert clone_beer.sale_price == 1250
    assert clone_beer.id != first_beer.id
    assert len(list(db.session.scalars(
        select(ProductCategory).where(ProductCategory.bar_id == clone.id)
    ))) == 6
