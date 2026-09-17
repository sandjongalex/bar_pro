from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask_migrate import upgrade
from app import create_app
from app.extensions import db
from app.models import Bar, User

def app_with_auth(tmp_path):
    key=ec.generate_private_key(ec.SECP256R1())
    private=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    app=create_app("testing",{"SECRET_KEY":"test-only","SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'auth.sqlite'}","JWT_PRIVATE_KEY":private,"WTF_CSRF_ENABLED":True})
    with app.app_context():
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
        owner=User(email="owner.auth.demo@example.invalid",display_name="DEMO",category="OWNER"); owner.set_password("correct-password")
        db.session.add(owner);db.session.flush(); db.session.add(Bar(owner_id=owner.id,name="DEMO Auth",timezone="Africa/Douala",currency="XAF"));db.session.commit()
    return app

def test_api_login_refresh_logout_and_disabled(tmp_path):
    app=app_with_auth(tmp_path); client=app.test_client()
    assert client.post("/api/v1/auth/tokens",json={"email":"owner.auth.demo@example.invalid","password":"wrong","bar_id":1}).status_code==401
    response=client.post("/api/v1/auth/tokens",json={"email":"owner.auth.demo@example.invalid","password":"correct-password","bar_id":1}); assert response.status_code==200
    refresh=response.get_json()["data"]["refresh_token"]
    assert client.post("/api/v1/auth/tokens/refresh",json={"refresh_token":refresh}).status_code==200
    assert client.post("/api/v1/auth/logout",json={"refresh_token":refresh}).status_code==200
    assert client.post("/api/v1/auth/tokens/refresh",json={"refresh_token":refresh}).status_code==401
    with app.app_context():
        db.session.get(User,1).is_active=False;db.session.commit()
    assert client.post("/api/v1/auth/tokens",json={"email":"owner.auth.demo@example.invalid","password":"correct-password","bar_id":1}).status_code==401

def test_web_login_requires_csrf_and_session(tmp_path):
    app=app_with_auth(tmp_path); client=app.test_client()
    assert client.post("/login",data={"email":"owner.auth.demo@example.invalid","password":"correct-password"}).status_code==400
