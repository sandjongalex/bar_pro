from flask import Blueprint, jsonify, request, render_template
from flask_login import current_user, login_required
from app.catalog_services import create_product, list_products
from app.auth import api_required
from app.extensions import db

catalog_bp=Blueprint("catalog",__name__,url_prefix="/bars/<int:bar_id>/catalog")
api_catalog_bp=Blueprint("api_catalog",__name__,url_prefix="/api/v1/bars/<int:bar_id>/products")
def output(item): return {"id":str(item.id),"name":item.name,"sku":item.sku,"category_id":str(item.category_id),"sale_price":str(item.sale_price),"is_active":item.is_active,"image_key":item.image_key}
@catalog_bp.get("")
@login_required
def web_list(bar_id):
    try: products=list_products(current_user,bar_id,request.args.get("q"),request.args.get("category_id",type=int),request.args.get("active",type=lambda x:x=="true"),request.args.get("page",1,type=int));return render_template("catalog.html",products=products,bar_id=bar_id)
    except PermissionError:return "Introuvable",404
@api_catalog_bp.get("")
@api_required
def api_list(bar_id):
    try:
        page=list_products(request.api_user,bar_id,request.args.get("q"),request.args.get("category_id",type=int),request.args.get("active",type=lambda x:x=="true"),request.args.get("page",1,type=int));return jsonify({"success":True,"data":[output(x) for x in page.items],"meta":{"page":page.page,"pages":page.pages,"total":page.total}})
    except PermissionError:return jsonify({"success":False,"error":{"code":"NOT_FOUND","message":"Introuvable","details":None}}),404
@api_catalog_bp.post("")
@api_required
def api_create(bar_id):
    try: item=create_product(request.api_user,bar_id,request.form or request.get_json(),request.files.get("image"));db.session.commit();return jsonify({"success":True,"data":output(item),"meta":{}}),201
    except (PermissionError,LookupError,ValueError) as exc: db.session.rollback();return jsonify({"success":False,"error":{"code":"BUSINESS_RULE_VIOLATION","message":"Données invalides","details":None}}),400
