from flask import Blueprint, jsonify, request
from app.auth import api_required
from app.extensions import db
from app.models import Subscription
from app.subscription_services import subscription_service

bp=Blueprint("subscriptions",__name__,url_prefix="/api/v1/bars/<int:bar_id>/subscriptions")
def serialize(x): return {"id":x.id,"bar_id":x.bar_id,"plan_id":x.plan_id,"reference":x.reference,"status":x.status,"price":str(x.price_amount_snapshot),"currency":x.currency,"starts_at":x.starts_at.isoformat(),"ends_at":x.ends_at.isoformat()}
@bp.get("")
@api_required
def list_items(bar_id):
 from app.permissions import permissions
 permissions.require(request.api_user,"subscriptions.read",bar_id)
 return jsonify({"success":True,"data":[serialize(x) for x in db.session.scalars(__import__('sqlalchemy').select(Subscription).where(Subscription.bar_id==bar_id).order_by(Subscription.id.desc()))],"meta":{}})
@bp.post("")
@api_required
def create(bar_id):
 try:
  x=request.get_json() or {}; item=subscription_service.create(request.api_user,bar_id,x["plan_id"],x["reference"]);db.session.commit();return jsonify({"success":True,"data":serialize(item),"meta":{}}),201
 except LookupError:return jsonify({"success":False,"error":{"code":"NOT_FOUND","message":"Ressource introuvable","details":None}}),404
 except (KeyError,ValueError) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":str(e),"message":"Abonnement refusé","details":None}}),422
@bp.post("/<int:subscription_id>/payments")
@api_required
def pay(bar_id,subscription_id):
 try:
  x=request.get_json() or {}; item=subscription_service.pay(request.api_user,bar_id,subscription_id,x["reference"],x["amount"]);db.session.commit();return jsonify({"success":True,"data":serialize(item),"meta":{}}),201
 except (KeyError,ValueError) as e:db.session.rollback();return jsonify({"success":False,"error":{"code":str(e),"message":"Paiement refusé","details":None}}),422
