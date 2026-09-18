from pathlib import Path

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import Bar, CashSession, User, utcnow


def migrated_app(tmp_path: Path):
    app = create_app("testing", {"SECRET_KEY": "test-only", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'schema.sqlite3'}"})
    with app.app_context():
        upgrade(directory=app.config["MIGRATIONS_DIRECTORY"])
    return app


def test_initial_migration_creates_every_model_table(tmp_path):
    app = migrated_app(tmp_path)
    with app.app_context():
        actual = set(inspect(db.engine).get_table_names())
        assert set(db.metadata.tables).issubset(actual)
        assert len(db.metadata.tables) == 40


def test_initial_downgrade_refuses_destructive_data_loss(tmp_path):
    app = migrated_app(tmp_path)
    with app.app_context(), pytest.raises(SystemExit) as exc:
        downgrade(directory=app.config["MIGRATIONS_DIRECTORY"], revision="base")
    assert exc.value.code == 1


def test_unique_email_and_single_open_cash_session(tmp_path):
    app = migrated_app(tmp_path)
    with app.app_context():
        owner = User(email="owner.constraint.demo@example.invalid", display_name="DEMO", category="OWNER")
        owner.set_password("DEMO-ONLY")
        db.session.add(owner); db.session.flush()
        bar = Bar(owner_id=owner.id, name="DEMO constraints", timezone="Africa/Douala", currency="XAF")
        db.session.add(bar); db.session.flush()
        first = CashSession(bar_id=bar.id, reference="DEMO-CASH-1", opened_by_id=owner.id, opened_at=utcnow(), currency="XAF", opening_amount=0)
        second = CashSession(bar_id=bar.id, reference="DEMO-CASH-2", opened_by_id=owner.id, opened_at=utcnow(), currency="XAF", opening_amount=0)
        db.session.add_all((first, second))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_seed_demo_has_two_owners_and_is_idempotent(tmp_path):
    app = migrated_app(tmp_path)
    runner = app.test_cli_runner()
    assert runner.invoke(args=["seed-demo"]).exit_code == 0
    with app.app_context():
        assert db.session.query(User).filter_by(category="OWNER").count() == 2
        assert db.session.query(Bar).count() == 3
    assert "aucune écriture" in runner.invoke(args=["seed-demo"]).output


def test_migrated_columns_constraints_and_metadata_match(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    app=migrated_app(tmp_path)
    with app.app_context(), db.engine.connect() as conn:
        inspector=inspect(conn)
        assert compare_metadata(MigrationContext.configure(conn),db.metadata)==[]
        for name,table in db.metadata.tables.items():
            columns={c["name"]:c for c in inspector.get_columns(name)}
            assert set(columns)==set(table.columns.keys())
            for column in table.columns:
                assert columns[column.name]["nullable"]==column.nullable
            actual={c["name"]:c["sqltext"] for c in inspector.get_check_constraints(name)}
            expected={c.name:str(c.sqltext) for c in table.constraints if c.__class__.__name__=="CheckConstraint"}
            assert actual==expected


def test_flask_cli_upgrade_and_routes_on_empty_database(tmp_path):
    app=create_app("testing",{"SECRET_KEY":"test-only","SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'empty.sqlite'}"})
    runner=app.test_cli_runner()
    result=runner.invoke(args=["db","upgrade"])
    assert result.exit_code==0,result.output
    result=runner.invoke(args=["db","check"])
    assert result.exit_code==0,result.output
    from flask.cli import routes_command
    result=runner.invoke(routes_command)
    assert result.exit_code==0,result.output
    assert "finance.payment" in result.output
    assert "orders.returns" in result.output


def test_data_model_document_matches_models():
    from scripts.schema_document import render
    assert Path("docs/DATA_MODEL.md").read_text(encoding="utf-8")==render()


def test_mysql_migration_sql_compiles_without_blob_keys(tmp_path):
    app=create_app("testing",{"SECRET_KEY":"test-only","SQLALCHEMY_DATABASE_URI":"mysql+pymysql://example.invalid/schema"})
    result=app.test_cli_runner().invoke(args=["db","upgrade","--sql"])
    assert result.exit_code==0,result.output
    assert "BINARY(32)" in result.output
    assert "CREATE TABLE payments" in result.output
    assert "ck_payments_tender" in result.output
    assert "BLOB" not in result.output


def test_upgrade_preserves_legacy_orders_and_payment_amounts(tmp_path):
    import sqlalchemy as sa
    from app.models import Payment,Order
    app=create_app("testing",{"SECRET_KEY":"test-only","SQLALCHEMY_DATABASE_URI":f"sqlite:///{tmp_path/'legacy.sqlite'}"})
    with app.app_context():
        upgrade(revision="9e4f5a6b7c8d")
        owner=User(email="legacy@example.invalid",display_name="Legacy",category="OWNER");owner.set_password("test-only")
        db.session.add(owner);db.session.flush()
        bar=Bar(owner_id=owner.id,name="Legacy",timezone="Africa/Douala",currency="XAF");db.session.add(bar);db.session.flush()
        old=sa.MetaData()
        orders=sa.Table("orders",old,autoload_with=db.session.connection())
        payments=sa.Table("payments",old,autoload_with=db.session.connection())
        now=utcnow()
        db.session.execute(orders.insert().values(id=1,bar_id=bar.id,reference="OLD",status="POSTED",currency="XAF",subtotal_amount=100,discount_amount=0,tax_amount=0,total_amount=100,posted_at=now,created_by_id=owner.id,created_at=now,updated_at=now))
        db.session.execute(payments.insert().values(id=1,bar_id=bar.id,order_id=1,reference="OLD-PAY",amount=100,currency="XAF",method="CARD",received_at=now,recorded_by_id=owner.id,created_at=now,updated_at=now))
        db.session.commit()
        upgrade()
        assert db.session.get(Order,1).status=="CONFIRMED"
        assert db.session.get(Order,1).payment_status=="PAID"
        payment=db.session.get(Payment,1)
        assert payment.amount==payment.amount_applied==payment.amount_presented==100
        assert payment.change_given==0
