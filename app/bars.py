import os, uuid
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required
from app.bar_services import assign_staff, create_bar, update_bar
from app.extensions import db
from app.models import Bar
from app.permissions import permissions
from app.auth import api_required

bars_bp=Blueprint("bars",__name__,url_prefix="/bars")
api_bars_bp=Blueprint("api_bars",__name__,url_prefix="/api/v1/bars")
def deny(reason): return jsonify({"success":False,"error":{"code":reason,"message":"Access denied.","details":None}}),404 if reason=="NOT_FOUND" else 403
def serialize(bar): return {"id":str(bar.id),"name":bar.name,"status":bar.status,"timezone":bar.timezone,"currency":bar.currency,"address":bar.address,"phone":bar.phone,"logo_key":bar.logo_key,"stock_alert_threshold":str(bar.stock_alert_threshold),"credit_sales_enabled":bar.credit_sales_enabled}

@bars_bp.get("/")
@login_required
def web_list():
    bars=[b for b in Bar.query.all() if permissions.evaluate(current_user,"bars.read",b.id).allowed]
    return "\n".join(f"{b.id}: {b.name}" for b in bars)

@api_bars_bp.get("")
@api_required
def list_bars():
    bar=db.session.get(Bar,request.api_bar_id)
    return jsonify({"success":True,"data":[serialize(bar)],"meta":{}})

@api_bars_bp.post("")
@api_required
def create():
    try: bar=create_bar(request.api_user,request.json["owner_id"],request.json,request.json.get("copy_from_id"));db.session.commit();return jsonify({"success":True,"data":serialize(bar),"meta":{}}),201
    except (PermissionError,LookupError) as exc: db.session.rollback();return deny(str(exc))

@api_bars_bp.patch("/<int:bar_id>")
@api_required
def patch(bar_id):
    try: bar=update_bar(request.api_user,bar_id,request.json or {});db.session.commit();return jsonify({"success":True,"data":serialize(bar),"meta":{}})
    except PermissionError as exc:return deny(str(exc))

@api_bars_bp.post("/<int:bar_id>/staff")
@api_required
def staff(bar_id):
    try: item=assign_staff(request.api_user,bar_id,request.json["user_id"],request.json["role"]);db.session.commit();return jsonify({"success":True,"data":{"id":str(item.id),"role":item.role},"meta":{}}),201
    except (PermissionError,LookupError) as exc:db.session.rollback();return deny(str(exc))
