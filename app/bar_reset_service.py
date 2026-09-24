"""Owner-only reset of one bar's operational data.

The reset keeps the tenant identity and reusable configuration/master data,
while removing operational journals and setting stock balances back to zero.
It is intentionally bar-scoped and never issues an unfiltered delete.
"""
from __future__ import annotations

from collections import defaultdict

from app.audit import record
from app.extensions import db
from app.models import Bar, StockBalance
from app.permissions import permissions


CONFIRMATION_TEXT = "REINITIALISER"

# These resources define the bar or reusable configuration and therefore survive
# a reset. Billing history is also preserved because it belongs to the SaaS
# subscription, not to the bar's daily operating history.
PRESERVED_BAR_TABLES = frozenset(
    {
        "staff_assignments",
        "product_categories",
        "products",
        "stock_balances",
        "bar_tables",
        "suppliers",
        "customers",
        "expense_categories",
        "subscriptions",
        "subscription_payments",
        "api_tokens",
        "token_revocations",
    }
)


def _clear_nullable_self_references(table, bar_id: int) -> None:
    """Break nullable self-FKs before a bulk tenant delete.

    Reversal journals and token rotations use self-references with RESTRICT.
    Clearing only nullable self references keeps the delete portable across
    SQLite/MySQL without disabling foreign-key checks globally.
    """
    columns = set()
    for foreign_key in table.foreign_keys:
        local = foreign_key.parent
        remote = foreign_key.column
        if local.table is table and remote.table is table and local.nullable:
            columns.add(local.name)

    if not columns:
        return

    db.session.execute(
        table.update()
        .where(table.c.bar_id == bar_id)
        .values({column: None for column in columns})
    )


def reset_bar(actor, bar_id: int, confirmation: str, password: str) -> dict:
    """Erase one owner's operational history and reset its stock to zero.

    The caller owns the transaction: this function never commits.  Any failure
    therefore leaves the bar unchanged once the caller rolls back.
    """
    permissions.require(actor, "bars.reset", bar_id)

    bar = db.session.get(Bar, bar_id)
    if not bar or actor.category != "OWNER" or bar.owner_id != actor.id:
        raise PermissionError("FORBIDDEN")

    if str(confirmation or "").strip().upper() != CONFIRMATION_TEXT:
        raise ValueError("RESET_CONFIRMATION_REQUIRED")
    if not password or not actor.check_password(password):
        raise ValueError("INVALID_PASSWORD")

    deleted = defaultdict(int)

    # SQLAlchemy orders metadata from parents to children. Reversing it lets us
    # remove dependent tenant rows before their parents while respecting FKs.
    for table in reversed(db.metadata.sorted_tables):
        if "bar_id" not in table.c or table.name in PRESERVED_BAR_TABLES:
            continue
        _clear_nullable_self_references(table, bar_id)
        result = db.session.execute(table.delete().where(table.c.bar_id == bar_id))
        if result.rowcount and result.rowcount > 0:
            deleted[table.name] += result.rowcount

    # Products and stock-balance rows survive so the owner keeps the catalogue,
    # but every quantity restarts from zero. Incrementing the version invalidates
    # stale inventory snapshots if any external client still holds one.
    result = db.session.execute(
        StockBalance.__table__.update()
        .where(StockBalance.bar_id == bar_id)
        .values(
            quantity=0,
            version=StockBalance.__table__.c.version + 1,
        )
    )
    balances_reset = max(result.rowcount or 0, 0)

    # Old audit rows were removed with the operational history. Keep one explicit
    # immutable trace indicating when the new clean period began.
    record(
        actor,
        bar_id,
        "bars.reset",
        "bars",
        bar_id,
        "Réinitialisation complète des données opérationnelles du bar",
    )
    db.session.flush()

    return {
        "bar_id": bar_id,
        "deleted_rows": sum(deleted.values()),
        "deleted_by_table": dict(deleted),
        "stock_balances_reset": balances_reset,
    }
