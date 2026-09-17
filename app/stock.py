from flask import Blueprint,jsonify,request,render_template
from flask_login import current_user,login_required
from app.extensions import db
from app.models import StockBalance,StockMovement
from app.stock_service import StockError,stock_service
from app.permissions import permissions
from app.auth import api_required
stock_bp=Blueprint("stock",__name__,url_prefix="/bars/<int:bar_id>/stock")
api_stock_bp=Blueprint("api_stock",__name__,url_prefix="/api/v1/bars/<int:bar_id>/stock")
@stock_bp.get("")
@login_required
def history(bar_id):
 permissions.require(current_user,"inventory.read",bar_id); rows=StockMovement.query.filter_by(bar_id=bar_id).order_by(StockMovement.occurred_at.desc()).paginate(page=request.args.get("page",1,type=int),per_page=20,error_out=False); return render_template("stock.html",rows=rows,bar_id=bar_id)
@api_stock_bp.post("/movements")
@api_required
def move(bar_id):
 try:
  body=request.get_json();permissions.require(request.api_user,"inventory.adjust",bar_id)
  if body["movement_type"] not in {"INITIAL","ADJUSTMENT","LOSS"}: raise StockError("MANUAL_TYPE_REQUIRED")
  item=stock_service.move(request.api_user,bar_id,body["product_id"],body["movement_type"],body["quantity_delta"],body.get("reason",""));db.session.commit();return jsonify({"success":True,"data":{"id":str(item.id),"quantity_delta":str(item.quantity_delta)},"meta":{}}),201
 except (StockError,PermissionError,LookupError) as exc: db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Mouvement refusé","details":None}}),400
@api_stock_bp.get("/alerts")
@api_required
def alerts(bar_id):
 permissions.require(request.api_user,"inventory.read",bar_id); rows=StockBalance.query.join(__import__('app.models',fromlist=['Product']).Product).filter(StockBalance.bar_id==bar_id,StockBalance.quantity<=__import__('app.models',fromlist=['Product']).Product.stock_alert_threshold).all();return jsonify({"success":True,"data":[{"product_id":str(x.product_id),"quantity":str(x.quantity)} for x in rows],"meta":{}})
