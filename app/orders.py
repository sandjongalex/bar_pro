from flask import Blueprint,jsonify,request,render_template
from flask_login import current_user,login_required
from app.extensions import db
from app.order_services import order_service
from app.models import Product,ProductCategory
from app.permissions import permissions
from app.auth import api_required
bp=Blueprint("orders",__name__,url_prefix="/api/v1/bars/<int:bar_id>/orders")
web_bp=Blueprint("orders_web",__name__,url_prefix="/bars/<int:bar_id>/orders")
@web_bp.route("/new",methods=["GET","POST"])
@login_required
def quick(bar_id):
 permissions.require(current_user,"orders.create",bar_id)
 products=Product.query.filter_by(bar_id=bar_id,is_active=True).order_by(Product.name).all()
 created=None
 if request.method=="POST":
  lines=[{"product_id":product.id,"quantity":request.form.get(f"quantity_{product.id}")} for product in products if request.form.get(f"quantity_{product.id}","").strip() not in {"","0"}]
  created=order_service.create(current_user,bar_id,request.form["reference"],lines)
  order_service.confirm(current_user,bar_id,created.id);db.session.commit()
 return render_template("order_quick.html",bar_id=bar_id,products=products,created_order=created)
@bp.post("")
@api_required
def create(bar_id):
 try:
  x=request.get_json();o=order_service.create(request.api_user,bar_id,x["reference"],x["lines"],x.get("table_id"),x.get("customer_id"),x.get("notes"));db.session.commit();return jsonify({"success":True,"data":{"id":str(o.id),"status":o.status},"meta":{}}),201
 except (ValueError,) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Commande refusée","details":None}}),400
@bp.post("/<int:order_id>/confirm")
@api_required
def confirm(bar_id,order_id):
 try:o=order_service.confirm(request.api_user,bar_id,order_id);db.session.commit();return jsonify({"success":True,"data":{"status":o.status},"meta":{}})
 except (ValueError,) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Confirmation refusée","details":None}}),409
@bp.patch("/<int:order_id>")
@api_required
def adjust(bar_id,order_id):
 try:
  x=request.get_json() or {};o=order_service.adjust_confirmed(request.api_user,bar_id,order_id,x.get("lines",[]),x.get("reason",""));db.session.commit();return jsonify({"success":True,"data":{"status":o.status,"total_amount":str(o.total_amount)},"meta":{}})
 except LookupError:db.session.rollback();return jsonify({"success":False,"error":{"code":"NOT_FOUND","message":"Commande introuvable","details":None}}),404
 except (ValueError,) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Modification refusée","details":None}}),409
@bp.post("/<int:order_id>/serve")
@api_required
def serve(bar_id,order_id):
 try:o=order_service.serve(request.api_user,bar_id,order_id);db.session.commit();return jsonify({"success":True,"data":{"status":o.status},"meta":{}})
 except (ValueError,) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Service refusé","details":None}}),409
@bp.post("/<int:order_id>/cancel")
@api_required
def cancel(bar_id,order_id):
 try:o=order_service.cancel(request.api_user,bar_id,order_id,(request.get_json() or {}).get("reason",""));db.session.commit();return jsonify({"success":True,"data":{"status":o.status},"meta":{}})
 except (ValueError,) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Annulation refusée","details":None}}),409
@bp.post("/<int:order_id>/returns")
@api_required
def returns(bar_id,order_id):
 try:o=order_service.return_lines(request.api_user,bar_id,order_id,(request.get_json() or {}).get("lines",[]),(request.get_json() or {}).get("reason",""));db.session.commit();return jsonify({"success":True,"data":{"id":str(o.id)},"meta":{}})
 except (ValueError,) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Retour refusé","details":None}}),409
