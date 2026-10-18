"""Atomic, tenant- and actor-scoped deduplication of server POS order creations.

Order and idempotency receipt commit in ONE database transaction. Do not commit
inside this service; the calling Flask route owns the transaction boundary.
The existing IdempotencyRecord schema provides the unique constraint.
"""
from __future__ import annotations

from hashlib import sha256
import json
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import IdempotencyRecord, Order, utcnow

OPERATION = "web.server_order.create"
_KEY = re.compile(r"^[A-Za-z0-9_-]{20,100}$")


def _normalize(key):
    key = str(key or "").strip()
    if not _KEY.fullmatch(key):
        raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
    return key.encode("ascii")


def _digest(payload):
    normalized = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                            separators=(",", ":"), default=str)
    return sha256(normalized.encode("utf-8")).digest()


def _find(bar_id, actor_id, key_bytes, *, lock=True):
    stmt = select(IdempotencyRecord).where(
        IdempotencyRecord.bar_id == bar_id,
        IdempotencyRecord.actor_id == actor_id,
        IdempotencyRecord.operation == OPERATION,
        IdempotencyRecord.idempotency_key == key_bytes,
    )
    return db.session.scalar(stmt.with_for_update() if lock else stmt)


def _replay(record, digest, bar_id, actor_id):
    if record.request_hash != digest:
        raise ValueError("IDEMPOTENCY_CONFLICT")
    if record.status != "COMPLETED" or not isinstance(record.response_body, dict):
        raise ValueError("IDEMPOTENCY_IN_PROGRESS")
    order_id = record.response_body.get("order_id")
    order = db.session.scalar(
        select(Order).where(Order.id == order_id, Order.bar_id == bar_id, Order.created_by_id == actor_id)
    )
    if order is None:
        raise ValueError("IDEMPOTENCY_RESULT_MISSING")
    return order, True


def create_once(actor, bar_id, key, payload, create_order):
    """Return (Order, replayed).

    INSERT the unique intent first, then call existing order_service.create.
    Racing database connections block on the unique key; only one can insert
    and commit. After IntegrityError the losing request reads completed record.
    """
    key_bytes = _normalize(key)
    digest = _digest(payload)
    existing = _find(bar_id, actor.id, key_bytes)
    if existing is not None:
        return _replay(existing, digest, bar_id, actor.id)

    record = IdempotencyRecord(
        bar_id=bar_id, actor_id=actor.id, operation=OPERATION,
        idempotency_key=key_bytes, request_hash=digest, status="PROCESSING",
    )
    db.session.add(record)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        existing = _find(bar_id, actor.id, key_bytes)
        if existing is None:
            raise ValueError("IDEMPOTENCY_RETRY_LATER")
        return _replay(existing, digest, bar_id, actor.id)

    order = create_order()
    record.response_status = 201
    record.response_body = {"order_id": order.id, "reference": order.reference}
    record.status = "COMPLETED"
    record.completed_at = utcnow()
    db.session.flush()
    return order, False


def lookup(actor, bar_id, key):
    """Recover after network uncertainty without ever creating a new order."""
    record = _find(bar_id, actor.id, _normalize(key), lock=False)
    if record is None:
        return None
    if record.status != "COMPLETED":
        return {"state": "PROCESSING"}
    order = db.session.scalar(select(Order).where(
        Order.id == (record.response_body or {}).get("order_id"),
        Order.bar_id == bar_id, Order.created_by_id == actor.id,
    ))
    if order is None:
        return None
    return {"state": "COMPLETED", "id": order.id, "reference": order.reference,
            "invoice_name": order.customer_name_snapshot or order.table_label_snapshot or "COMPTOIR"}
