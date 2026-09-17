"""Fictional test factories; none of these values may represent production data."""
from app.models import Bar, User, utcnow


def owner(email="owner.factory.demo@example.invalid"):
    value = User(email=email, display_name="DEMO Factory Owner", category="OWNER")
    value.set_password("DEMO-ONLY")
    return value


def bar(owner_id, name="DEMO Factory Bar"):
    return Bar(owner_id=owner_id, name=name, timezone="Africa/Douala", currency="XAF")
