"""Subscription lifecycle; platform billing is deliberately manual."""
from datetime import timedelta
from decimal import Decimal
from sqlalchemy import select
from app.extensions import db
from app.models import Bar, Plan, Subscription, SubscriptionPayment, utcnow
from app.permissions import permissions
from app.validation import number
from app.audit import record

class SubscriptionService:
 def create(self,actor,bar_id,plan_id,reference):
  permissions.require(actor,"subscriptions.manage",bar_id)
  bar=db.session.get(Bar,bar_id); plan=db.session.get(Plan,plan_id)
  if not bar or not plan or not plan.is_active: raise LookupError("NOT_FOUND")
  start=utcnow(); item=Subscription(bar_id=bar_id,plan_id=plan.id,reference=reference,status="PENDING",plan_code_snapshot=plan.code,plan_name_snapshot=plan.name,price_amount_snapshot=plan.price_amount,duration_days_snapshot=plan.duration_days,currency=plan.currency,starts_at=start,ends_at=start+timedelta(days=plan.duration_days),created_by_id=actor.id)
  db.session.add(item);db.session.flush();record(actor,bar_id,"subscriptions.create","subscriptions",item.id,reference);return item
 def pay(self,actor,bar_id,subscription_id,reference,amount):
  permissions.require(actor,"subscriptions.manage",bar_id); item=db.session.scalar(select(Subscription).where(Subscription.id==subscription_id,Subscription.bar_id==bar_id).with_for_update())
  if not item or item.status not in {"PENDING","ACTIVE"}: raise ValueError("SUBSCRIPTION_NOT_PAYABLE")
  amount=number(amount,4,positive=True); paid=sum((x.amount if x.entry_kind=="PAYMENT" else -x.amount for x in db.session.scalars(select(SubscriptionPayment).where(SubscriptionPayment.bar_id==bar_id,SubscriptionPayment.subscription_id==item.id))),Decimal(0))
  if paid+amount>item.price_amount_snapshot: raise ValueError("AMOUNT_EXCEEDED")
  payment=SubscriptionPayment(bar_id=bar_id,subscription_id=item.id,reference=reference,amount=amount,currency=item.currency,entry_kind="PAYMENT",provider_code="MANUAL",provider_transaction_id=reference,paid_at=utcnow(),recorded_by_id=actor.id)
  db.session.add(payment);db.session.flush()
  if paid+amount==item.price_amount_snapshot:
   item.status="ACTIVE";item.activated_at=item.activated_at or utcnow();record(actor,bar_id,"subscriptions.activate","subscriptions",item.id,reference)
  record(actor,bar_id,"subscriptions.payment","subscription_payments",payment.id,reference);return item
 def cancel(self,actor,bar_id,subscription_id,reason):
  permissions.require(actor,"subscriptions.manage",bar_id); item=db.session.scalar(select(Subscription).where(Subscription.id==subscription_id,Subscription.bar_id==bar_id).with_for_update())
  if not item or item.status in {"CANCELLED","EXPIRED"}: raise ValueError("SUBSCRIPTION_NOT_CANCELLABLE")
  item.status="CANCELLED";item.cancelled_at=utcnow();record(actor,bar_id,"subscriptions.cancel","subscriptions",item.id,reason);return item

subscription_service=SubscriptionService()
