"""Bearer-only finance endpoints."""
from flask import Blueprint,jsonify,request
from app.auth import api_required
from app.extensions import db
from app.payment_services import payment_service
from app.cash_services import cash_service
bp=Blueprint("finance",__name__,url_prefix="/api/v1/bars/<int:bar_id>")
@bp.post("/payments")
@api_required
def payment(bar_id):
 data=request.get_json()
 item=payment_service.record(request.api_user,bar_id,data["order_id"],data["reference"],data["method"],data["amount_presented"],data["amount_applied"],data.get("change_given",0),data.get("cash_session_id"),data.get("staff_assignment_id"),data.get("provider_code"),data.get("provider_transaction_id"))
 db.session.commit()
 return jsonify(success=True,data={"id":str(item.id),"amount_applied":str(item.amount_applied)},meta={}),201
@bp.post("/cash-sessions")
@api_required
def open_session(bar_id):
 data=request.get_json();item=cash_service.open(request.api_user,bar_id,data["reference"],data["opening_amount"]);db.session.commit()
 return jsonify(success=True,data={"id":str(item.id),"status":item.status},meta={}),201
@bp.post("/cash-sessions/<int:session_id>/close")
@api_required
def close_session(bar_id,session_id):
 data=request.get_json();item=cash_service.close(request.api_user,bar_id,session_id,data["counted_closing_amount"],data.get("reason",""));db.session.commit()
 return jsonify(success=True,data={"id":str(item.id),"status":item.status,"expected_closing_amount":str(item.expected_closing_amount),"closing_difference":str(item.closing_difference)},meta={})

from sqlalchemy import select
from app.models import Order, Payment, Refund, CashSession, CashMovement, CashHandover, StaffCashLedger, OrderReturn
from app.permissions import permissions
from app.finance_totals import order_balance


def serialize(item):
 return {column.name: (str(getattr(item,column.name)) if getattr(item,column.name) is not None else None) for column in item.__table__.columns}


def result(item, status=200):
 db.session.commit()
 return jsonify(success=True,data=serialize(item),meta={}),status


@bp.get('/orders/<int:order_id>/balance')
@api_required
def balance(bar_id,order_id):
 permissions.require(request.api_user,'payments.read',bar_id)
 item=db.session.scalar(select(Order).where(Order.bar_id==bar_id,Order.id==order_id))
 if not item: raise LookupError('NOT_FOUND')
 return jsonify(success=True,data={key:str(value) for key,value in order_balance(item).items()},meta={})


@bp.post('/refunds')
@api_required
def refund(bar_id):
 data=request.get_json()
 return result(payment_service.refund(request.api_user,bar_id,data['payment_id'],data['reference'],data['amount'],data['reason'],data.get('order_return_id'),data.get('cash_session_id'),data.get('staff_assignment_id')),201)


@bp.post('/orders/<int:order_id>/cancel-with-refunds')
@api_required
def cancel_paid(bar_id,order_id):
 data=request.get_json()
 return result(payment_service.cancel_paid(request.api_user,bar_id,order_id,data['reference'],data['reason'],data.get('cash_session_id'),data.get('staff_assignment_id')))


@bp.post('/cash-sessions/<int:session_id>/movements')
@api_required
def movement(bar_id,session_id):
 data=request.get_json()
 return result(cash_service.movement(request.api_user,bar_id,session_id,data['kind'],data['amount'],data['reason']),201)


@bp.post('/cash-movements/<int:movement_id>/reverse')
@api_required
def reverse(bar_id,movement_id):
 return result(cash_service.reverse(request.api_user,bar_id,movement_id,request.get_json()['reason']),201)


@bp.post('/cash-handovers')
@api_required
def handover(bar_id):
 data=request.get_json()
 return result(cash_service.handover(request.api_user,bar_id,data['staff_assignment_id'],data['cash_session_id'],data['reference'],data['amount']),201)


@bp.post('/cash-handovers/<int:handover_id>/post')
@api_required
def post_handover(bar_id,handover_id):
 return result(cash_service.transition_handover(request.api_user,bar_id,handover_id))


@bp.post('/cash-handovers/<int:handover_id>/cancel')
@api_required
def cancel_handover(bar_id,handover_id):
 return result(cash_service.transition_handover(request.api_user,bar_id,handover_id,cancel=True))


@bp.get('/cash-sessions/<int:session_id>')
@api_required
def session_detail(bar_id,session_id):
 permissions.require(request.api_user,'cash.read',bar_id)
 item=db.session.scalar(select(CashSession).where(CashSession.bar_id==bar_id,CashSession.id==session_id))
 if not item: raise LookupError('NOT_FOUND')
 data=serialize(item);data['expected_amount']=str(cash_service.expected(item))
 return jsonify(success=True,data=data,meta={})


@bp.get('/staff-cash/<int:staff_id>')
@api_required
def staff_balance(bar_id,staff_id):
 permissions.require(request.api_user,'cash.read',bar_id)
 if not db.session.scalar(select(StaffCashLedger.id).where(StaffCashLedger.bar_id==bar_id,StaffCashLedger.staff_assignment_id==staff_id).limit(1)):
  from app.models import StaffAssignment
  if not db.session.scalar(select(StaffAssignment.id).where(StaffAssignment.bar_id==bar_id,StaffAssignment.id==staff_id)): raise LookupError('NOT_FOUND')
 return jsonify(success=True,data={'balance':str(cash_service.custody(bar_id,staff_id))},meta={})


def listing(model,permission):
 @api_required
 def view(bar_id):
  permissions.require(request.api_user,permission,bar_id)
  page=max(1,int(request.args.get('page',1)));size=min(100,max(1,int(request.args.get('page_size',50))))
  query=select(model).where(model.bar_id==bar_id)
  for key in ('order_id','cash_session_id','staff_assignment_id'):
   if key in request.args and hasattr(model,key): query=query.where(getattr(model,key)==int(request.args[key]))
  items=db.session.scalars(query.order_by(model.id.desc()).offset((page-1)*size).limit(size+1)).all()
  return jsonify(success=True,data=[serialize(item) for item in items[:size]],meta={'page':page,'page_size':size,'has_more':len(items)>size})
 return view


for path,model,permission in [('payments',Payment,'payments.read'),('refunds',Refund,'payments.read'),('returns',OrderReturn,'orders.read'),('cash-sessions',CashSession,'cash.read'),('cash-movements',CashMovement,'cash.read'),('cash-handovers',CashHandover,'cash.read'),('staff-cash',StaffCashLedger,'cash.read')]:
 bp.add_url_rule('/'+path,endpoint='list_'+path,view_func=listing(model,permission),methods=['GET'])
