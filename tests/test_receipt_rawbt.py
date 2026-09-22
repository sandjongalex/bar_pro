import base64
import html
import re
from decimal import Decimal

from app.extensions import db
from app.receipt_printing import number, printable
from test_cashier_complete_flow import _login
from test_workflows import env, order


def test_rawbt_ticket_uses_authorized_receipt_data_and_32_columns(env):
    app, owner, bar, _, product, _, _, _ = env
    value = order(env)
    value.customer_name_snapshot = 'Zaza #Intent;end; Élodie'
    value.notes = 'Note longue ' * 30 + '\x1b@'
    db.session.commit()
    client = app.test_client()
    _login(client, owner.email)
    page = client.get(f'/bars/{bar.id}/checkout/orders/{value.id}/receipt')
    assert page.status_code == 200
    link = html.unescape(re.search(r'data-rawbt-intent="([^"]+)"', page.text).group(1))
    encoded, suffix = link.removeprefix('intent:base64,').split('#Intent;')
    assert suffix == 'scheme=rawbt;package=ru.a402d.rawbtprinter;end;'
    payload = base64.b64decode(encoded)
    assert payload.startswith(b'\x1b@\x1b!\x00\x1ba\x00')
    ticket = payload[8:].decode('ascii')
    assert 'Client: Zaza' in ticket
    assert 'Elodie' in ticket
    assert 'RESTE A PAYER: 200 FCFA' in ticket
    assert 'A PAYER' in ticket
    assert all(len(line) <= 32 for line in ticket.splitlines())
    assert '\x1b' not in ticket
    assert 'receipt_print.js' in page.text
    assert 'width: 58mm' in page.text
    assert value.payment_status == 'UNPAID'


def test_receipt_rawbt_keeps_tenant_and_login_protection(env):
    app, owner, bar, foreign, _, _, _, _ = env
    value = order(env)
    client = app.test_client()
    url = f'/bars/{bar.id}/checkout/orders/{value.id}/receipt'
    assert client.get(url).status_code == 401
    _login(client, owner.email)
    assert client.get(f'/bars/{foreign.id}/checkout/orders/{value.id}/receipt').status_code in (403, 404)


def test_print_numbers_preserve_fractional_values_and_strip_controls():
    assert number(Decimal('100.00')) == '100'
    assert number(Decimal('1000.50')) == '1,000.5'
    assert number(Decimal('0.125')) == '0.125'
    assert printable('Élodie\x1b@\n33 Export') == 'Elodie @ 33 Export'
