import os, uuid
from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from app.bar_services import assign_staff, create_bar, create_owner, update_bar
from app.extensions import db
from app.models import Bar, Order, Product, StaffAssignment, StockBalance, User
from app.permissions import permissions
from app.auth import api_required

bars_bp=Blueprint("bars",__name__,url_prefix="/bars")
api_bars_bp=Blueprint("api_bars",__name__,url_prefix="/api/v1/bars")
def deny(reason): return jsonify({"success":False,"error":{"code":reason,"message":"Access denied.","details":None}}),404 if reason=="NOT_FOUND" else 403
def serialize(bar): return {"id":str(bar.id),"name":bar.name,"status":bar.status,"timezone":bar.timezone,"currency":bar.currency,"address":bar.address,"phone":bar.phone,"logo_key":bar.logo_key,"stock_alert_threshold":str(bar.stock_alert_threshold),"credit_sales_enabled":bar.credit_sales_enabled}


def _active_owners():
    return list(db.session.scalars(select(User).where(User.category=="OWNER",User.is_active.is_(True)).order_by(User.display_name,User.email)))


def _form_error_message(code):
    messages={
        "FORBIDDEN":"Vous n'êtes pas autorisé à créer un établissement.",
        "OWNER_NOT_FOUND":"Le propriétaire sélectionné est introuvable ou inactif.",
        "INVALID_OWNER_EMAIL":"L'adresse e-mail du propriétaire est invalide.",
        "INVALID_OWNER_NAME":"Le nom du propriétaire est obligatoire.",
        "WEAK_OWNER_PASSWORD":"Le mot de passe du propriétaire doit contenir au moins 8 caractères.",
        "OWNER_EMAIL_EXISTS":"Cette adresse e-mail est déjà utilisée.",
        "PASSWORD_MISMATCH":"Les deux mots de passe du propriétaire ne correspondent pas.",
        "INVALID_BAR_NAME":"Le nom de l'établissement est obligatoire.",
        "INVALID_BAR_ADDRESS":"L'adresse de l'établissement est trop longue.",
        "INVALID_BAR_PHONE":"Le numéro de téléphone est invalide ou trop long.",
        "INVALID_CURRENCY":"La devise doit être un code de 3 lettres, par exemple XAF.",
        "INVALID_TIMEZONE":"Le fuseau horaire sélectionné est invalide.",
        "INVALID_THRESHOLD":"Le seuil d'alerte stock doit être un nombre positif ou nul.",
    }
    return messages.get(str(code),"Impossible de créer l'établissement. Vérifiez les informations saisies.")


@bars_bp.get("/")
@login_required
def web_list():
    bars=[b for b in Bar.query.order_by(Bar.created_at.desc(),Bar.name).all() if permissions.evaluate(current_user,"bars.read",b.id).allowed]
    return render_template(
        "bars/list.html",
        bars=bars,
        can_create=current_user.category=="SUPER_ADMIN",
        created=request.args.get("created"),
    )


@bars_bp.get("/<int:bar_id>")
@login_required
def web_detail(bar_id):
    decision=permissions.evaluate(current_user,"bars.read",bar_id)
    if not decision.allowed:
        raise LookupError("NOT_FOUND")

    bar=db.session.get(Bar,bar_id)
    if not bar:
        raise LookupError("NOT_FOUND")

    def allowed(action):
        return permissions.evaluate(current_user,action,bar_id).allowed

    rights={
        "catalog_read":allowed("catalog.read"),
        "inventory_read":allowed("inventory.read"),
        "orders_read":allowed("orders.read"),
        "orders_create":allowed("orders.create"),
        "payments_read":allowed("payments.read"),
        "customers_read":allowed("customers.read"),
        "staff_read":allowed("staff.read"),
        "staff_manage":allowed("staff.manage"),
        "settings":allowed("bars.update_settings"),
        "reports":allowed("reports.read"),
    }

    stats={"products":None,"staff":None,"low_stock":None,"open_orders":None}

    if rights["catalog_read"]:
        stats["products"]=db.session.scalar(
            select(func.count(Product.id)).where(Product.bar_id==bar_id,Product.is_active.is_(True))
        ) or 0

    if rights["staff_read"]:
        stats["staff"]=db.session.scalar(
            select(func.count(StaffAssignment.id)).where(
                StaffAssignment.bar_id==bar_id,
                StaffAssignment.ended_at.is_(None),
            )
        ) or 0

    if rights["inventory_read"]:
        stats["low_stock"]=db.session.scalar(
            select(func.count(StockBalance.id))
            .join(Product,Product.id==StockBalance.product_id)
            .where(
                StockBalance.bar_id==bar_id,
                Product.bar_id==bar_id,
                Product.is_active.is_(True),
                StockBalance.quantity<=Product.stock_alert_threshold,
            )
        ) or 0

    if rights["orders_read"]:
        stats["open_orders"]=db.session.scalar(
            select(func.count(Order.id)).where(
                Order.bar_id==bar_id,
                Order.status.in_(["DRAFT","CONFIRMED"]),
            )
        ) or 0

    return render_template("bars/detail.html",bar=bar,stats=stats,rights=rights)


@bars_bp.route("/new",methods=["GET","POST"])
@login_required
def web_create():
    if current_user.category!="SUPER_ADMIN":
        raise PermissionError("FORBIDDEN")

    owners=_active_owners()
    values={
        "owner_mode":"existing" if owners else "new",
        "owner_id":"",
        "owner_display_name":"",
        "owner_email":"",
        "name":"",
        "address":"",
        "phone":"",
        "timezone":"Africa/Douala",
        "currency":"XAF",
        "stock_alert_threshold":"0",
        "credit_sales_enabled":False,
    }
    error_message=None

    if request.method=="POST":
        values.update({
            "owner_mode":request.form.get("owner_mode","existing"),
            "owner_id":request.form.get("owner_id","").strip(),
            "owner_display_name":request.form.get("owner_display_name","").strip(),
            "owner_email":request.form.get("owner_email","").strip(),
            "name":request.form.get("name","").strip(),
            "address":request.form.get("address","").strip(),
            "phone":request.form.get("phone","").strip(),
            "timezone":request.form.get("timezone","Africa/Douala").strip(),
            "currency":request.form.get("currency","XAF").strip().upper(),
            "stock_alert_threshold":request.form.get("stock_alert_threshold","0").strip(),
            "credit_sales_enabled":request.form.get("credit_sales_enabled")=="1",
        })
        try:
            if values["owner_mode"]=="new":
                password=request.form.get("owner_password","")
                confirmation=request.form.get("owner_password_confirm","")
                if password!=confirmation:
                    raise ValueError("PASSWORD_MISMATCH")
                owner=create_owner(current_user,{
                    "display_name":values["owner_display_name"],
                    "email":values["owner_email"],
                    "password":password,
                })
                owner_id=owner.id
            else:
                try:
                    owner_id=int(values["owner_id"])
                except (TypeError,ValueError):
                    raise LookupError("OWNER_NOT_FOUND") from None

            bar=create_bar(current_user,owner_id,{
                "name":values["name"],
                "address":values["address"],
                "phone":values["phone"],
                "timezone":values["timezone"],
                "currency":values["currency"],
                "stock_alert_threshold":values["stock_alert_threshold"],
                "credit_sales_enabled":values["credit_sales_enabled"],
            })
            db.session.commit()
            return redirect(url_for("bars.web_detail",bar_id=bar.id))
        except (PermissionError,LookupError,ValueError) as exc:
            db.session.rollback()
            error_message=_form_error_message(exc)
            owners=_active_owners()

    return render_template("bars/create.html",owners=owners,values=values,error=error_message)


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
