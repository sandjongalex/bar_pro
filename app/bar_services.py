"""Bar and staff use cases; all persistence is tenant-scoped here."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select

from app.extensions import db
from app.models import Bar, Product, ProductCategory, StaffAssignment, StockBalance, User
from app.permissions import permissions
from app.audit import record


def require(actor, action, bar_id):
    decision = permissions.evaluate(actor, action, bar_id)
    if not decision.allowed:
        raise PermissionError(decision.reason)
    return db.session.get(Bar, bar_id)


def create_owner(actor, data):
    """Create an active bar owner account from the super-admin onboarding flow."""
    if not actor.is_active or actor.category != "SUPER_ADMIN":
        raise PermissionError("FORBIDDEN")

    email = str(data.get("email", "")).strip().lower()
    display_name = str(data.get("display_name", "")).strip()
    password = str(data.get("password", ""))

    if not email or "@" not in email or len(email) > 254:
        raise ValueError("INVALID_OWNER_EMAIL")
    if not display_name or len(display_name) > 120:
        raise ValueError("INVALID_OWNER_NAME")
    if len(password) < 8:
        raise ValueError("WEAK_OWNER_PASSWORD")
    if db.session.scalar(select(User.id).where(User.email == email)):
        raise ValueError("OWNER_EMAIL_EXISTS")

    owner = User(
        email=email,
        display_name=display_name,
        category="OWNER",
        is_active=True,
    )
    owner.set_password(password)
    db.session.add(owner)
    db.session.flush()
    return owner


def create_bar(actor, owner_id, data, copy_from_id=None):
    if not actor.is_active or actor.category != "SUPER_ADMIN":
        raise PermissionError("FORBIDDEN")

    owner = db.session.get(User, owner_id)
    if not owner or owner.category != "OWNER" or not owner.is_active:
        raise LookupError("OWNER_NOT_FOUND")

    name = str(data.get("name", "")).strip()
    address = str(data.get("address", "")).strip() or None
    phone = str(data.get("phone", "")).strip() or None
    timezone_name = str(data.get("timezone", "Africa/Douala")).strip()
    currency = str(data.get("currency", "XAF")).strip().upper()

    if not name or len(name) > 160:
        raise ValueError("INVALID_BAR_NAME")
    if address and len(address) > 500:
        raise ValueError("INVALID_BAR_ADDRESS")
    if phone and len(phone) > 32:
        raise ValueError("INVALID_BAR_PHONE")
    if len(currency) != 3 or not currency.isalpha():
        raise ValueError("INVALID_CURRENCY")

    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("INVALID_TIMEZONE") from None

    try:
        threshold = Decimal(str(data.get("stock_alert_threshold", 0)))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("INVALID_THRESHOLD") from None
    if threshold < 0:
        raise ValueError("INVALID_THRESHOLD")

    bar = Bar(
        owner_id=owner.id,
        name=name,
        timezone=timezone_name,
        currency=currency,
        address=address,
        phone=phone,
        stock_alert_threshold=threshold,
        credit_sales_enabled=bool(data.get("credit_sales_enabled", False)),
    )
    db.session.add(bar)
    db.session.flush()
    record(actor, bar.id, "bars.create", "bars", bar.id, "Création établissement")

    if copy_from_id:
        source = db.session.get(Bar, copy_from_id)
        if not source or source.owner_id != owner.id:
            raise LookupError("NOT_FOUND")
        categories = {}
        for category in db.session.scalars(select(ProductCategory).where(ProductCategory.bar_id == source.id)):
            clone = ProductCategory(bar_id=bar.id, name=category.name, is_active=category.is_active)
            db.session.add(clone)
            db.session.flush()
            categories[category.id] = clone.id
        for product in db.session.scalars(select(Product).where(Product.bar_id == source.id)):
            clone = Product(
                bar_id=bar.id,
                category_id=categories[product.category_id],
                sku=product.sku,
                name=product.name,
                base_unit=product.base_unit,
                sale_price=product.sale_price,
                valuation_unit_cost=product.valuation_unit_cost,
                is_active=product.is_active,
            )
            db.session.add(clone)
            db.session.flush()
            db.session.add(StockBalance(bar_id=bar.id, product_id=clone.id, quantity=0, version=0))
    return bar


def update_bar(actor, bar_id, data):
    bar = require(actor, "bars.update_settings", bar_id)
    if "currency" in data and data["currency"] != bar.currency:
        raise ValueError("CURRENCY_IMMUTABLE")
    if "logo_key" in data:
        raise ValueError("LOGO_STORAGE_UNAVAILABLE")
    if "stock_alert_threshold" in data:
        from app.validation import number
        data["stock_alert_threshold"] = number(data["stock_alert_threshold"], 6)
        if data["stock_alert_threshold"] < 0:
            raise ValueError("INVALID_THRESHOLD")
    if "timezone" in data:
        try:
            ZoneInfo(data["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("INVALID_TIMEZONE") from None
    for field in ("name", "address", "phone", "timezone", "stock_alert_threshold", "credit_sales_enabled"):
        if field in data:
            setattr(bar, field, data[field])
    return bar


def create_employee(actor, bar_id, data, role):
    """Create an employee account and immediately assign it to a bar."""
    require(actor, "staff.manage", bar_id)

    email = str(data.get("email", "")).strip().lower()
    display_name = str(data.get("display_name", "")).strip()
    password = str(data.get("password", ""))

    if not email or "@" not in email or len(email) > 254:
        raise ValueError("INVALID_STAFF_EMAIL")
    if not display_name or len(display_name) > 120:
        raise ValueError("INVALID_STAFF_NAME")
    if len(password) < 8:
        raise ValueError("WEAK_STAFF_PASSWORD")
    if role not in {"BAR_ADMIN", "CASHIER", "SERVER"}:
        raise ValueError("INVALID_STAFF_ROLE")
    if db.session.scalar(select(User.id).where(User.email == email)):
        raise ValueError("STAFF_EMAIL_EXISTS")

    user = User(
        email=email,
        display_name=display_name,
        category="EMPLOYEE",
        is_active=True,
    )
    user.set_password(password)
    db.session.add(user)
    db.session.flush()
    assignment = assign_staff(actor, bar_id, user.id, role)
    return user, assignment


def assign_staff(actor, bar_id, user_id, role):
    require(actor, "staff.manage", bar_id)
    user = db.session.get(User, user_id)
    if not user or user.category != "EMPLOYEE" or not user.is_active:
        raise LookupError("INVALID_STAFF")
    if role not in {"BAR_ADMIN", "CASHIER", "SERVER"}:
        raise ValueError("INVALID_STAFF_ROLE")

    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == user_id,
            StaffAssignment.ended_at.is_(None),
        )
    )
    old_role = assignment.role if assignment else None

    if assignment is None:
        other_assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.user_id == user_id,
                StaffAssignment.ended_at.is_(None),
            )
        )
        if other_assignment:
            raise ValueError("STAFF_ALREADY_ASSIGNED")
        assignment = StaffAssignment(
            bar_id=bar_id,
            user_id=user_id,
            role=role,
            started_at=datetime.now(timezone.utc),
        )
    else:
        assignment.role = role

    db.session.add(assignment)
    db.session.flush()
    record(
        actor,
        bar_id,
        "staff.role.change" if old_role else "staff.role.assign",
        "staff_assignments",
        assignment.id,
        f"Rôle: {old_role or 'AUCUN'} -> {role}",
    )
    return assignment


def end_staff_assignment(actor, bar_id, assignment_id):
    """End one active assignment without deleting historical records."""
    require(actor, "staff.manage", bar_id)
    assignment = db.session.scalar(
        select(StaffAssignment).where(
            StaffAssignment.id == assignment_id,
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.ended_at.is_(None),
        )
    )
    if not assignment:
        raise LookupError("STAFF_ASSIGNMENT_NOT_FOUND")

    assignment.ended_at = datetime.now(timezone.utc)
    db.session.flush()
    record(
        actor,
        bar_id,
        "staff.assignment.end",
        "staff_assignments",
        assignment.id,
        "Fin d'affectation",
    )
    return assignment


def set_employee_active(actor, bar_id, user_id, active):
    """Activate or deactivate an employee account previously attached to the bar."""
    require(actor, "staff.manage", bar_id)
    user = db.session.get(User, user_id)
    if not user or user.category != "EMPLOYEE":
        raise LookupError("INVALID_STAFF")

    belongs_to_bar = db.session.scalar(
        select(StaffAssignment.id).where(
            StaffAssignment.bar_id == bar_id,
            StaffAssignment.user_id == user_id,
        ).limit(1)
    )
    if not belongs_to_bar:
        raise LookupError("INVALID_STAFF")

    active = bool(active)
    if user.is_active == active:
        return user

    if not active:
        assignment = db.session.scalar(
            select(StaffAssignment).where(
                StaffAssignment.bar_id == bar_id,
                StaffAssignment.user_id == user_id,
                StaffAssignment.ended_at.is_(None),
            )
        )
        if assignment:
            assignment.ended_at = datetime.now(timezone.utc)
        user.disabled_at = datetime.now(timezone.utc)
    else:
        user.disabled_at = None

    user.is_active = active
    user.credentials_version += 1
    db.session.flush()
    record(
        actor,
        bar_id,
        "staff.account.activate" if active else "staff.account.deactivate",
        "users",
        user.id,
        "Activation du compte" if active else "Désactivation du compte",
    )
    return user
