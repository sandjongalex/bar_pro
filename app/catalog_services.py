"""Tenant-isolated catalogue use cases."""
from decimal import Decimal, InvalidOperation
from werkzeug.utils import secure_filename
from app.extensions import db
from app.models import Product, ProductCategory
from app.permissions import permissions

IMAGE_EXTENSIONS={"jpg","jpeg","png","webp"}
def require(actor,action,bar_id):
    permissions.require(actor,action,bar_id)
def decimal(value,name):
    from app.validation import number
    result=number(value,6 if name=="stock_alert_threshold" else 4)
    if result<0: raise ValueError(name)
    return result

def image_key(upload):
    if not upload: return None
    raise ValueError("IMAGE_STORAGE_UNAVAILABLE")
    name=secure_filename(upload.filename or "")
    if not name or name.rsplit(".",1)[-1].lower() not in IMAGE_EXTENSIONS or upload.mimetype not in {"image/jpeg","image/png","image/webp"}: raise ValueError("INVALID_IMAGE")
    return f"catalog/{__import__('uuid').uuid4().hex}.{name.rsplit('.',1)[-1].lower()}"
def create_product(actor,bar_id,data,upload=None):
    require(actor,"catalog.manage",bar_id)
    category=db.session.get(ProductCategory,data["category_id"])
    if not category or category.bar_id!=bar_id: raise LookupError("NOT_FOUND")
    units=data.get("units_per_case")
    if units is not None and int(units)<=0: raise ValueError("units_per_case")
    item=Product(bar_id=bar_id,category_id=category.id,sku=data["sku"].strip(),name=data["name"].strip(),base_unit=data["base_unit"].strip(),sale_price=decimal(data["sale_price"],"sale_price"),valuation_unit_cost=decimal(data["valuation_unit_cost"],"valuation_unit_cost"),stock_alert_threshold=decimal(data.get("stock_alert_threshold",0),"stock_alert_threshold"),units_per_case=units,image_key=image_key(upload))
    db.session.add(item);return item
def list_products(actor,bar_id,q=None,category_id=None,active=None,page=1,per_page=20):
    require(actor,"catalog.read",bar_id); query=Product.query.filter_by(bar_id=bar_id)
    if q: query=query.filter(Product.name.ilike(f"%{q}%") | Product.sku.ilike(f"%{q}%"))
    if category_id: query=query.filter_by(category_id=category_id)
    if active is not None: query=query.filter_by(is_active=active)
    return query.order_by(Product.name,Product.id).paginate(page=page,per_page=min(per_page,100),error_out=False)
