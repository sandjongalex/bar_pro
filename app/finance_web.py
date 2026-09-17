"""Server-rendered financial desk; all writes retain CSRF protection."""
from flask import Blueprint, request, render_template, redirect, url_for, flash
from flask_login import current_user, login_required
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from app.extensions import db
from app.models import Bar, Order, Payment, Refund, CashSession, CashMovement, StaffAssignment, CashHandover, OrderReturn
from app.permissions import permissions
from app.cash_services import cash_service
from app.payment_services import payment_service
from app.finance_totals import order_balance

bp=Blueprint('finance_web',__name__,url_prefix='/bars/<int:bar_id>/finance')


def optional_id(data,key):
 return int(data[key]) if data.get(key) else None


@bp.route('',methods=['GET','POST'])
@login_required
def desk(bar_id):
 permissions.require(current_user,'payments.read',bar_id)
 if request.method=='POST':
  data=request.form
  try:
   action=data['action'];session_id=optional_id(data,'cash_session_id');staff_id=optional_id(data,'staff_assignment_id')
   if action=='open': cash_service.open(current_user,bar_id,data['reference'],data['opening_amount'])
   elif action=='close': cash_service.close(current_user,bar_id,session_id,data['counted_closing_amount'],data.get('reason',''))
   elif action=='movement': cash_service.movement(current_user,bar_id,session_id,data['kind'],data['amount'],data['reason'])
   elif action=='reverse': cash_service.reverse(current_user,bar_id,int(data['movement_id']),data['reason'])
   elif action=='payment': payment_service.record(current_user,bar_id,int(data['order_id']),data['reference'],data['method'],data['amount_presented'],data['amount_applied'],data.get('change_given',0),session_id,staff_id)
   elif action=='refund': payment_service.refund(current_user,bar_id,int(data['payment_id']),data['reference'],data['amount'],data['reason'],optional_id(data,'order_return_id'),session_id,staff_id)
   elif action=='cancel_order': payment_service.cancel_paid(current_user,bar_id,int(data['order_id']),data['reference'],data['reason'],session_id,staff_id)
   elif action=='handover': cash_service.handover(current_user,bar_id,staff_id,session_id,data['reference'],data['amount'])
   elif action in {'post_handover','cancel_handover'}: cash_service.transition_handover(current_user,bar_id,int(data['handover_id']),cancel=action=='cancel_handover')
   else: raise ValueError('INVALID_ACTION')
   db.session.commit();flash('Opération enregistrée.','success')
  except (ValueError,LookupError,IntegrityError,TypeError):
   db.session.rollback();flash('Opération refusée. Vérifiez les montants, le solde disponible et les références.','danger')
  return redirect(url_for('finance_web.desk',bar_id=bar_id))
 bar=db.session.get(Bar,bar_id)
 orders=db.session.scalars(select(Order).where(Order.bar_id==bar_id,Order.status.in_(['CONFIRMED','SERVED'])).order_by(Order.id.desc()).limit(100)).all()
 sessions=db.session.scalars(select(CashSession).where(CashSession.bar_id==bar_id).order_by(CashSession.id.desc()).limit(30)).all()
 payments=db.session.scalars(select(Payment).where(Payment.bar_id==bar_id).order_by(Payment.id.desc()).limit(100)).all()
 refunds=db.session.scalars(select(Refund).where(Refund.bar_id==bar_id).order_by(Refund.id.desc()).limit(100)).all()
 movements=db.session.scalars(select(CashMovement).where(CashMovement.bar_id==bar_id).order_by(CashMovement.id.desc()).limit(100)).all()
 staff=db.session.scalars(select(StaffAssignment).where(StaffAssignment.bar_id==bar_id)).all()
 handovers=db.session.scalars(select(CashHandover).where(CashHandover.bar_id==bar_id).order_by(CashHandover.id.desc()).limit(100)).all()
 returns=db.session.scalars(select(OrderReturn).where(OrderReturn.bar_id==bar_id,OrderReturn.status=='POSTED').order_by(OrderReturn.id.desc()).limit(100)).all()
 return render_template('finance.html',bar=bar,orders=orders,balances={o.id:order_balance(o) for o in orders},sessions=sessions,payments=payments,refunds=refunds,movements=movements,staff=staff,handovers=handovers,returns=returns,cash=cash_service)
