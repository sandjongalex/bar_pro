from decimal import Decimal
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from flask_migrate import upgrade
from sqlalchemy import select,func,text
from app import create_app
from app.extensions import db
from app.models import *
from app.auth import issue
from app.stock_service import stock_service
from app.inventory_services import inventory_service
from app.order_services import order_service
from app.payment_services import payment_service
from app.cash_services import cash_service

@pytest.fixture
def env(tmp_path):
 key=ec.generate_private_key(ec.SECP256R1())
 pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
 app=create_app("testing",{"SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'flow.sqlite'}","JWT_PRIVATE_KEY":pem,"WTF_CSRF_ENABLED":True})
 with app.app_context():
  upgrade()
  db.session.execute(text("PRAGMA foreign_keys=ON"))
  owner=User(email="owner@example.invalid",display_name="Owner",category="OWNER");owner.set_password("test-password")
  other=User(email="other@example.invalid",display_name="Other",category="OWNER");other.set_password("test-password")
  server=User(email="server@example.invalid",display_name="Server",category="EMPLOYEE");server.set_password("test-password")
  db.session.add_all([owner,other,server]);db.session.flush()
  bar=Bar(owner_id=owner.id,name="One",timezone="Africa/Douala",currency="XAF")
  foreign=Bar(owner_id=other.id,name="Two",timezone="Africa/Douala",currency="XAF")
  db.session.add_all([bar,foreign]);db.session.flush()
  category=ProductCategory(bar_id=bar.id,name="Drinks");db.session.add(category);db.session.flush()
  product=Product(bar_id=bar.id,category_id=category.id,name="Water",sku="WATER",base_unit="bottle",sale_price=100,valuation_unit_cost=40)
  staff=StaffAssignment(bar_id=bar.id,user_id=server.id,role="SERVER",started_at=utcnow())
  db.session.add_all([product,staff]);db.session.flush()
  stock_service.move(owner,bar.id,product.id,"INITIAL",10,"Opening stock")
  token,refresh=issue(owner,bar.id);db.session.commit()
  yield app,owner,bar,foreign,product,server,{"Authorization":"Bearer "+token},refresh
  db.session.remove();db.engine.dispose()

def order(env,quantity=2,served=False,actor=None):
 _,owner,bar,_,product,_,_,_=env
 value=order_service.create(actor or owner,bar.id,"ORD",[{"product_id":product.id,"quantity":quantity}])
 order_service.confirm(actor or owner,bar.id,value.id)
 if served: order_service.serve(actor or owner,bar.id,value.id)
 db.session.commit();return value

def balance(env):
 return db.session.scalar(select(StockBalance.quantity).where(StockBalance.product_id==env[4].id))

def test_order_server_confirm_serve_and_stock_once(env):
 value=order(env,actor=env[5]);assert balance(env)==8
 with pytest.raises(ValueError):order_service.confirm(env[5],env[2].id,value.id)
 order_service.serve(env[5],env[2].id,value.id);db.session.commit()
 assert balance(env)==8
 assert db.session.scalar(select(func.count()).select_from(StockMovement).where(StockMovement.order_line_id.is_not(None)))==1
 with pytest.raises(ValueError):order_service.replace_lines(env[1],value,[])

def test_cancel_restores_stock_once(env):
 value=order(env);order_service.cancel(env[1],env[2].id,value.id,"Mistake");db.session.commit()
 assert balance(env)==10
 with pytest.raises(ValueError):order_service.cancel(env[1],env[2].id,value.id,"Again")
 assert db.session.scalar(select(StockMovement).where(StockMovement.reversal_of_id.is_not(None))) is not None

def test_confirmed_order_quantity_is_adjusted_differentially(env):
 value=order(env,quantity=3)
 line=db.session.scalar(select(OrderLine).where(OrderLine.order_id==value.id))
 order_service.adjust_confirmed(env[1],env[2].id,value.id,[{"order_line_id":line.id,"quantity":"5"}],"Client ajoute deux")
 db.session.commit();assert balance(env)==5;assert line.quantity==5
 order_service.adjust_confirmed(env[1],env[2].id,value.id,[{"order_line_id":line.id,"quantity":"2"}],"Client retire trois")
 db.session.commit();assert balance(env)==8;assert line.quantity==2
 with pytest.raises(ValueError): order_service.adjust_confirmed(env[1],env[2].id,value.id,[{"order_line_id":line.id,"quantity":"1"}],"")

@pytest.mark.parametrize("bad",[0,-1,"NaN","Infinity","0.0000001"])
def test_bad_quantities(env,bad):
 with pytest.raises(ValueError):order_service.create(env[1],env[2].id,"BAD",[{"product_id":env[4].id,"quantity":bad}])
 db.session.rollback();assert balance(env)==10

def test_failed_multiline_confirm_rolls_back(env):
 empty=Product(bar_id=env[2].id,category_id=env[4].category_id,name="Empty",sku="EMPTY",base_unit="bottle",sale_price=100,valuation_unit_cost=40)
 db.session.add(empty);db.session.flush()
 value=order_service.create(env[1],env[2].id,"FAIL",[{"product_id":env[4].id,"quantity":1},{"product_id":empty.id,"quantity":1}]);db.session.commit()
 with pytest.raises(ValueError):order_service.confirm(env[1],env[2].id,value.id)
 db.session.rollback();assert balance(env)==10;assert value.status=="DRAFT"
 assert db.session.query(StockMovement).count()==1

def test_return_persists_and_enforces_cumulative_limit(env):
 value=order(env,served=True)
 line=db.session.scalar(select(OrderLine).where(OrderLine.order_id==value.id))
 data={"order_line_id":line.id,"quantity":1,"disposition":"RESTOCK"}
 result=order_service.return_lines(env[1],env[2].id,value.id,[data],"Unused");db.session.commit()
 assert result.total_amount==100;assert balance(env)==9
 data["disposition"]="LOSS"
 order_service.return_lines(env[1],env[2].id,value.id,[data],"Broken");db.session.commit();assert balance(env)==9
 with pytest.raises(ValueError):order_service.return_lines(env[1],env[2].id,value.id,[data],"Too many")
 db.session.rollback();assert db.session.query(OrderReturn).count()==2
 assert db.session.query(OrderReturnLine).count()==2

def test_inventory_count_post_and_stale(env):
 inv=inventory_service.create(env[1],env[2].id,"INV",[env[4].id],"Count");db.session.flush()
 inventory_service.count(env[1],env[2].id,inv.id,{str(env[4].id):"7"})
 inventory_service.post(env[1],env[2].id,inv.id);db.session.commit();assert balance(env)==7
 with pytest.raises(ValueError):inventory_service.post(env[1],env[2].id,inv.id)
 stale=inventory_service.create(env[1],env[2].id,"STALE",[env[4].id],"Count");db.session.flush()
 inventory_service.count(env[1],env[2].id,stale.id,{str(env[4].id):"6"});db.session.commit()
 stock_service.move(env[1],env[2].id,env[4].id,"LOSS",-1,"Broken");db.session.commit()
 with pytest.raises(ValueError,match="INVENTORY_STALE"):inventory_service.post(env[1],env[2].id,stale.id)
 db.session.rollback();assert balance(env)==6

def test_payment_partial_cash_change_and_close(env):
 value=order(env)
 cash=cash_service.open(env[1],env[2].id,"CASH",50);db.session.flush()
 payment_service.record(env[1],env[2].id,value.id,"PAY1","CASH",100,80,20,cash.id);db.session.commit()
 assert value.payment_status=="PARTIAL"
 payment_service.record(env[1],env[2].id,value.id,"PAY2","CARD",120,120);db.session.commit()
 assert value.payment_status=="PAID"
 assert db.session.scalar(select(func.sum(CashMovement.amount_delta)))==80
 with pytest.raises(ValueError):payment_service.record(env[1],env[2].id,value.id,"OVER","CARD",1,1)
 with pytest.raises(ValueError):cash_service.close(env[1],env[2].id,cash.id,129)
 cash_service.close(env[1],env[2].id,cash.id,129,"One missing");db.session.commit()
 assert cash.expected_closing_amount==130;assert cash.closing_difference==-1
 with pytest.raises(ValueError):cash_service.close(env[1],env[2].id,cash.id,130)

def test_payment_rejects_draft_and_server(env):
 value=order_service.create(env[1],env[2].id,"DRAFT",[{"product_id":env[4].id,"quantity":1}]);db.session.commit()
 with pytest.raises(ValueError):payment_service.record(env[1],env[2].id,value.id,"D","CARD",100,100)
 with pytest.raises(PermissionError):payment_service.record(env[5],env[2].id,value.id,"D","CARD",100,100)

@pytest.mark.parametrize("path",["products","inventories","stock/alerts"])
def test_api_tenant_and_bearer(env,path):
 client=env[0].test_client()
 assert client.get(f"/api/v1/bars/{env[2].id}/{path}").status_code==401
 assert client.get(f"/api/v1/bars/{env[3].id}/{path}",headers=env[6]).status_code==404
 assert client.get(f"/api/v1/bars/{env[2].id}/{path}",headers=env[6]).status_code==200

def test_routes_end_to_end_with_csrf_enabled(env):
 client=env[0].test_client();base=f"/api/v1/bars/{env[2].id}";headers=env[6]
 response=client.post(base+"/orders",headers=headers,json={"reference":"API","lines":[{"product_id":env[4].id,"quantity":1}]})
 assert response.status_code==201,response.json
 oid=response.json["data"]["id"]
 assert client.post(base+f"/orders/{oid}/confirm",headers=headers).status_code==200
 response=client.post(base+"/cash-sessions",headers=headers,json={"reference":"API-CASH","opening_amount":0});assert response.status_code==201,response.json
 cid=response.json["data"]["id"]
 response=client.post(base+"/payments",headers=headers,json={"order_id":oid,"reference":"API-PAY","method":"CASH","amount_presented":100,"amount_applied":100,"cash_session_id":cid});assert response.status_code==201,response.json
 response=client.post(base+f"/cash-sessions/{cid}/close",headers=headers,json={"counted_closing_amount":100});assert response.status_code==200,response.json
 assert Decimal(response.json["data"]["expected_closing_amount"])==100

def test_suspended_bar_writes_denied(env):
 env[2].status="SUSPENDED";db.session.commit()
 for action in (lambda:cash_service.open(env[1],env[2].id,"NO",0),lambda:stock_service.move(env[1],env[2].id,env[4].id,"LOSS",-1,"No"),lambda:order_service.create(env[1],env[2].id,"NO",[])):
  with pytest.raises(PermissionError):action()
 assert balance(env)==10

def test_web_csrf_login_and_foreign_routes(env):
 import re
 client=env[0].test_client();page=client.get("/login")
 csrf=re.search("name='csrf_token' value='([^']+)'",page.text).group(1)
 result=client.post("/login",data={"email":env[1].email,"password":"test-password","csrf_token":csrf});assert result.status_code==302
 for suffix in ("catalog","stock","inventories","orders/new"):
  assert client.get(f"/bars/{env[3].id}/{suffix}").status_code==404
  assert client.get(f"/bars/{env[2].id}/{suffix}").status_code==200
 assert client.get("/").headers["Location"]=="/dashboard"
 assert client.get(f"/api/v1/bars/{env[2].id}/products").status_code==401

def test_refresh_reuse_revokes_family_and_access(env):
 client=env[0].test_client();base="/api/v1/auth/"
 fresh=client.post(base+"tokens/refresh",json={"refresh_token":env[7]});assert fresh.status_code==200
 data=fresh.json["data"]
 assert client.post(base+"tokens/refresh",json={"refresh_token":env[7]}).status_code==401
 assert client.post(base+"tokens/refresh",json={"refresh_token":data["refresh_token"]}).status_code==401
 assert client.get("/api/v1/bars",headers={"Authorization":"Bearer "+data["access_token"]}).status_code==401

def test_jwt_expired_missing_claim_wrong_audience_and_version(env):
 import jwt
 client=env[0].test_client();app=env[0]
 original=env[6]["Authorization"][7:]
 claims=jwt.decode(original,options={"verify_signature":False})
 for change in ({"exp":1},{"aud":"other"},{"token_use":"refresh"},{"credentials_version":999}):
  token=jwt.encode({**claims,**change},app.config["JWT_PRIVATE_KEY"],algorithm="ES256")
  assert client.get("/api/v1/bars",headers={"Authorization":"Bearer "+token}).status_code==401
 del claims["exp"]
 token=jwt.encode(claims,app.config["JWT_PRIVATE_KEY"],algorithm="ES256")
 assert client.get("/api/v1/bars",headers={"Authorization":"Bearer "+token}).status_code==401
 env[1].credentials_version+=1;db.session.commit()
 assert client.get("/api/v1/bars",headers=env[6]).status_code==401

def test_ended_staff_token_denied(env):
 token,_=issue(env[5],env[2].id);db.session.commit()
 staff=db.session.scalar(select(StaffAssignment).where(StaffAssignment.user_id==env[5].id));staff.ended_at=utcnow();db.session.commit()
 assert env[0].test_client().get("/api/v1/bars",headers={"Authorization":"Bearer "+token}).status_code==401


def test_purchase_receipt_and_duplicate_prevention(env):
 from app.purchase_services import purchase_service
 supplier=Supplier(bar_id=env[2].id,name="Supply");db.session.add(supplier);db.session.flush()
 value=purchase_service.create(env[1],env[2].id,supplier.id,"PUR",[{"product_id":env[4].id,"quantity":3,"unit_cost":40}]);db.session.commit()
 purchase_service.receive(env[1],env[2].id,value.id);db.session.commit();assert balance(env)==13
 assert purchase_service.due(env[2].id,value.id)==120
 with pytest.raises(ValueError):purchase_service.receive(env[1],env[2].id,value.id)
 assert db.session.scalar(select(StockMovement).where(StockMovement.purchase_line_id.is_not(None))) is not None


def test_supplier_purchase_payment_and_reversal_workflow(env):
 from app.purchase_services import purchase_service,supplier_service
 supplier=supplier_service.create(env[1],env[2].id,{"name":"Supply","phone":"123"});db.session.commit()
 purchase=purchase_service.create(env[1],env[2].id,supplier.id,"PUR-PAY",[{"product_id":env[4].id,"quantity":2,"unit_cost":30}]);db.session.commit()
 purchase_service.update(env[1],env[2].id,purchase.id,{"supplier_invoice_reference":"INV-1","lines":[{"product_id":env[4].id,"quantity":3,"unit_cost":30}]});db.session.commit()
 assert purchase.total_amount==90
 purchase_service.receive(env[1],env[2].id,purchase.id);db.session.commit()
 payment=purchase_service.pay(env[1],env[2].id,purchase.id,"SP1",40,"CARD","Acompte");db.session.commit()
 assert purchase_service.due(env[2].id,purchase.id)==50
 reversal=purchase_service.reverse_payment(env[1],env[2].id,payment.id,"SPR1","Erreur");db.session.commit()
 assert reversal.reversal_of_id==payment.id
 assert purchase_service.due(env[2].id,purchase.id)==90
 with pytest.raises(ValueError):purchase_service.reverse_payment(env[1],env[2].id,payment.id,"SPR2","Encore")
 db.session.rollback()
 with pytest.raises(ValueError):purchase_service.pay(env[1],env[2].id,purchase.id,"OVER",91,"CARD","Trop")


def test_cash_supplier_payment_updates_drawer(env):
 from app.purchase_services import purchase_service
 supplier=Supplier(bar_id=env[2].id,name="Supply");db.session.add(supplier);db.session.flush()
 purchase=purchase_service.create(env[1],env[2].id,supplier.id,"PUR-CASH",[{"product_id":env[4].id,"quantity":1,"unit_cost":40}])
 cash=cash_service.open(env[1],env[2].id,"SUP-CASH",100);db.session.commit()
 purchase_service.receive(env[1],env[2].id,purchase.id);db.session.commit()
 payment=purchase_service.pay(env[1],env[2].id,purchase.id,"SPC",40,"CASH","Règlement",cash.id);db.session.commit()
 assert cash_service.expected(cash)==60
 purchase_service.reverse_payment(env[1],env[2].id,payment.id,"SPCR","Correction",cash.id);db.session.commit()
 assert cash_service.expected(cash)==100


def test_purchase_cancel_supplier_api_and_permissions(env):
 client=env[0].test_client();base=f"/api/v1/bars/{env[2].id}"
 response=client.post(base+"/suppliers",headers=env[6],json={"name":"API Supply"});assert response.status_code==201,response.json
 sid=response.json["data"]["id"]
 assert client.get(base+"/suppliers",headers=env[6]).status_code==200
 response=client.post(base+"/purchases",headers=env[6],json={"supplier_id":sid,"reference":"API-PUR","lines":[{"product_id":env[4].id,"quantity":1,"unit_cost":25}]});assert response.status_code==201,response.json
 pid=response.json["data"]["id"]
 assert client.get(base+f"/purchases/{pid}",headers=env[6]).status_code==200
 assert client.post(base+f"/purchases/{pid}/cancel",headers=env[6],json={"reason":"Erreur"}).json["data"]["status"]=="CANCELLED"
 assert client.post(base+f"/purchases/{pid}/receive",headers=env[6]).status_code==422
 from app.auth import issue
 token,_=issue(env[5],env[2].id);db.session.commit()
 assert client.get(base+"/suppliers",headers={"Authorization":"Bearer "+token}).status_code==403
 assert client.post(base+"/suppliers",headers={"Authorization":"Bearer "+token},json={"name":"No"}).status_code==403


def test_foreign_secondary_reference_rejected_by_service_and_database(env):
 from sqlalchemy.exc import IntegrityError
 category=ProductCategory(bar_id=env[3].id,name="Foreign");db.session.add(category);db.session.flush()
 product=Product(bar_id=env[3].id,category_id=category.id,name="Foreign",sku="F",base_unit="bottle",sale_price=100,valuation_unit_cost=40);db.session.add(product);db.session.commit()
 with pytest.raises(LookupError):order_service.create(env[1],env[2].id,"BAD",[{"product_id":product.id,"quantity":1}])
 db.session.rollback()
 db.session.add(StockBalance(bar_id=env[2].id,product_id=product.id,quantity=0,version=0))
 with pytest.raises(IntegrityError):db.session.commit()
 db.session.rollback()


def test_cash_duplicate_closed_and_foreign_rejected(env):
 value=order(env)
 cash=cash_service.open(env[1],env[2].id,"C",0);db.session.commit()
 with pytest.raises(ValueError):cash_service.open(env[1],env[2].id,"C2",0)
 with pytest.raises(ValueError):payment_service.record(env[1],env[2].id,value.id,"P","CASH",100,100,cash_session_id=999)
 cash_service.close(env[1],env[2].id,cash.id,0);db.session.commit()
 with pytest.raises(ValueError):payment_service.record(env[1],env[2].id,value.id,"P","CASH",100,100,cash_session_id=cash.id)
 assert db.session.query(Payment).count()==0


def test_inventory_uninitialised_product_and_missing_count(env):
 product=Product(bar_id=env[2].id,category_id=env[4].category_id,name="New",sku="NEW",base_unit="bottle",sale_price=100,valuation_unit_cost=40);db.session.add(product);db.session.flush()
 inv=inventory_service.create(env[1],env[2].id,"NEW",[product.id],"First count");db.session.commit()
 with pytest.raises(ValueError,match="COUNT_REQUIRED"):inventory_service.post(env[1],env[2].id,inv.id)
 inventory_service.count(env[1],env[2].id,inv.id,{str(product.id):0});inventory_service.post(env[1],env[2].id,inv.id);db.session.commit()
 assert inv.status=="POSTED"


def test_logout_revokes_access_and_refresh(env):
 client=env[0].test_client()
 assert client.post("/api/v1/auth/logout",json={"refresh_token":env[7]}).status_code==200
 assert client.get("/api/v1/bars",headers=env[6]).status_code==401
 assert client.post("/api/v1/auth/tokens/refresh",json={"refresh_token":env[7]}).status_code==401


def test_snapshot_price_is_used_after_catalogue_change(env):
 value=order(env,served=True)
 line=db.session.scalar(select(OrderLine).where(OrderLine.order_id==value.id))
 env[4].sale_price=999;env[4].valuation_unit_cost=88;db.session.commit()
 result=order_service.return_lines(env[1],env[2].id,value.id,[{"order_line_id":line.id,"quantity":1,"disposition":"RESTOCK"}],"Return");db.session.commit()
 assert result.total_amount==100;assert value.total_amount==200
 assert db.session.scalar(select(StockMovement.unit_cost_snapshot).where(StockMovement.order_return_line_id.is_not(None)))==40


@pytest.mark.parametrize("payload",[None,[],{"amount_applied":"NaN"}])
def test_invalid_api_body_never_returns_500(env,payload):
 response=env[0].test_client().post(f"/api/v1/bars/{env[2].id}/payments",headers=env[6],json=payload)
 assert 400<=response.status_code<500
 assert response.json["success"] is False


def test_web_order_form_creates_and_confirms(env):
 import re
 client=env[0].test_client()
 page=client.get("/login");csrf=re.search("name='csrf_token' value='([^']+)'",page.text).group(1)
 client.post("/login",data={"email":env[1].email,"password":"test-password","csrf_token":csrf})
 path=f"/bars/{env[2].id}/orders/new"
 page=client.get(path);csrf=re.search('name="csrf_token" value="([^"]+)"',page.text).group(1)
 response=client.post(path,data={"csrf_token":csrf,"reference":"WEB-ORDER",f"quantity_{env[4].id}":2})
 assert response.status_code==200,response.text
 assert "WEB-ORDER" in response.text
 assert balance(env)==8
 assert db.session.scalar(select(Order).where(Order.reference=="WEB-ORDER")).status=="CONFIRMED"


def test_every_business_api_route_requires_bearer(env):
 client=env[0].test_client()
 for rule in env[0].url_map.iter_rules():
  if not rule.rule.startswith("/api/v1/bars"):continue
  path=rule.rule
  for argument in rule.arguments:path=path.replace("<int:"+argument+">","1")
  for method in rule.methods-{"HEAD","OPTIONS"}:
   response=client.open(path,method=method,json={})
   assert response.status_code==401,(path,method,response.status_code)


def test_jwt_tampering_wrong_algorithm_and_disabled_user(env):
 import jwt
 original=env[6]["Authorization"][7:];claims=jwt.decode(original,options={"verify_signature":False})
 client=env[0].test_client()
 token=jwt.encode(claims,"test-only-wrong-algorithm-key-1234567890",algorithm="HS256")
 assert client.get("/api/v1/bars",headers={"Authorization":"Bearer "+token}).status_code==401
 parts=original.split(".");parts[1]=parts[1][:-1]+("A" if parts[1][-1]!="A" else "B")
 assert client.get("/api/v1/bars",headers={"Authorization":"Bearer "+".".join(parts)}).status_code==401
 env[1].is_active=False;db.session.commit()
 assert client.get("/api/v1/bars",headers=env[6]).status_code==401


def test_server_cannot_read_supplier_debt(env):
 token,_=issue(env[5],env[2].id);db.session.commit()
 response=env[0].test_client().get(f"/api/v1/bars/{env[2].id}/purchases/1/balance",headers={"Authorization":"Bearer "+token})
 assert response.status_code==403
