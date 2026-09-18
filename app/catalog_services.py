"""Tenant-isolated catalogue use cases."""
from app.extensions import db
from app.models import Product, ProductCategory, StockBalance
from app.permissions import permissions

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}


def require(actor, action, bar_id):
    permissions.require(actor, action, bar_id)


def decimal(value, name):
    from app.validation import number

    result = number(value, 6 if name == "stock_alert_threshold" else 4)
    if result < 0:
        raise ValueError(name)
    return result


def image_key(upload):
    if not upload:
        return None
    # Product image storage has deliberately not been enabled yet.
    raise ValueError("IMAGE_STORAGE_UNAVAILABLE")


def list_categories(actor, bar_id, active=None):
    require(actor, "catalog.read", bar_id)
    query = ProductCategory.query.filter_by(bar_id=bar_id)
    if active is not None:
        query = query.filter_by(is_active=active)
    return query.order_by(ProductCategory.name, ProductCategory.id).all()


def create_category(actor, bar_id, name):
    require(actor, "catalog.manage", bar_id)
    normalized = str(name or "").strip()
    if not normalized or len(normalized) > 100:
        raise ValueError("INVALID_CATEGORY_NAME")

    existing = ProductCategory.query.filter(
        ProductCategory.bar_id == bar_id,
        db.func.lower(ProductCategory.name) == normalized.lower(),
    ).first()
    if existing:
        raise ValueError("CATEGORY_EXISTS")

    category = ProductCategory(bar_id=bar_id, name=normalized, is_active=True)
    db.session.add(category)
    db.session.flush()
    return category


def set_category_active(actor, bar_id, category_id, active):
    require(actor, "catalog.manage", bar_id)
    category = db.session.get(ProductCategory, category_id)
    if not category or category.bar_id != bar_id:
        raise LookupError("NOT_FOUND")
    category.is_active = bool(active)
    return category


def create_product(actor, bar_id, data, upload=None):
    require(actor, "catalog.manage", bar_id)

    try:
        category_id = int(data["category_id"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("INVALID_CATEGORY") from None

    category = db.session.get(ProductCategory, category_id)
    if not category or category.bar_id != bar_id or not category.is_active:
        raise LookupError("NOT_FOUND")

    sku = str(data.get("sku", "")).strip()
    name = str(data.get("name", "")).strip()
    base_unit = str(data.get("base_unit", "")).strip()
    if not sku or len(sku) > 64:
        raise ValueError("INVALID_SKU")
    if not name or len(name) > 160:
        raise ValueError("INVALID_PRODUCT_NAME")
    if not base_unit or len(base_unit) > 16:
        raise ValueError("INVALID_BASE_UNIT")

    duplicate = Product.query.filter(
        Product.bar_id == bar_id,
        db.func.lower(Product.sku) == sku.lower(),
    ).first()
    if duplicate:
        raise ValueError("SKU_EXISTS")

    units_raw = data.get("units_per_case")
    units = None
    if units_raw not in (None, ""):
        try:
            units = int(units_raw)
        except (TypeError, ValueError):
            raise ValueError("INVALID_UNITS_PER_CASE") from None
        if units <= 0:
            raise ValueError("INVALID_UNITS_PER_CASE")

    item = Product(
        bar_id=bar_id,
        category_id=category.id,
        sku=sku,
        name=name,
        base_unit=base_unit,
        sale_price=decimal(data.get("sale_price"), "sale_price"),
        valuation_unit_cost=decimal(data.get("valuation_unit_cost"), "valuation_unit_cost"),
        stock_alert_threshold=decimal(data.get("stock_alert_threshold", 0), "stock_alert_threshold"),
        units_per_case=units,
        image_key=image_key(upload),
        is_active=True,
    )
    db.session.add(item)
    db.session.flush()
    db.session.add(StockBalance(bar_id=bar_id, product_id=item.id, quantity=0, version=0))
    db.session.flush()
    return item


def update_product(actor, bar_id, product_id, data):
    require(actor, "catalog.manage", bar_id)
    item = Product.query.filter_by(bar_id=bar_id, id=product_id).first()
    if not item:
        raise LookupError("NOT_FOUND")

    if "name" in data:
        name = str(data["name"] or "").strip()
        if not name or len(name) > 160:
            raise ValueError("INVALID_PRODUCT_NAME")
        item.name = name

    if "sale_price" in data:
        item.sale_price = decimal(data["sale_price"], "sale_price")
    if "valuation_unit_cost" in data:
        item.valuation_unit_cost = decimal(data["valuation_unit_cost"], "valuation_unit_cost")
    if "stock_alert_threshold" in data:
        item.stock_alert_threshold = decimal(data["stock_alert_threshold"], "stock_alert_threshold")
    if "is_active" in data:
        item.is_active = bool(data["is_active"])
    return item


def list_products(actor, bar_id, q=None, category_id=None, active=None, page=1, per_page=20):
    require(actor, "catalog.read", bar_id)
    query = Product.query.filter_by(bar_id=bar_id)
    if q:
        query = query.filter(Product.name.ilike(f"%{q}%") | Product.sku.ilike(f"%{q}%"))
    if category_id:
        query = query.filter_by(category_id=category_id)
    if active is not None:
        query = query.filter_by(is_active=active)
    return query.order_by(Product.name, Product.id).paginate(
        page=page,
        per_page=min(per_page, 100),
        error_out=False,
    )
