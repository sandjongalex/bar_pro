"""Persistent, transaction-scoped cashier payment deduplication.

The existing idempotency_records table is reused; no schema change is needed.
Claim and receipt are committed together with the existing payment/credit journals.
A replay or a changed payload with the same key is a 409, never a second charge.
"""
from __future__ import annotations

from hashlib import sha256
import json
import secrets

from sqlalchemy import select

from app.extensions import db
from app.models import IdempotencyRecord, utcnow


def claim(actor, bar_id, action, key, fields, *, require_key=False):
    """Reserve a payment intent before any financial mutation.

    A unique DB constraint serializes identical intents across connections.
    The caller MUST commit only after complete() and roll back on every error.
    """
    key = str(key or "").strip()
    if not key:
        if require_key:
            raise ValueError("IDEMPOTENCY_KEY_REQUIRED")
        # Compatibility for older HTML integrations that do not send a token.
        key = secrets.token_urlsafe(32)
    if len(key) > 128:
        raise ValueError("IDEMPOTENCY_KEY_REQUIRED")

    operation = "web.cashier." + action
    key_bytes = sha256(key.encode("utf-8")).digest()
    canonical = {name: list(values) for name, values in fields.lists()
                 if name not in {"csrf_token", "idempotency_key"}}
    digest = sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":")).encode("utf-8")).digest()
    existing = db.session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.bar_id == bar_id,
            IdempotencyRecord.actor_id == actor.id,
            IdempotencyRecord.operation == operation,
            IdempotencyRecord.idempotency_key == key_bytes,
        ).with_for_update()
    )
    if existing is not None:
        raise ValueError("IDEMPOTENCY_CONFLICT" if existing.request_hash != digest
                         else "PAYMENT_ALREADY_PROCESSED")

    receipt = IdempotencyRecord(
        bar_id=bar_id, actor_id=actor.id, operation=operation,
        idempotency_key=key_bytes, request_hash=digest, status="PROCESSING",
    )
    db.session.add(receipt)
    # The unique constraint also guards races on engines without row-level locks.
    db.session.flush()
    return receipt


def complete(receipt, *, order_id):
    """Persist success in the same transaction as the payment and audit."""
    receipt.status = "COMPLETED"
    receipt.response_status = 200
    receipt.response_body = {"order_id": order_id}
    receipt.completed_at = utcnow()
    db.session.flush()
