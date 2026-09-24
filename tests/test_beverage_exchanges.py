from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.cash_services import cash_service
from app.exchange_services import exchange_service
from app.extensions import db
from app.models import BeverageExchange, CashMovement, Order, Product, StaffAssignment, StockBalance, StockMovement, User, utcnow
from app.stock_service import stock_service
from test_cashier_workspace import _cashier, _csrf, _login
from test_workflows import env


def setup_exchange(env, price=150, stock=10, quantity=2):
    app, owner, bar, _, product, server, _, _ = env
    replacement = Product(bar_id=bar.id, category_id=product.category_id, name="Castel", sku="CASTEL",
                          base_unit="bottle", sale_price=price, valuation_unit_cost=60)
    db.session.add(replacement)
    db.session.flush()
    if stock:
        stock_service.move(owner, bar.id, replacement.id, "INITIAL", stock, "Opening")
    staff = db.session.scalar(select(StaffAssignment).where(StaffAssignment.user_id == server.id))
    item = exchange_service.request(server, bar.id, "ECH-test", staff.id, product.id,
                                    replacement.id, quantity, quantity, "Changement client")
    db.session.commit()
    return item, replacement, staff


def qty(product):
    return db.session.scalar(select(StockBalance.quantity).where(StockBalance.product_id == product.id)) or 0


def test_supplement_stock_cash_no_invoice_and_retry(env):
    item, replacement, staff = setup_exchange(env)
    _, owner, bar, _, product, server, _, _ = env
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, "EXCH-CASH", 500)
    db.session.commit()
    assert item.supplement == 100
    assert qty(product) == qty(replacement) == 10
    assert db.session.query(Order).count() == 0
    duplicate = exchange_service.request(server, bar.id, "ECH-test", staff.id, product.id, replacement.id, 2, 2, "Changement client")
    assert duplicate.id == item.id
    exchange_service.post(cashier, bar.id, item.id, "100", True)
    db.session.commit()
    assert qty(product) == 12 and qty(replacement) == 8
    assert cash_service.expected(session) == 600
    assert item.cash_movement_id is not None
    exchange_service.post(cashier, bar.id, item.id, "100", True)
    db.session.commit()
    assert qty(product) == 12 and qty(replacement) == 8
    assert db.session.query(CashMovement).count() == 1
    assert db.session.query(Order).count() == 0
    from app.report_services import summary
    report = summary(owner, bar.id)
    assert Decimal(report['sales']['revenue']) == 100
    assert Decimal(report['payments']['net_received']) == 100
    assert Decimal(report['sales']['gross_margin_estimate']) == 60
    from app.cashier_web import _service_summary
    assert _service_summary(session)['gross_total'] == 100


def test_equal_price_needs_no_cash_session(env):
    item, replacement, _ = setup_exchange(env, price=100)
    exchange_service.post(env[1], env[2].id, item.id, "0", True)
    db.session.commit()
    assert item.cash_movement_id is None
    assert qty(env[4]) == 12 and qty(replacement) == 8


@pytest.mark.parametrize('received,checked,error', [('99', True, 'SUPPLEMENT_REQUIRED'), ('101', True, 'SUPPLEMENT_REQUIRED'), ('NaN', True, 'INVALID_NUMBER'), ('100', False, 'BOTTLES_CHECK_REQUIRED'), ('100', True, 'CASH_SESSION_NOT_OPEN')])
def test_validation_has_no_side_effects(env, received, checked, error):
    item, replacement, _ = setup_exchange(env)
    with pytest.raises(ValueError, match=error):
        exchange_service.post(env[1], env[2].id, item.id, received, checked)
    db.session.rollback()
    assert item.status == 'PENDING'
    assert qty(env[4]) == qty(replacement) == 10
    assert db.session.query(CashMovement).count() == 0


def test_insufficient_stock_rolls_back_return_and_cash(env):
    item, replacement, _ = setup_exchange(env, stock=1)
    cash_service.open(env[1], env[2].id, 'ROLLBACK', 0)
    db.session.commit()
    with pytest.raises(ValueError, match='INSUFFICIENT_STOCK'):
        exchange_service.post(env[1], env[2].id, item.id, 100, True)
    db.session.rollback()
    assert qty(env[4]) == 10 and qty(replacement) == 1
    assert item.status == 'PENDING'
    assert db.session.query(StockMovement).count() == 2
    assert db.session.query(CashMovement).count() == 0


def test_server_cannot_post_or_impersonate_and_other_bar_denied(env):
    item, replacement, staff = setup_exchange(env)
    with pytest.raises(PermissionError):
        exchange_service.post(env[5], env[2].id, item.id, 100, True)
    db.session.rollback()
    with pytest.raises(PermissionError):
        exchange_service.request(env[5], env[3].id, 'bad', staff.id, env[4].id, replacement.id, 1, 1, 'Bad')
    db.session.rollback()
    with pytest.raises(LookupError):
        exchange_service.post(env[3].owner, env[3].id, item.id, 100, True)
    db.session.rollback()
    user = User(email='second-server@example.invalid', display_name='Second', category='EMPLOYEE')
    user.set_password('test-password')
    db.session.add(user); db.session.flush()
    second = StaffAssignment(bar_id=env[2].id, user_id=user.id, role='SERVER', started_at=utcnow())
    db.session.add(second); db.session.commit()
    with pytest.raises(PermissionError):
        exchange_service.request(env[5], env[2].id, 'impersonation', second.id, env[4].id, replacement.id, 1, 1, 'Bad')
    db.session.rollback()
    with pytest.raises(PermissionError):
        exchange_service.cancel(user, env[2].id, item.id)
    db.session.rollback()
    client=env[0].test_client(); _login(client, user.email)
    page=client.get(f'/bars/{env[2].id}/exchanges')
    assert item.reference not in page.text


@pytest.mark.parametrize('quantity', ['0', '-1', '0.5', 'NaN', 'Infinity'])
def test_invalid_quantities(env, quantity):
    with pytest.raises(ValueError):
        setup_exchange(env, quantity=quantity)
    db.session.rollback()
    assert db.session.query(BeverageExchange).count() == 0


def test_cheaper_exchange_blocked(env):
    with pytest.raises(ValueError, match='CHEAPER_EXCHANGE_NOT_SUPPORTED'):
        setup_exchange(env, price=50)
    db.session.rollback()


def test_cancel_pending_and_prevent_late_approval(env):
    item, replacement, _ = setup_exchange(env)
    exchange_service.cancel(env[5], env[2].id, item.id)
    db.session.commit()
    with pytest.raises(ValueError, match='EXCHANGE_NOT_PENDING'):
        exchange_service.post(env[1], env[2].id, item.id, 100, True)
    db.session.rollback()
    assert qty(replacement) == 10


def test_web_request_validation_csrf_and_tenant(env):
    item, replacement, staff = setup_exchange(env)
    app, owner, bar, foreign, product, server, _, _ = env
    client = app.test_client()
    _login(client, server.email)
    url = f'/bars/{bar.id}/exchanges'
    page = client.get(url)
    assert page.status_code == 200 and item.reference in page.text
    assert 'Valider et encaisser' not in page.text
    assert client.post(url, data={'action':'post', 'exchange_id':item.id}).status_code == 400
    assert client.post(url, data={'csrf_token':_csrf(page), 'action':'post', 'exchange_id':item.id, 'amount_received':'100', 'bottles_checked':'yes'}).status_code == 403
    assert client.get(f'/bars/{foreign.id}/exchanges').status_code == 404
    response = client.post(url, data={'csrf_token':_csrf(page), 'action':'request', 'reference':'WEB-EXCHANGE',
        'returned_id':product.id, 'replacement_id':replacement.id, 'returned_quantity':'1', 'replacement_quantity':'1', 'reason':'Autre client'}, follow_redirects=True)
    assert response.status_code == 200 and 'WEB-EXCHANGE' in response.text
    cashier = _cashier(bar)
    session = cash_service.open(cashier, bar.id, 'WEB-CASH', 0); db.session.commit()
    from flask import g
    g.pop("_login_user", None)
    g.pop("csrf_token", None)
    client = app.test_client(); assert _login(client, cashier.email).status_code == 302
    page = client.get(url)
    assert 'Valider et encaisser 100' in page.text
    response = client.post(url, data={'csrf_token':_csrf(page), 'action':'post', 'exchange_id':item.id, 'amount_received':'100', 'bottles_checked':'yes'}, follow_redirects=True)
    assert response.status_code == 200
    db.session.refresh(item)
    assert item.status == 'POSTED', response.text
    assert cash_service.expected(session) == 100


def test_exchange_migration_matches_metadata_and_compiles_mysql(env):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import importlib.util
    import io
    from pathlib import Path
    with db.engine.connect() as connection:
        diffs = compare_metadata(MigrationContext.configure(connection), db.metadata)
        assert not [d for d in diffs if 'beverage_exchanges' in str(d)]
    path = Path('migrations/versions/c9d0e1f2a3b4_beverage_exchanges.py')
    spec = importlib.util.spec_from_file_location('exchange_migration', path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name='mysql', opts={'as_sql':True, 'output_buffer':output})
    with Operations.context(context):
        migration.upgrade()
    assert 'CREATE TABLE beverage_exchanges' in output.getvalue()
    assert 'DATETIME(6)' in output.getvalue()
