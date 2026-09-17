import pytest
from app.config import validate_config,ConfigError
from app import create_app
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def production():
 key=ec.generate_private_key(ec.SECP256R1())
 return dict(ENVIRONMENT="production",SECRET_KEY="x"*48,SQLALCHEMY_DATABASE_URI="mysql+pymysql://example.invalid/schema?charset=utf8mb4",RATELIMIT_STORAGE_URI="redis://example.invalid/0",LOG_LEVEL="INFO",JWT_PRIVATE_KEY=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode(),JWT_PUBLIC_KEY=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode())


def test_valid_production_configuration_has_no_database_side_effects():
 validate_config(production())


@pytest.mark.parametrize("field,value",[("SECRET_KEY","short"),("SQLALCHEMY_DATABASE_URI","sqlite://"),("RATELIMIT_STORAGE_URI","memory://"),("JWT_PRIVATE_KEY",""),("JWT_PUBLIC_KEY","invalid")])
def test_unsafe_production_configuration_rejected(field,value):
 config=production();config[field]=value
 with pytest.raises(ConfigError):validate_config(config)


def test_factory_reads_changed_environment_and_key_files(monkeypatch,tmp_path):
 config=production();private=tmp_path/"private.pem";public=tmp_path/"public.pem"
 private.write_text(config["JWT_PRIVATE_KEY"]);public.write_text(config["JWT_PUBLIC_KEY"])
 monkeypatch.setenv("SECRET_KEY","new-value")
 monkeypatch.setenv("JWT_PRIVATE_KEY_FILE",str(private));monkeypatch.setenv("JWT_PUBLIC_KEY_FILE",str(public))
 app=create_app("testing")
 assert app.config["SECRET_KEY"]=="new-value"
 assert app.config["JWT_PRIVATE_KEY"]==config["JWT_PRIVATE_KEY"]
 assert app.config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_recycle"]==280
