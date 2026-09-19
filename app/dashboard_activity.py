"""Recent audit activity for the owner/admin dashboard."""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.extensions import db
from app.models import AuditLog, Bar, User
from app.permissions import permissions


_ACTION_LABELS = {
    "orders.create": "Commande créée",
    "orders.note": "Note ajoutée à une commande",
    "orders.deliver": "Commande livrée",
    "orders.cancel": "Commande annulée",
    "orders.return": "Retour de commande",
    "payments.record": "Paiement enregistré",
    "refunds.record": "Remboursement enregistré",
    "inventory.post": "Inventaire validé",
    "inventories.post": "Inventaire validé",
    "inventory.adjust": "Stock ajusté par inventaire",
    "stock.adjust": "Ajustement de stock",
    "stock.loss": "Perte de stock enregistrée",
    "purchases.create": "Achat créé",
    "purchases.receive": "Achat réceptionné",
    "supplier_payments.record": "Paiement fournisseur enregistré",
    "expenses.record": "Dépense enregistrée",
    "expenses.reverse": "Dépense annulée",
    "customers.create": "Client créé",
    "customer_credit.sale": "Vente à crédit enregistrée",
    "customer_credit.payment": "Règlement client enregistré",
    "customer_credit.reverse": "Crédit client annulé",
    "cases.out": "Casiers remis au client",
    "cases.return": "Casiers retournés",
    "cash.open": "Caisse ouverte",
    "cash.close": "Caisse clôturée",
    "cash.entry": "Mouvement de caisse",
    "cash.handover": "Versement de caisse",
    "cash.handover.create": "Remise serveuse préparée",
    "cash.handover.posted": "Remise serveuse reçue",
    "cash.handover.cancelled": "Remise serveuse annulée",
    "staff.assign": "Affectation du personnel",
    "staff.end": "Affectation terminée",
    "products.create": "Produit créé",
    "products.update": "Produit modifié",
}


def _label(action: str) -> str:
    if action in _ACTION_LABELS:
        return _ACTION_LABELS[action]
    text = (action or "Action").replace("_", " ").replace(".", " · ")
    return text[:1].upper() + text[1:]


def _local_dt(value, timezone_name: str):
    if value is None:
        return None
    aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return aware.astimezone(ZoneInfo(timezone_name))


def _today_bounds(bar: Bar):
    tz = ZoneInfo(bar.timezone)
    now_local = datetime.now(tz)
    start = datetime.combine(now_local.date(), time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    end = datetime.combine(now_local.date(), time.max, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return start, end


def dashboard_activity(actor, bar_id: int, limit: int = 12):
    permissions.require(actor, "reports.read", bar_id)
    bar = db.session.get(Bar, bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    rows = db.session.execute(
        select(AuditLog, User)
        .join(User, User.id == AuditLog.actor_id)
        .where(AuditLog.bar_id == bar_id)
        .order_by(AuditLog.occurred_at.desc(), AuditLog.id.desc())
        .limit(limit)
    ).all()

    start_at, end_at = _today_bounds(bar)
    today_count = int(
        db.session.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.bar_id == bar_id,
                AuditLog.occurred_at >= start_at,
                AuditLog.occurred_at <= end_at,
            )
        )
        or 0
    )

    items = []
    for audit, user in rows:
        items.append(
            {
                "id": audit.id,
                "action": audit.action,
                "label": _label(audit.action),
                "actor": user.display_name,
                "actor_category": user.category,
                "target_table": audit.target_table,
                "target_id": audit.target_id,
                "outcome": audit.outcome,
                "reason": audit.reason,
                "occurred_local": _local_dt(audit.occurred_at, bar.timezone),
            }
        )

    return {
        "items": items,
        "today_count": today_count,
        "shown_count": len(items),
        "timezone": bar.timezone,
    }
