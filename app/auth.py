"""Authentication routes and token service; no token values are logged."""
from datetime import datetime, timedelta
import hashlib, secrets, uuid
import jwt
from flask import Blueprint, current_app, jsonify, request, render_template_string, session, g
from flask_login import current_user, login_required, login_user, logout_user
from functools import wraps
from sqlalchemy import select
from app.extensions import db, limiter, login_manager
from app.models import ApiToken, TokenRevocation, User, utcnow
from app.permissions import permissions

auth_bp=Blueprint("auth",__name__)
api_auth_bp=Blueprint("api_auth",__name__,url_prefix="/api/v1/auth")

def error(code,status): return jsonify({"success":False,"error":{"code":code,"message":"Authentication failed.","details":None}}),status
def digest(value): return hashlib.sha256(value.encode() if isinstance(value,str) else b"invalid").digest()
def accessible(user,bar_id): return permissions.evaluate(user,"bars.read",bar_id).allowed
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
            if not user or not user.is_active or user.credentials_version!=claims.get("credentials_version"): raise jwt.InvalidTokenError()
            request.api_user=user; request.api_bar_id=int(claims["bar_id"])
            token=db.session.get(ApiToken,int(claims["sid"]))
            if not token or token.user_id!=user.id or token.bar_id!=request.api_bar_id: raise jwt.InvalidTokenError()
            revoked=db.session.scalar(select(TokenRevocation).where(TokenRevocation.api_token_id==token.id,TokenRevocation.reason!="rotation"))
            if revoked or not accessible(user,request.api_bar_id): raise jwt.InvalidTokenError()
        except (jwt.PyJWTError,KeyError,ValueError,TypeError): return error("INVALID_ACCESS_TOKEN",401)
        if "bar_id" in kwargs and kwargs["bar_id"]!=request.api_bar_id: return error("NOT_FOUND",404)
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
    return user if user and user.is_active and session.get("credentials_version")==user.credentials_version else None

@auth_bp.route("/login",methods=["GET","POST"])
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"])
def web_login():
    if request.method=="GET": return render_template_string("<form method='post'><input type='hidden' name='csrf_token' value='{{ csrf_token() }}'><input name='email'><input name='password' type='password'><input type='submit'></form>")
    user=db.session.scalar(select(User).where(User.email==request.form.get("email","").strip().lower()))
    if not user or not user.is_active or not user.check_password(request.form.get("password", "")): return "Invalid credentials",401
    session.clear(); g.pop("csrf_token",None); login_user(user); session["credentials_version"]=user.credentials_version; return "",302,{"Location":"/"}
@auth_bp.post("/logout")
@login_required
def web_logout(): logout_user(); return "",302,{"Location":"/login"}

@api_auth_bp.post("/tokens")
@limiter.limit(lambda: current_app.config["LOGIN_RATE_LIMIT"])
def token_login():
    body=request.get_json(silent=True) or {}; user=db.session.scalar(select(User).where(User.email==str(body.get("email","")).strip().lower()))
    if not user or not user.is_active or not user.check_password(str(body.get("password",""))): return error("INVALID_CREDENTIALS",401)
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
    if not user or not user.is_active or row.expires_at<=utcnow().replace(tzinfo=None) or row.credentials_version_snapshot!=user.credentials_version or not accessible(user,row.bar_id): return error("INVALID_REFRESH_TOKEN",401)
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
