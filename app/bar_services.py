"""Bar and staff use cases; all persistence is tenant-scoped here."""
from datetime import datetime, timezone
from sqlalchemy import select
from app.extensions import db
from app.models import Bar, Product, ProductCategory, StaffAssignment, StockBalance, User
from app.permissions import permissions
from app.audit import record

def require(actor, action, bar_id):
    decision=permissions.evaluate(actor,action,bar_id)
    if not decision.allowed: raise PermissionError(decision.reason)
    return db.session.get(Bar,bar_id)

def create_bar(actor, owner_id, data, copy_from_id=None):
    if not actor.is_active or actor.category != "SUPER_ADMIN": raise PermissionError("FORBIDDEN")
    owner=db.session.get(User,owner_id)
    if not owner or owner.category!="OWNER" or not owner.is_active: raise LookupError("OWNER_NOT_FOUND")
    bar=Bar(owner_id=owner.id,name=data["name"],timezone=data["timezone"],currency=data.get("currency","XAF"),address=data.get("address"),phone=data.get("phone"),stock_alert_threshold=data.get("stock_alert_threshold",0),credit_sales_enabled=bool(data.get("credit_sales_enabled",False)))
    db.session.add(bar); db.session.flush(); record(actor, bar.id, "bars.create", "bars", bar.id, "Création établissement")
    if copy_from_id:
        source=db.session.get(Bar,copy_from_id)
        if not source or source.owner_id!=owner.id: raise LookupError("NOT_FOUND")
        categories={}
        for category in db.session.scalars(select(ProductCategory).where(ProductCategory.bar_id==source.id)):
            clone=ProductCategory(bar_id=bar.id,name=category.name,is_active=category.is_active);db.session.add(clone);db.session.flush();categories[category.id]=clone.id
        for product in db.session.scalars(select(Product).where(Product.bar_id==source.id)):
            clone=Product(bar_id=bar.id,category_id=categories[product.category_id],sku=product.sku,name=product.name,base_unit=product.base_unit,sale_price=product.sale_price,valuation_unit_cost=product.valuation_unit_cost,is_active=product.is_active);db.session.add(clone);db.session.flush();db.session.add(StockBalance(bar_id=bar.id,product_id=clone.id,quantity=0,version=0))
    return bar

def update_bar(actor, bar_id, data):
    bar=require(actor,"bars.update_settings",bar_id)
    if "currency" in data and data["currency"]!=bar.currency: raise ValueError("CURRENCY_IMMUTABLE")
    if "logo_key" in data: raise ValueError("LOGO_STORAGE_UNAVAILABLE")
    if "stock_alert_threshold" in data:
        from app.validation import number
        data["stock_alert_threshold"]=number(data["stock_alert_threshold"],6)
        if data["stock_alert_threshold"]<0: raise ValueError("INVALID_THRESHOLD")
    if "timezone" in data:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try: ZoneInfo(data["timezone"])
        except (ZoneInfoNotFoundError,ValueError): raise ValueError("INVALID_TIMEZONE") from None
    for field in ("name","address","phone","timezone","stock_alert_threshold","credit_sales_enabled"):
        if field in data: setattr(bar,field,data[field])
    return bar

def assign_staff(actor,bar_id,user_id,role):
    require(actor,"staff.manage",bar_id)
    user=db.session.get(User,user_id)
    if not user or user.category!="EMPLOYEE" or role not in {"BAR_ADMIN","CASHIER","SERVER"}: raise LookupError("INVALID_STAFF")
    assignment=db.session.scalar(select(StaffAssignment).where(StaffAssignment.bar_id==bar_id,StaffAssignment.user_id==user_id,StaffAssignment.ended_at.is_(None)))
    old_role=assignment.role if assignment else None
    if assignment:
        assignment.role=role
    else:
        assignment=StaffAssignment(bar_id=bar_id,user_id=user_id,role=role,started_at=datetime.now(timezone.utc))
    db.session.add(assignment)
    db.session.flush()
    record(actor,bar_id,"staff.role.change" if old_role else "staff.role.assign","staff_assignments",assignment.id,f"Rôle: {old_role or 'AUCUN'} -> {role}")
    return assignment
