import re

from sqlalchemy import select

from app.extensions import db
from app.inventory_services import inventory_service
from app.models import InventoryLine
from test_workflows import balance, env


def _csrf_from(page):
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email):
    page = client.get('/login')
    return client.post(
        '/login',
        data={
            'email': email,
            'password': 'test-password',
            'csrf_token': _csrf_from(page),
        },
        follow_redirects=False,
    )


def test_blank_physical_stock_is_saved_and_posted_as_zero(env):
    app, owner, bar, _, product, _, _, _ = env
    inventory = inventory_service.create(
        owner,
        bar.id,
        'INV-BLANK-ZERO',
        [product.id],
        'Physical count',
    )
    db.session.commit()

    client = app.test_client()
    assert _login(client, owner.email).status_code == 302
    detail_path = f'/bars/{bar.id}/inventories/{inventory.id}'

    page = client.get(detail_path)
    assert page.status_code == 200
    assert 'laissée vide sera considérée comme' in page.text
    assert '0 bouteille' in page.text

    response = client.post(
        detail_path,
        data={
            'csrf_token': _csrf_from(page),
            'action': 'counts',
            f'quantity_{product.id}': '',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302

    line = db.session.scalar(
        select(InventoryLine).where(
            InventoryLine.inventory_id == inventory.id,
            InventoryLine.product_id == product.id,
        )
    )
    assert line.counted_quantity == 0

    page = client.get(detail_path)
    response = client.post(
        detail_path,
        data={
            'csrf_token': _csrf_from(page),
            'action': 'post',
            f'quantity_{product.id}': '',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    db.session.refresh(inventory)
    assert inventory.status == 'POSTED'
    assert balance(env) == 0
