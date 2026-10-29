"""Authentication routes and token service; no token values are logged."""
from datetime import datetime, timedelta
import hashlib, secrets, uuid
import jwt
from flask import Blueprint, abort, current_app, jsonify, request, render_template, session, g, url_for
from flask_login import current_user, login_required, login_user, logout_user
from functools import wraps
from sqlalchemy import select
from app.employee_context import get_current_employee_context
from app.extensions import db, limiter, login_manager
from app.models import ApiToken, TokenRevocation, User, utcnow
from app.permissions import permissions
from app.saas_governance import owner_enabled, suspension_level, trial_expired

auth_bp=Blueprint("auth",__name__)
api_auth_bp=Blueprint("api_auth",__name__,url_prefix="/api/v1/auth")

def error(code,status): return jsonify({"success":False,"error":{"code":code,"message":"Authentication failed.","details":None}}),status
def digest(value): return hashlib.sha256(value.encode() if isinstance(value,str) else b"invalid").digest()
def accessible(user,bar_id): return permissions.evaluate(user,"bars.support.read",bar_id).allowed

def _web_destination(user, employee_context=None):
    """Route an authenticated user only to a page they can actually view.

    A cashier/server retains a valid login off duty, but cannot access sales.
    Suspended venues similarly receive a safe informational dashboard.
    The caller must never use the browser's stored role/bar as authority.
    """
    if user.category != "EMPLOYEE":
        return url_for("web.dashboard")
    context = employee_context or get_current_employee_context(user)
    from app.models import Bar
    from app.shift_service import get_active_shift_for_assignment

    bar = db.session.get(Bar, context.bar_id)
    if not bar:
        raise LookupError("EMPLOYEE_BAR_UNAVAILABLE")
    if bar.status == "SUSPENDED" or suspension_level(bar.id) == 2:
        return url_for("web.dashboard")
    if context.role == "CASHIER":
        if not get_active_shift_for_assignment(context.bar_id, context.assignment.id):
            return url_for("web.dashboard")
        return url_for("checkout_web.checkout", bar_id=context.bar_id)
    if context.role == "SERVER":
        if not get_active_shift_for_assignment(context.bar_id, context.assignment.id):
            return url_for("web.dashboard")
        return url_for("orders_web.quick", bar_id=context.bar_id)
    if context.role == "BAR_ADMIN":
        return url_for("web.dashboard", bar_id=context.bar_id)
    raise LookupError("EMPLOYEE_ROLE_INVALID")

def _bind_employee_session(context):
    if context is None:
        return
    session["current_bar_id"] = context.bar_id
    session["current_role"] = context.role
    session["current_assignment_id"] = context.assignment.id

@auth_bp.before_app_request
def bind_employee_web_context():
    """Keep every authenticated EMPLOYEE request bound to its active assignment.

    The browser/session is never the source of truth for the employee tenant.
    The active StaffAssignment is re-resolved server-side. A URL targeting a
    different bar returns 404 so an employee cannot distinguish another tenant
    from a non-existent one.
    """
    if request.path.startswith("/api/") or not current_user.is_authenticated:
        return None
    if current_user.category != "EMPLOYEE":
        return None
    try:
        context = get_current_employee_context(current_user)
    except LookupError:
        logout_user()
        session.clear()
        g.pop("csrf_token", None)
        return "", 302, {"Location": url_for("auth.web_login")}

    _bind_employee_session(context)
    route_bar_id = (request.view_args or {}).get("bar_id")
    if route_bar_id is not None and int(route_bar_id) != context.bar_id:
        abort(404)
    return None

def api_required(view):
    """Authenticate bearer JWTs only; never accept a web cookie as API identity."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        header=request.headers.get("Authorization","")
        if not header.startswith("Bearer "): return error("AUTH_REQUIRED",401)
        try:
            key=current_app.config.get("JWT_PUBLIC_KEY") or current_app.config["JWT_PRIVATE_KEY"]
            claims=jwt.decode(header[7:],key,algorithms=["ES256"],audience=current_app.config["JWT_AUDIENCE"],issuer=current_app.config["JWT_ISSUER"],options={"require":["exp","iat","nbf","sub","bar_id","jti","token_use","credentials_version","sid"]})
            if claims.get("token_use")!="access": raise jwt.InvalidTokenError()
            user=db.session.get(User,int(claims["sub"]))
            if not owner_enabled(user) or user.credentials_version!=claims.get("credentials_version"): raise jwt.InvalidTokenError()
            request.api_user=user; request.api_bar_id=int(claims["bar_id"])
            token=db.session.get(ApiToken,int(claims["sid"]))
            if not token or token.user_id!=user.id or token.bar_id!=request.api_bar_id: raise jwt.InvalidTokenError()
            revoked=db.session.scalar(select(TokenRevocation).where(TokenRevocation.api_token_id==token.id,TokenRevocation.reason!="rotation"))
            if revoked or not accessible(user,request.api_bar_id): raise jwt.InvalidTokenError()
        except (jwt.PyJWTError,KeyError,ValueError,TypeError): return error("INVALID_ACCESS_TOKEN",401)
        if "bar_id" in kwargs and kwargs["bar_id"]!=request.api_bar_id: return error("NOT_FOUND",404)
        level = suspension_level(request.api_bar_id)
        if trial_expired(request.api_bar_id) and request.method not in {"GET", "HEAD", "OPTIONS"} and request.endpoint not in {"subscriptions.create", "subscriptions.pay", "saas_governance_api.update_bar_governance", "saas_governance_api.update_owner_governance"}:
            return error("TRIAL_EXPIRED_READ_ONLY", 403)
        governance_endpoints = {
            "saas_governance_api.update_bar_governance",
            "saas_governance_api.update_owner_governance",
        }
        if level == 2 and request.endpoint not in governance_endpoints | {"saas_governance_api.read_bar_governance"}:
            return error("BAR_ADMIN_LOCKED", 403)
        if level == 1 and request.method not in {"GET", "HEAD", "OPTIONS"} and request.endpoint not in governance_endpoints:
            return error("BAR_ADMIN_READ_ONLY", 403)
        if user.category == "SUPER_ADMIN" and request.method not in {"GET", "HEAD", "OPTIONS"}:
            administrative_endpoints = governance_endpoints | {
                "api_bars.create", "api_bars.patch", "api_bars.staff",
                "subscriptions.create", "subscriptions.pay",
            }
            if request.endpoint not in administrative_endpoints:
                return error("PLATFORM_COMMERCE_FORBIDDEN", 403)
        return view(*args,**kwargs)
    return wrapped
def issue(user,bar_id,family=None,parent=None):
    now=utcnow(); raw=secrets.token_urlsafe(48); family=family or uuid.uuid4().bytes
    row=ApiToken(bar_id=bar_id,user_id=user.id,label="mobile",rotated_from_id=parent,token_digest=digest(raw),family_id=family,credentials_version_snapshot=user.credentials_version,issued_at=now,expires_at=now+timedelta(days=30))
    db.session.add(row); db.session.flush()
    claims={"sid":str(row.id),"iss":current_app.config["JWT_ISSUER"],"aud":current_app.config["JWT_AUDIENCE"],"sub":str(user.id),"bar_id":bar_id,"jti":str(uuid.uuid4()),"iat":now,"nbf":now,"exp":now+timedelta(minutes=15),"token_use":"access","credentials_version":user.credentials_version}
    access=jwt.encode(claims,current_app.config["JWT_PRIVATE_KEY"],algorithm="ES256")
    return access,raw
@login_manager.user_loader
def load_user(user_id):
    try: user=db.session.get(User,int(user_id))
    except (TypeError,ValueError): return None
    return user if owner_enabled(user) and session.get("credentials_version")==user.credentials_version else None

@auth_bp.route("/login",methods=["GET","POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"])
def web_login():
    if request.method=="GET":
        if current_user.is_authenticated:
            try:
                context=get_current_employee_context(current_user)
                _bind_employee_session(context)
                return "",302,{"Location":_web_destination(current_user,context)}
            except LookupError:
                logout_user(); session.clear(); g.pop("csrf_token",None)
                return render_template("login.html",error="Aucune affectation active n'est disponible pour ce compte employé.",email=""),403
        return render_template("login.html",error=None,email="")
    email=request.form.get("email","").strip().lower()
    user=db.session.scalar(select(User).where(User.email==email))
    if not owner_enabled(user) or not user.check_password(request.form.get("password", "")):
        return render_template("login.html",error="Adresse e-mail ou mot de passe incorrect.",email=email),401
    try:
        context=get_current_employee_context(user)
        destination=_web_destination(user,context)
    except LookupError:
        return render_template("login.html",error="Aucune affectation active n'est disponible pour ce compte employé.",email=email),403
    session.clear(); g.pop("csrf_token",None); login_user(user); session["credentials_version"]=user.credentials_version; _bind_employee_session(context); return "",302,{"Location":destination}
@auth_bp.post("/logout")
@login_required
def web_logout(): logout_user(); return "",302,{"Location":"/login"}

@api_auth_bp.post("/tokens")
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"])
def token_login():
    body=request.get_json(silent=True) or {}; user=db.session.scalar(select(User).where(User.email==str(body.get("email","")).strip().lower()))
    if not owner_enabled(user) or not user.check_password(str(body.get("password",""))): return error("INVALID_CREDENTIALS",401)
    if user.category=="EMPLOYEE":
        try:
            context=get_current_employee_context(user)
        except LookupError:
            return error("NOT_FOUND",404)
        requested_bar_id=body.get("bar_id")
        if requested_bar_id is not None and (not isinstance(requested_bar_id,int) or isinstance(requested_bar_id,bool) or requested_bar_id!=context.bar_id):
            return error("NOT_FOUND",404)
        bar_id=context.bar_id
    else:
        bar_id=body.get("bar_id")
        if not isinstance(bar_id,int) or isinstance(bar_id,bool): return error("NOT_FOUND",404)
        if not accessible(user,bar_id): return error("NOT_FOUND",404)
    access,refresh=issue(user,bar_id); db.session.commit()
    return jsonify({"success":True,"data":{"token_type":"Bearer","access_token":access,"expires_in":900,"refresh_token":refresh},"meta":{}})

@api_auth_bp.post("/tokens/refresh")
def refresh():
    raw=(request.get_json(silent=True) or {}).get("refresh_token",""); row=db.session.scalar(select(ApiToken).where(ApiToken.token_digest==digest(raw)).with_for_update())
    if not row: return error("INVALID_REFRESH_TOKEN",401)
    if db.session.scalar(select(TokenRevocation).where(TokenRevocation.api_token_id==row.id)):
        revoke_family(row,"refresh_reuse"); db.session.commit()
        return error("REFRESH_TOKEN_REUSED",401)
    user=db.session.get(User,row.user_id)
    if not owner_enabled(user) or row.expires_at<=utcnow().replace(tzinfo=None) or row.credentials_version_snapshot!=user.credentials_version or not accessible(user,row.bar_id): return error("INVALID_REFRESH_TOKEN",401)
    db.session.add(TokenRevocation(bar_id=row.bar_id,api_token_id=row.id,revoked_by_id=user.id,revoked_at=utcnow(),reason="rotation")); access,new=issue(user,row.bar_id,row.family_id,row.id); db.session.commit()
    return jsonify({"success":True,"data":{"token_type":"Bearer","access_token":access,"expires_in":900,"refresh_token":new},"meta":{}})

@api_auth_bp.post("/logout")
def api_logout():
    raw=(request.get_json(silent=True) or {}).get("refresh_token",""); row=db.session.scalar(select(ApiToken).where(ApiToken.token_digest==digest(raw)).with_for_update())
    if row:
        revoke_family(row,"logout"); db.session.commit()
    return jsonify({"success":True,"data":{},"meta":{}})


def revoke_family(row,reason):
    for token in db.session.scalars(select(ApiToken).where(ApiToken.family_id==row.family_id,ApiToken.bar_id==row.bar_id).with_for_update()):
        revocation=db.session.scalar(select(TokenRevocation).where(TokenRevocation.api_token_id==token.id))
        if revocation:
            revocation.reason=reason
        else:
            db.session.add(TokenRevocation(bar_id=token.bar_id,api_token_id=token.id,revoked_by_id=token.user_id,revoked_at=utcnow(),reason=reason))
