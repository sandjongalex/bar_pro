"""Tenant-isolated catalogue use cases."""
from app.extensions import db
from app.models import Product, ProductCategory, StockBalance
from app.permissions import permissions
from app.product_display_order import product_order_expression
from app.product_images import save_product_image


def require(actor, action, bar_id):
    permissions.require(actor, action, bar_id)


def decimal(value, name):
    from app.validation import number

    result = number(value, 6 if name == "stock_alert_threshold" else 4)
    if result < 0:
        raise ValueError(name)
    return result


def image_key(upload):
    return save_product_image(upload)


def _category(bar_id, category_id, *, active_required=True):
    try:
        category_id = int(category_id)
    except (TypeError, ValueError):
        raise ValueError("INVALID_CATEGORY") from None
    category = db.session.get(ProductCategory, category_id)
    if not category or category.bar_id != bar_id or (active_required and not category.is_active):
        raise LookupError("NOT_FOUND")
    return category


def _units_per_case(value):
    if value in (None, ""):
        return None
    try:
        units = int(value)
    except (TypeError, ValueError):
        raise ValueError("INVALID_UNITS_PER_CASE") from None
    if units <= 0:
        raise ValueError("INVALID_UNITS_PER_CASE")
    return units


def _sku(bar_id, value, *, exclude_product_id=None):
    sku = str(value or "").strip()
    if not sku or len(sku) > 64:
        raise ValueError("INVALID_SKU")
    duplicate = Product.query.filter(
        Product.bar_id == bar_id,
        db.func.lower(Product.sku) == sku.lower(),
    )
    if exclude_product_id is not None:
        duplicate = duplicate.filter(Product.id != exclude_product_id)
    if duplicate.first():
        raise ValueError("SKU_EXISTS")
    return sku


def _name(value):
    name = str(value or "").strip()
    if not name or len(name) > 160:
        raise ValueError("INVALID_PRODUCT_NAME")
    return name


def _base_unit(value):
    base_unit = str(value or "").strip()
    if not base_unit or len(base_unit) > 16:
        raise ValueError("INVALID_BASE_UNIT")
    return base_unit


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

    category = _category(bar_id, data.get("category_id"))
    item = Product(
        bar_id=bar_id,
        category_id=category.id,
        sku=_sku(bar_id, data.get("sku")),
        name=_name(data.get("name")),
        base_unit=_base_unit(data.get("base_unit")),
        sale_price=decimal(data.get("sale_price"), "sale_price"),
        valuation_unit_cost=decimal(data.get("valuation_unit_cost"), "valuation_unit_cost"),
        stock_alert_threshold=decimal(data.get("stock_alert_threshold", 0), "stock_alert_threshold"),
        units_per_case=_units_per_case(data.get("units_per_case")),
        image_key=image_key(upload),
        is_active=True,
    )
    db.session.add(item)
    db.session.flush()
    db.session.add(StockBalance(bar_id=bar_id, product_id=item.id, quantity=0, version=0))
    db.session.flush()
    return item


def update_product(actor, bar_id, product_id, data, upload=None):
    """Update all editable product catalogue fields, optionally replacing its image."""
    require(actor, "catalog.manage", bar_id)
    item = Product.query.filter_by(bar_id=bar_id, id=product_id).first()
    if not item:
        raise LookupError("NOT_FOUND")

    if "category_id" in data:
        item.category_id = _category(bar_id, data["category_id"]).id
    if "sku" in data:
        item.sku = _sku(bar_id, data["sku"], exclude_product_id=item.id)
    if "name" in data:
        item.name = _name(data["name"])
    if "base_unit" in data:
        item.base_unit = _base_unit(data["base_unit"])
    if "sale_price" in data:
        item.sale_price = decimal(data["sale_price"], "sale_price")
    if "valuation_unit_cost" in data:
        item.valuation_unit_cost = decimal(data["valuation_unit_cost"], "valuation_unit_cost")
    if "stock_alert_threshold" in data:
        item.stock_alert_threshold = decimal(data["stock_alert_threshold"], "stock_alert_threshold")
    if "units_per_case" in data:
        item.units_per_case = _units_per_case(data["units_per_case"])
    if "is_active" in data:
        item.is_active = bool(data["is_active"])
    if data.get("remove_image"):
        item.image_key = None
    if upload and getattr(upload, "filename", ""):
        item.image_key = image_key(upload)
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
    return query.order_by(product_order_expression(Product.name), Product.name, Product.id).paginate(
        page=page,
        per_page=min(per_page, 100),
        error_out=False,
    )
