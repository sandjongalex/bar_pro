"""Inventory regressions against a migrated database, services and HTTP."""
from decimal import Decimal
import re
import pytest
from sqlalchemy import select
from test_workflows import env, balance
from app.extensions import db
from app.models import Inventory, InventoryLine, Product, StockMovement
from app.inventory_period_models import InventoryLineSnapshot
from app.inventory_services import inventory_service
from app.stock_service import stock_service
from app.auth import issue


def draft(env, products=None):
    inv = inventory_service.create(env[1], env[2].id, 'INV', products or [env[4].id], 'Physical count')
    db.session.commit()
    return inv


def base(env):
    return f'/api/v1/bars/{env[2].id}/inventories'


@pytest.mark.parametrize('quantity', ['0', '7.123456', '10', '12.5'])
def test_post_snapshot_difference_and_exactly_once(env, quantity):
    inv = draft(env)
    inventory_service.count(env[1], env[2].id, inv.id, {str(env[4].id): quantity})
    inventory_service.post(env[1], env[2].id, inv.id)
    db.session.commit()
    line = db.session.scalar(select(InventoryLine).where(InventoryLine.inventory_id == inv.id))
    assert balance(env) == Decimal(quantity)
    assert line.expected_quantity_snapshot == 10
    assert line.difference_quantity == Decimal(quantity) - 10
    moves = list(db.session.scalars(select(StockMovement).where(StockMovement.inventory_line_id == line.id)))
    assert len(moves) == (0 if quantity == '10' else 1)
    if moves:
        assert moves[0].quantity_delta == line.difference_quantity
    for action in (lambda: inventory_service.post(env[1], env[2].id, inv.id), lambda: inventory_service.count(env[1], env[2].id, inv.id, {env[4].id: 1}), lambda: inventory_service.cancel(env[1], env[2].id, inv.id)):
        with pytest.raises(ValueError):
            action()
    assert inv.posted_at is not None


@pytest.mark.parametrize('value', [None, [], 'bad', {}, {'reference': ''}, {'reference': 'A', 'product_ids': [{}]}, {'reference': 'A', 'product_ids': [True]}, {'reference': 'A', 'product_ids': [1.5]}, {'reference': 'A', 'product_ids': [1], 'reason': None}])
def test_invalid_create_body(env, value):
    response = env[0].test_client().post(base(env), headers=env[6], json=value)
    assert 400 <= response.status_code < 500
    assert response.json['success'] is False
    assert db.session.query(Inventory).count() == 0


@pytest.mark.parametrize('value', [None, [], '', {}, {'999999': 1}, {'1': -1}, {'1': 'NaN'}, {'1': 'Infinity'}, {'1': '0.0000001'}, {'1': True}])
def test_invalid_counts_do_not_modify_stock(env, value):
    inv = draft(env)
    response = env[0].test_client().patch(base(env) + f'/{inv.id}/counts', headers=env[6], json={'quantities': value})
    assert response.status_code == 422
    assert balance(env) == 10
    assert db.session.scalar(select(InventoryLine.counted_quantity)) is None


def test_duplicate_identifiers_reference_and_missing_product(env):
    client = env[0].test_client()
    for ids in ([env[4].id, str(env[4].id)], [env[4].id, '0'+str(env[4].id)]):
        assert client.post(base(env), headers=env[6], json={'reference': 'I', 'product_ids': ids}).status_code == 422
    assert client.post(base(env), headers=env[6], json={'reference': 'I', 'product_ids': [999999]}).status_code == 404
    draft(env)
    assert client.post(base(env), headers=env[6], json={'reference': 'INV', 'product_ids': [env[4].id]}).status_code == 409
    assert db.session.query(Inventory).count() == 1


def test_incomplete_stale_multiline_is_atomic(env):
    product = Product(bar_id=env[2].id, category_id=env[4].category_id, name='Second', sku='SECOND', base_unit='bottle', sale_price=100, valuation_unit_cost=40)
    db.session.add(product)
    db.session.commit()
    inv = draft(env, [env[4].id, product.id])
    client = env[0].test_client()
    path = base(env) + f'/{inv.id}'
    assert client.patch(path+'/counts', headers=env[6], json={'quantities': {str(env[4].id): 5}}).status_code == 200
    assert client.post(path+'/post', headers=env[6]).status_code == 409
    assert balance(env) == 10
    assert client.patch(path+'/counts', headers=env[6], json={'quantities': {str(product.id): 2}}).status_code == 200
    stock_service.move(env[1], env[2].id, product.id, 'INITIAL', 1, 'New delivery')
    db.session.commit()
    assert client.post(path+'/post', headers=env[6]).status_code == 409
    assert balance(env) == 10
    assert db.session.query(StockMovement).filter(StockMovement.inventory_line_id.is_not(None)).count() == 0
    assert db.session.get(Inventory, inv.id).status == 'DRAFT'


def test_api_read_cancel_permissions_and_scope(env):
    inv = draft(env)
    client = env[0].test_client()
    path = base(env) + f'/{inv.id}'
    assert client.get(path, headers=env[6]).json['data']['lines'][0]['product_name'] == 'Water'
    assert client.get(base(env), headers=env[6]).json['meta']['total'] == 1
    assert client.get(base(env)+'?page=2', headers=env[6]).json['data'] == []
    assert client.get(base(env)+'/999999', headers=env[6]).status_code == 404
    assert client.get(f'/api/v1/bars/{env[3].id}/inventories/{inv.id}', headers=env[6]).status_code == 404
    token, _ = issue(env[5], env[2].id)
    db.session.commit()
    assert client.post(path+'/cancel', headers={'Authorization': 'Bearer '+token}).status_code == 403
    env[2].status = 'SUSPENDED'
    db.session.commit()
    assert client.post(path+'/cancel', headers=env[6]).status_code == 403
    env[2].status = 'ACTIVE'
    db.session.commit()
    response = client.post(path+'/cancel', headers=env[6])
    assert response.status_code == 200
    assert response.json['data']['cancelled_at'] is not None
    assert client.post(path+'/post', headers=env[6]).status_code == 409
    assert client.patch(path+'/counts', headers=env[6], json={'quantities': {str(env[4].id): 2}}).status_code == 422
    assert balance(env) == 10


def test_web_create_count_post_print_with_csrf(env):
    client = env[0].test_client()
    csrf = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', client.get('/login').text).group(1)
    client.post('/login', data={'email': env[1].email, 'password': 'test-password', 'csrf_token': csrf})
    path = f'/bars/{env[2].id}/inventories'
    page = client.get(path+'/new')
    csrf = re.search('name="csrf_token" value="([^"]+)"', page.text).group(1)
    assert client.post(path+'/new', data={'reference': 'WEB'}).status_code == 400
    response = client.post(path+'/new', data={'csrf_token': csrf, 'reference': 'WEB', 'reason': 'Count', 'product_ids': str(env[4].id)})
    assert response.status_code == 302
    detail = response.headers['Location']
    assert client.post(detail, data={'csrf_token': csrf, 'action': 'counts', f'quantity_{env[4].id}': '8'}).status_code == 302
    assert client.post(detail, data={'csrf_token': csrf, 'action': 'post'}).status_code == 302
    assert balance(env) == 8
    page = client.get(detail)
    assert 'Validé' in page.text and 'Valider et ajuster' not in page.text
    assert db.session.get(Inventory, int(detail.rstrip('/').split('/')[-1])).status == 'POSTED'
    printed = client.get(detail+'/print')
    assert 'Water' in printed.text and '<td>-2</td>' in printed.text
    assert '-2.000000' not in printed.text  # quantity display omits database-scale zero padding


def test_foreign_inventory_and_product_references(env):
    from app.models import ProductCategory
    other = db.session.get(type(env[1]), env[3].owner_id)
    category = ProductCategory(bar_id=env[3].id, name='Foreign')
    db.session.add(category)
    db.session.flush()
    product = Product(bar_id=env[3].id, category_id=category.id, name='Foreign', sku='FOREIGN', base_unit='bottle', sale_price=1, valuation_unit_cost=1)
    db.session.add(product)
    db.session.flush()
    inv = inventory_service.create(other, env[3].id, 'FOREIGN', [product.id], 'Count')
    db.session.commit()
    client = env[0].test_client()
    path = base(env)+f'/{inv.id}'
    assert client.get(path, headers=env[6]).status_code == 404
    assert client.patch(path+'/counts', headers=env[6], json={'quantities': {str(product.id): 1}}).status_code == 404
    assert client.post(path+'/post', headers=env[6]).status_code == 404
    assert client.post(path+'/cancel', headers=env[6]).status_code == 404
    assert client.post(base(env), headers=env[6], json={'reference': 'BAD', 'product_ids': [product.id]}).status_code == 404
    assert db.session.query(Inventory).filter_by(bar_id=env[2].id).count() == 0


def test_failure_during_stock_adjustment_rolls_back_every_line(env, monkeypatch):
    product = Product(bar_id=env[2].id, category_id=env[4].category_id, name='Second', sku='SECOND', base_unit='bottle', sale_price=100, valuation_unit_cost=40)
    db.session.add(product)
    db.session.commit()
    inv = draft(env, [env[4].id, product.id])
    inventory_service.count(env[1], env[2].id, inv.id, {env[4].id: 7, product.id: 3})
    db.session.commit()
    original = stock_service.move
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(args[2])
        if len(calls) == 2:
            raise ValueError('Adjustment failed')
        return original(*args, **kwargs)
    monkeypatch.setattr(stock_service, 'move', fail_second)
    response = env[0].test_client().post(base(env)+f'/{inv.id}/post', headers=env[6])
    assert response.status_code == 409
    assert len(calls) == 2
    assert balance(env) == 10
    assert db.session.get(Inventory, inv.id).status == 'DRAFT'
    assert db.session.query(StockMovement).filter(StockMovement.inventory_line_id.is_not(None)).count() == 0


def test_non_sale_stock_movements_are_included_in_next_theoretical_inventory(env):
    first = draft(env)
    inventory_service.count(env[1], env[2].id, first.id, {str(env[4].id): "20"})
    inventory_service.post(env[1], env[2].id, first.id)
    db.session.commit()
    assert balance(env) == Decimal("20")

    # A manual correction, a recorded loss and a restocked return must all
    # change the next theoretical stock without being counted as sales.
    stock_service.move(env[1], env[2].id, env[4].id, "ADJUSTMENT", "-12", "Correction du stock initial")
    stock_service.move(env[1], env[2].id, env[4].id, "LOSS", "-1", "Bouteille cassée")
    stock_service.move(env[1], env[2].id, env[4].id, "RETURN", "1", "Retour client remis en stock")
    db.session.commit()
    assert balance(env) == Decimal("8")

    next_inventory = inventory_service.create(
        env[1], env[2].id, "INV-NEXT", [env[4].id], "Physical count"
    )
    db.session.commit()
    line = db.session.scalar(
        select(InventoryLine).where(InventoryLine.inventory_id == next_inventory.id)
    )
    snapshot = db.session.scalar(
        select(InventoryLineSnapshot).where(
            InventoryLineSnapshot.inventory_line_id == line.id
        )
    )

    # Preserve the actual last physical count, while showing the correction
    # separately and using its net effect in the theoretical stock.
    assert snapshot.opening_quantity == Decimal("20")
    assert snapshot.other_movement_quantity == Decimal("-12")
    assert snapshot.theoretical_quantity == Decimal("8")
    assert line.expected_quantity_snapshot == Decimal("8")

    inventory_service.count(
        env[1], env[2].id, next_inventory.id, {str(env[4].id): "7"}
    )
    db.session.commit()
    assert snapshot.theoretical_sold_quantity == Decimal("1")
    assert snapshot.theoretical_sales_amount == Decimal("100")


def test_inventory_movement_origin_explains_signed_net_and_is_bar_scoped(env):
    # This is the same signed net shown next to "Mouvements hors ventes".
    stock_service.move(env[1], env[2].id, env[4].id, 'ADJUSTMENT', 2, 'Ajout corrigé')
    stock_service.move(env[1], env[2].id, env[4].id, 'LOSS', -1, 'Bouteille cassée')
    db.session.commit()
    inv = draft(env)
    client = env[0].test_client()
    csrf = re.search(r'name=["\\']csrf_token["\\']\\s+value=["\\']([^"\\']+)["\\']', client.get('/login').text).group(1)
    assert client.post('/login', data={
        'email': env[1].email, 'password': 'test-password', 'csrf_token': csrf,
    }).status_code == 302

    url = f'/bars/{env[2].id}/inventories/{inv.id}/movements/{env[4].id}'
    response = client.get(url)
    assert response.status_code == 200
    data = response.json['data']
    assert data['product_name'] == env[4].name
    assert Decimal(data['snapshot_total']) == Decimal('1')
    assert Decimal(data['journal_total']) == Decimal('1')
    assert data['matches_snapshot'] is True
    assert {entry['type'] for entry in data['movements']} == {'Ajustement manuel', 'Perte / casse'}
    assert sum(Decimal(entry['quantity']) for entry in data['movements']) == Decimal('1')
    assert {entry['reason'] for entry in data['movements']} == {'Ajout corrigé', 'Bouteille cassée'}
    assert all(entry['author'] and entry['date'] for entry in data['movements'])
    assert client.get(url.replace(f'/bars/{env[2].id}', f'/bars/{env[3].id}')).status_code in (403, 404)
    assert client.get(url.replace(f'/movements/{env[4].id}', '/movements/999999')).status_code == 404


def test_inventory_movement_origin_requires_authenticated_session(env):
    inv = draft(env)
    response = env[0].test_client().get(
        f'/bars/{env[2].id}/inventories/{inv.id}/movements/{env[4].id}'
    )
    assert response.status_code in (302, 401, 403)
