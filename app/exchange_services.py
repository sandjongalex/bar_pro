"""Invoice-independent exchanges. The caller commits or rolls back the transaction."""
from sqlalchemy import select

from app.audit import record
from app.cash_services import cash_service
from app.extensions import db
from app.models import Bar, BeverageExchange, CashSession, Product, StaffAssignment, utcnow
from app.permissions import permissions
from app.stock_service import stock_service
from app.validation import number, required_text


class ExchangeService:
    def request(self, actor, bar_id, reference, staff_id, returned_id, replacement_id,
                returned_quantity, replacement_quantity, reason):
        permissions.require(actor, "exchanges.request", bar_id)
        reference = required_text(reference, 64)
        reason = required_text(reason)
        rq = number(returned_quantity, 6, positive=True)
        nq = number(replacement_quantity, 6, positive=True)
        if rq != rq.to_integral_value() or nq != nq.to_integral_value():
            raise ValueError("WHOLE_BOTTLES_REQUIRED")
        staff = db.session.scalar(select(StaffAssignment).where(
            StaffAssignment.bar_id == bar_id, StaffAssignment.id == staff_id,
            StaffAssignment.role == "SERVER", StaffAssignment.ended_at.is_(None)))
        if not staff or not staff.user.is_active:
            raise LookupError("NOT_FOUND")
        if not permissions.evaluate(actor, "exchanges.post", bar_id).allowed and staff.user_id != actor.id:
            raise PermissionError("FORBIDDEN")
        previous = db.session.scalar(select(BeverageExchange).where(
            BeverageExchange.bar_id == bar_id, BeverageExchange.reference == reference))
        if previous:
            if (previous.created_by_id, previous.staff_assignment_id, previous.returned_product_id,
                previous.replacement_product_id, previous.returned_quantity, previous.replacement_quantity,
                previous.reason) != (actor.id, staff.id, returned_id, replacement_id, rq, nq, reason):
                raise ValueError("REFERENCE_REUSED")
            return previous
        if returned_id == replacement_id:
            raise ValueError("DIFFERENT_PRODUCTS_REQUIRED")
        products = {p.id: p for p in db.session.scalars(select(Product).where(
            Product.bar_id == bar_id, Product.id.in_([returned_id, replacement_id]),
            Product.is_active.is_(True)).order_by(Product.id).with_for_update())}
        if len(products) != 2:
            raise LookupError("NOT_FOUND")
        old, new = products[returned_id], products[replacement_id]
        difference = number(nq * new.sale_price - rq * old.sale_price)
        if difference < 0:
            raise ValueError("CHEAPER_EXCHANGE_NOT_SUPPORTED")
        item = BeverageExchange(
            bar_id=bar_id, reference=reference, staff_assignment_id=staff.id,
            returned_product_id=old.id, replacement_product_id=new.id,
            returned_name=old.name, replacement_name=new.name,
            returned_quantity=rq, replacement_quantity=nq,
            returned_price=old.sale_price, replacement_price=new.sale_price,
            supplement=difference, currency=db.session.get(Bar, bar_id).currency,
            reason=reason, created_by_id=actor.id, status="PENDING")
        db.session.add(item)
        db.session.flush()
        record(actor, bar_id, "exchanges.request", "beverage_exchanges", item.id, reason)
        return item

    def _get(self, bar_id, exchange_id):
        item = db.session.scalar(select(BeverageExchange).where(
            BeverageExchange.bar_id == bar_id, BeverageExchange.id == exchange_id
        ).execution_options(populate_existing=True).with_for_update())
        if not item:
            raise LookupError("NOT_FOUND")
        return item

    def post(self, actor, bar_id, exchange_id, amount_received, bottles_checked=False):
        permissions.require(actor, "exchanges.post", bar_id)
        item = self._get(bar_id, exchange_id)
        if item.status == "POSTED":
            return item  # A retried approval must never move cash or stock twice.
        if item.status != "PENDING":
            raise ValueError("EXCHANGE_NOT_PENDING")
        if not bottles_checked:
            raise ValueError("BOTTLES_CHECK_REQUIRED")
        if number(amount_received) != item.supplement:
            raise ValueError("SUPPLEMENT_REQUIRED")
        products = list(db.session.scalars(select(Product).where(
            Product.bar_id == bar_id,
            Product.id.in_([item.returned_product_id, item.replacement_product_id]),
            Product.is_active.is_(True)).order_by(Product.id).with_for_update()))
        if len(products) != 2:
            raise LookupError("NOT_FOUND")
        session = None
        if item.supplement > 0:
            session = db.session.scalar(select(CashSession).where(
                CashSession.bar_id == bar_id, CashSession.status == "OPEN").with_for_update())
            if not session:
                raise ValueError("CASH_SESSION_NOT_OPEN")
        reason = f"Échange {item.reference}"
        incoming = stock_service.move(actor, bar_id, item.returned_product_id, "RETURN",
                                      item.returned_quantity, reason)
        outgoing = stock_service.move(actor, bar_id, item.replacement_product_id, "SALE",
                                      -item.replacement_quantity, reason)
        cash = None
        if session:
            cash = cash_service.entry(actor, bar_id, item.supplement, item.currency,
                                      f"Supplément {reason}", session_id=session.id)
        db.session.flush()
        item.return_movement_id = incoming.id
        item.replacement_movement_id = outgoing.id
        item.cash_movement_id = cash.id if cash else None
        item.status = "POSTED"
        item.decided_by_id = actor.id
        item.decided_at = utcnow()
        db.session.flush()
        record(actor, bar_id, "exchanges.post", "beverage_exchanges", item.id, reason)
        return item

    def cancel(self, actor, bar_id, exchange_id):
        permissions.require(actor, "exchanges.request", bar_id)
        item = self._get(bar_id, exchange_id)
        if not permissions.evaluate(actor, "exchanges.post", bar_id).allowed and item.created_by_id != actor.id:
            raise PermissionError("FORBIDDEN")
        if item.status != "PENDING":
            raise ValueError("EXCHANGE_NOT_PENDING")
        item.status = "CANCELLED"
        item.decided_by_id = actor.id
        item.decided_at = utcnow()
        record(actor, bar_id, "exchanges.cancel", "beverage_exchanges", item.id, item.reference)
        return item


exchange_service = ExchangeService()
