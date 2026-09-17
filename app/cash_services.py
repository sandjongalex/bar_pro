"""Cash custody and drawer operations, committed by the caller."""
from sqlalchemy import select, func
from app.extensions import db
from app.models import Bar, CashMovement, CashSession, StaffAssignment, StaffCashLedger, CashHandover, utcnow
from app.validation import number, required_text
from app.audit import record
from app.permissions import permissions


class CashService:
    def session(self, bar_id, session_id):
        item = db.session.scalar(select(CashSession).where(CashSession.id == session_id, CashSession.bar_id == bar_id).with_for_update())
        if not item or item.status != "OPEN":
            raise ValueError("CASH_SESSION_NOT_OPEN")
        return item

    def staff(self, bar_id, staff_id):
        item = db.session.scalar(select(StaffAssignment).where(StaffAssignment.id == staff_id, StaffAssignment.bar_id == bar_id).with_for_update())
        if not item:
            raise LookupError("NOT_FOUND")
        return item

    def expected(self, item):
        return item.opening_amount + db.session.scalar(select(func.coalesce(func.sum(CashMovement.amount_delta), 0)).where(CashMovement.bar_id == item.bar_id, CashMovement.cash_session_id == item.id))

    def custody(self, bar_id, staff_id):
        return db.session.scalar(select(func.coalesce(func.sum(StaffCashLedger.amount_delta), 0)).where(StaffCashLedger.bar_id == bar_id, StaffCashLedger.staff_assignment_id == staff_id))

    def entry(self, actor, bar_id, amount, currency, reason, session_id=None, staff_id=None, **source):
        if (session_id is None) == (staff_id is None):
            raise ValueError("CASH_LOCATION_REQUIRED")
        if staff_id is not None:
            self.staff(bar_id, staff_id)
            if self.custody(bar_id, staff_id) + amount < 0:
                raise ValueError("INSUFFICIENT_STAFF_CASH")
            item = StaffCashLedger(staff_assignment_id=staff_id)
        else:
            session = self.session(bar_id, session_id)
            if session.currency != currency:
                raise ValueError("CURRENCY_MISMATCH")
            if self.expected(session) + amount < 0:
                raise ValueError("INSUFFICIENT_DRAWER_CASH")
            item = CashMovement(cash_session_id=session_id)
        for key, value in source.items():
            setattr(item, key, value)
        item.bar_id=bar_id; item.amount_delta=amount; item.currency=currency
        item.reason=required_text(reason); item.occurred_at=utcnow(); item.recorded_by_id=actor.id
        db.session.add(item)
        return item

    def open(self, actor, bar_id, reference, opening_amount):
        permissions.require(actor, "cash.operate", bar_id)
        bar = db.session.get(Bar, bar_id)
        if db.session.scalar(select(CashSession).where(CashSession.bar_id == bar_id, CashSession.status == "OPEN")):
            raise ValueError("CASH_SESSION_ALREADY_OPEN")
        amount=number(opening_amount)
        if amount < 0: raise ValueError("INVALID_OPENING_AMOUNT")
        item=CashSession(bar_id=bar_id, reference=required_text(reference,64), status="OPEN", opened_by_id=actor.id, opened_at=utcnow(), currency=bar.currency, opening_amount=amount)
        db.session.add(item); db.session.flush()
        record(actor,bar_id,"cash.open","cash_sessions",item.id,"Ouverture")
        return item

    def close(self, actor, bar_id, session_id, counted, reason=""):
        permissions.require(actor,"cash.operate",bar_id)
        item=self.session(bar_id,session_id); expected=self.expected(item); counted=number(counted)
        if counted < 0: raise ValueError("INVALID_COUNTED_AMOUNT")
        if counted != expected: reason=required_text(reason)
        item.expected_closing_amount=expected; item.counted_closing_amount=counted
        item.status="CLOSED"; item.closed_at=utcnow(); item.closed_by_id=actor.id
        record(actor,bar_id,"cash.close","cash_sessions",item.id,reason or "Clôture sans écart")
        return item

    def movement(self, actor, bar_id, session_id, kind, amount, reason):
        permissions.require(actor,"cash.operate",bar_id)
        if kind not in {"DEPOSIT","WITHDRAWAL"}: raise ValueError("INVALID_MOVEMENT_KIND")
        amount=number(amount,positive=True); session=self.session(bar_id,session_id)
        item=self.entry(actor,bar_id,amount if kind=="DEPOSIT" else -amount,session.currency,reason,session_id=session_id,manual_kind=kind)
        db.session.flush(); record(actor,bar_id,"cash.movement","cash_movements",item.id,item.reason)
        return item

    def reverse(self, actor, bar_id, movement_id, reason):
        permissions.require(actor,"cash.operate",bar_id)
        source=db.session.scalar(select(CashMovement).where(CashMovement.bar_id==bar_id,CashMovement.id==movement_id).with_for_update())
        if not source: raise LookupError("NOT_FOUND")
        if source.manual_kind not in {"DEPOSIT","WITHDRAWAL"} or source.reversal_of_id is not None:
            raise ValueError("ONLY_MANUAL_MOVEMENTS_REVERSIBLE")
        if db.session.scalar(select(CashMovement.id).where(CashMovement.bar_id==bar_id,CashMovement.reversal_of_id==source.id)):
            raise ValueError("ALREADY_REVERSED")
        item=self.entry(actor,bar_id,-source.amount_delta,source.currency,reason,session_id=source.cash_session_id,reversal_of_id=source.id)
        db.session.flush(); record(actor,bar_id,"cash.reverse","cash_movements",item.id,item.reason)
        return item

    def handover(self, actor, bar_id, staff_id, session_id, reference, amount):
        permissions.require(actor,"cash.operate",bar_id)
        self.staff(bar_id,staff_id); session=self.session(bar_id,session_id)
        amount=number(amount,positive=True)
        if amount>self.custody(bar_id,staff_id): raise ValueError("INSUFFICIENT_STAFF_CASH")
        item=CashHandover(bar_id=bar_id,staff_assignment_id=staff_id,cash_session_id=session_id,reference=required_text(reference,64),amount=amount,currency=session.currency,status="DRAFT",requested_by_id=actor.id)
        db.session.add(item); db.session.flush()
        record(actor,bar_id,"cash.handover.create","cash_handovers",item.id,item.reference)
        return item

    def transition_handover(self, actor, bar_id, handover_id, cancel=False):
        permissions.require(actor,"cash.operate",bar_id)
        item=db.session.scalar(select(CashHandover).where(CashHandover.bar_id==bar_id,CashHandover.id==handover_id).with_for_update())
        if not item or item.status!="DRAFT": raise ValueError("HANDOVER_NOT_DRAFT")
        if cancel:
            item.status="CANCELLED"; item.cancelled_at=utcnow()
        else:
            self.session(bar_id,item.cash_session_id)
            self.entry(actor,bar_id,-item.amount,item.currency,item.reference,staff_id=item.staff_assignment_id,cash_handover_id=item.id)
            self.entry(actor,bar_id,item.amount,item.currency,item.reference,session_id=item.cash_session_id,cash_handover_id=item.id)
            item.status="POSTED"; item.received_by_id=actor.id; item.posted_at=utcnow()
        record(actor,bar_id,"cash.handover."+item.status.lower(),"cash_handovers",item.id,item.reference)
        return item

cash_service=CashService()
