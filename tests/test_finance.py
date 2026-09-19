"""Financial regression scenarios on migrated SQLite databases."""
import pytest
from sqlalchemy import select
from test_workflows import env, order, balance
from app.extensions import db
from app.models import Payment, Refund, OrderLine, CashMovement, StaffAssignment, CashHandover
from app.payment_services import payment_service
from app.cash_services import cash_service
from app.order_services import order_service
from app.finance_totals import order_balance


def test_return_refund_and_repayment(env):
 value=order(env,served=True)
 p=payment_service.record(env[1],env[2].id,value.id,'P','CARD',200,200);db.session.commit()
 line=db.session.scalar(select(OrderLine).where(OrderLine.order_id==value.id))
 ret=order_service.return_lines(env[1],env[2].id,value.id,[dict(order_line_id=line.id,quantity=1,disposition='RESTOCK')],'Retour');db.session.commit()
 assert order_balance(value)['refundable_overpayment']==100
 payment_service.refund(env[1],env[2].id,p.id,'R',100,'Retour',ret.id);db.session.commit()
 assert order_balance(value)['amount_due']==0
 with pytest.raises(ValueError):payment_service.refund(env[1],env[2].id,p.id,'R2',1,'Trop',ret.id)
 db.session.rollback()
 payment_service.refund(env[1],env[2].id,p.id,'CORRECTION',50,'Correction encaissement');db.session.commit()
 assert value.payment_status=='PARTIAL'
 payment_service.record(env[1],env[2].id,value.id,'P2','CARD',50,50);db.session.commit()
 assert value.payment_status=='PAID'


def test_paid_cancellation_atomic(env):
 value=order(env)
 session=cash_service.open(env[1],env[2].id,'C',0);db.session.flush()
 payment_service.record(env[1],env[2].id,value.id,'P','CASH',200,200,cash_session_id=session.id);db.session.commit()
 payment_service.cancel_paid(env[1],env[2].id,value.id,'CANCEL','Erreur',session.id);db.session.commit()
 assert value.status=='CANCELLED';assert balance(env)==10
 assert cash_service.expected(session)==0
 assert db.session.query(Refund).count()==1
 with pytest.raises(ValueError):payment_service.cancel_paid(env[1],env[2].id,value.id,'AGAIN','Erreur',session.id)


def test_insufficient_cash_rolls_back_cancellation(env):
 value=order(env);session=cash_service.open(env[1],env[2].id,'C',0);db.session.flush()
 payment_service.record(env[1],env[2].id,value.id,'P','CASH',200,200,cash_session_id=session.id)
 cash_service.movement(env[1],env[2].id,session.id,'WITHDRAWAL',150,'Banque');db.session.commit()
 with pytest.raises(ValueError):payment_service.cancel_paid(env[1],env[2].id,value.id,'CANCEL','Erreur',session.id)
 db.session.rollback()
 assert db.session.query(Refund).count()==0;assert value.status=='CONFIRMED';assert balance(env)==8
 assert cash_service.expected(session)==50


def test_staff_handover_and_refund(env):
 value=order(env)
 staff=db.session.scalar(select(StaffAssignment))
 p=payment_service.record(env[1],env[2].id,value.id,'P','CASH',250,200,50,staff_assignment_id=staff.id);db.session.commit()
 assert cash_service.custody(env[2].id,staff.id)==200
 session=cash_service.open(env[1],env[2].id,'C',10);db.session.flush()
 handover=cash_service.handover(env[1],env[2].id,staff.id,session.id,'H',150);db.session.commit()
 assert cash_service.expected(session)==10
 cash_service.transition_handover(env[1],env[2].id,handover.id);db.session.commit()
 assert cash_service.expected(session)==160;assert cash_service.custody(env[2].id,staff.id)==50
 with pytest.raises(ValueError):cash_service.transition_handover(env[1],env[2].id,handover.id)
 payment_service.refund(env[1],env[2].id,p.id,'R',50,'Correction',staff_assignment_id=staff.id);db.session.commit()
 assert cash_service.custody(env[2].id,staff.id)==0
 assert db.session.query(Payment).count()==1
 cash_service.close(env[1],env[2].id,session.id,160);db.session.commit()


def test_manual_movements_reversal_and_closed_guard(env):
 session=cash_service.open(env[1],env[2].id,'C',20);db.session.flush()
 movement=cash_service.movement(env[1],env[2].id,session.id,'DEPOSIT',100,'Fond complémentaire');db.session.commit()
 cash_service.reverse(env[1],env[2].id,movement.id,'Erreur');db.session.commit()
 assert cash_service.expected(session)==20
 with pytest.raises(ValueError):cash_service.reverse(env[1],env[2].id,movement.id,'Encore')
 with pytest.raises(ValueError):cash_service.movement(env[1],env[2].id,session.id,'WITHDRAWAL',21,'Banque')
 cash_service.close(env[1],env[2].id,session.id,20);db.session.commit()
 with pytest.raises(ValueError):cash_service.movement(env[1],env[2].id,session.id,'DEPOSIT',10,'Fermé')


def test_finance_api_permissions_and_refund_limits(env):
 value=order(env);client=env[0].test_client();base=f'/api/v1/bars/{env[2].id}'
 response=client.post(base+'/payments',headers=env[6],json=dict(order_id=value.id,reference='P',method='CARD',amount_presented=200,amount_applied=200));assert response.status_code==201
 pid=response.json['data']['id']
 response=client.post(base+'/refunds',headers=env[6],json=dict(payment_id=pid,reference='R',amount=201,reason='Erreur'));assert response.status_code==422
 assert db.session.query(Refund).count()==0
 response=client.post(base+'/refunds',headers=env[6],json=dict(payment_id=pid,reference='R',amount=100,reason='Erreur'));assert response.status_code==201,response.json
 assert client.get(base+f'/orders/{value.id}/balance',headers=env[6]).json['data']['amount_due']=='100.0000'
 for path in ('payments','refunds','cash-sessions','cash-movements','cash-handovers','staff-cash'):
  assert client.get(base+'/'+path,headers=env[6]).status_code==200
 from app.auth import issue
 token,_=issue(env[5],env[2].id);db.session.commit()
 assert client.get(base+'/refunds',headers={'Authorization':'Bearer '+token}).status_code==403
 assert client.post(base+'/refunds',headers={'Authorization':'Bearer '+token},json={}).status_code in {400,403}
 with pytest.raises(LookupError):payment_service.refund(env[1],env[2].id,999,'BAD',1,'Erreur')


def test_web_finance_csrf_and_workflow(env):
 import re
 client=env[0].test_client()
 page=client.get('/login');csrf=re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',page.text).group(1)
 client.post('/login',data=dict(email=env[1].email,password='test-password',csrf_token=csrf))
 path=f'/bars/{env[2].id}/finance'
 page=client.get(path);assert page.status_code==200
 csrf=re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',page.text).group(1)
 assert client.post(path,data=dict(action='open',reference='BAD',opening_amount=0)).status_code==400
 response=client.post(path,data=dict(action='open',reference='WEB',opening_amount=50,csrf_token=csrf),follow_redirects=True)
 assert response.status_code==200;assert 'WEB' in response.text
 assert client.get(f'/bars/{env[3].id}/finance').status_code==404


def test_duplicate_refund_rolls_back_and_foreign_return_rejected(env):
 value=order(env,served=True)
 p=payment_service.record(env[1],env[2].id,value.id,'P','CARD',200,200);db.session.commit()
 client=env[0].test_client();path=f'/api/v1/bars/{env[2].id}/refunds'
 data=dict(payment_id=p.id,reference='R',amount=50,reason='Correction')
 assert client.post(path,headers=env[6],json={**data,'order_return_id':999}).status_code==404
 assert client.post(path,headers=env[6],json=data).status_code==201
 assert client.post(path,headers=env[6],json=data).status_code==409
 assert db.session.query(Refund).count()==1
 assert order_balance(value)['net_paid']==150


def test_refund_suspension_and_server_denied(env):
 value=order(env);p=payment_service.record(env[1],env[2].id,value.id,'P','CARD',100,100);db.session.commit()
 with pytest.raises(PermissionError):payment_service.refund(env[5],env[2].id,p.id,'R',10,'Correction')
 env[2].status='SUSPENDED';db.session.commit()
 with pytest.raises(PermissionError):payment_service.refund(env[1],env[2].id,p.id,'R',10,'Correction')
 assert db.session.query(Refund).count()==0
