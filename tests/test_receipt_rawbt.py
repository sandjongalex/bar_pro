import base64
import html
import re
from decimal import Decimal
from types import SimpleNamespace

from app.extensions import db
from app.receipt_printing import cumulative_receipt_lines, number, printable, receipt_columns
from test_cashier_complete_flow import _login
from test_workflows import env, order


def _decode_payload(link):
    link = html.unescape(link)
    encoded, suffix = link.removeprefix('intent:base64,').split('#Intent;')
    assert suffix == 'scheme=rawbt;package=ru.a402d.rawbtprinter;end;'
    payload = base64.b64decode(encoded)
    assert payload.startswith(b'\x1b@\x1b!\x00\x1ba\x00')
    return payload


def _decode_intent(link):
    # Strip ONLY the documented ESC/POS styling/line-spacing commands.
    # Unexpected control bytes still cause the injection assertion below to fail.
    payload = _decode_payload(link)
    plain = re.sub(
        rb'\x1bE[\x00\x01]|\x1d![\x00\x01\x11]|\x1b3[\x20-\x40]|\x1b2',
        b'', payload[8:],
    )
    assert b'\x1b' not in plain and b'\x1d' not in plain
    return plain.decode('ascii')


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

    links = re.findall(r'data-rawbt-intent="([^"]+)"', page.text)
    assert len(links) == 2
    tickets = [_decode_intent(link) for link in links]
    ticket = tickets[0]

    assert 'Client: Zaza' in ticket
    assert 'Elodie' in ticket
    assert 'A PAYER: 200 FCFA' in ticket
    assert ticket.count('A PAYER') == 1
    assert 'RECU DE VENTE' not in ticket
    assert 'Regle / finance' not in ticket
    assert 'Commande initiale' not in ticket
    assert 'Sans serveuse' not in ticket
    assert 'Edite le' not in ticket
    assert all(len(line) <= 32 for line in ticket.splitlines())
    assert '\x1b' not in ticket
    assert all('FACTURE CUMULEE' not in item and 'FACTURE DETAILLEE' not in item for item in tickets)
    assert 'Facture cumulée' in page.text
    assert 'Facture détaillée' in page.text
    assert 'data-receipt-mode="cumulative"' in page.text
    assert 'data-receipt-mode="detailed"' in page.text
    assert 'receipt_print.js' in page.text
    assert 'width: 58mm' in page.text
    assert 'font-size: 13px;' in page.text  # Improved 58mm item text
    assert b'\x1d!\x01' in _decode_payload(links[0])  # Double-height products
    assert value.payment_status == 'UNPAID'


def test_cumulative_receipt_groups_same_product_only_at_same_price():
    lines = [
        SimpleNamespace(
            product_id=10,
            product_name_snapshot='33 Export',
            unit_snapshot='bottle',
            quantity=Decimal('2'),
            unit_sale_price_snapshot=Decimal('800'),
            total_amount=Decimal('1600'),
        ),
        SimpleNamespace(
            product_id=10,
            product_name_snapshot='33 Export',
            unit_snapshot='bottle',
            quantity=Decimal('3'),
            unit_sale_price_snapshot=Decimal('800'),
            total_amount=Decimal('2400'),
        ),
        SimpleNamespace(
            product_id=10,
            product_name_snapshot='33 Export',
            unit_snapshot='bottle',
            quantity=Decimal('1'),
            unit_sale_price_snapshot=Decimal('900'),
            total_amount=Decimal('900'),
        ),
    ]

    cumulative = cumulative_receipt_lines(lines)

    assert len(cumulative) == 2
    assert cumulative[0].product_name_snapshot == '33 Export'
    assert cumulative[0].quantity == Decimal('5')
    assert cumulative[0].unit_sale_price_snapshot == Decimal('800')
    assert cumulative[0].total_amount == Decimal('4000')
    assert cumulative[1].quantity == Decimal('1')
    assert cumulative[1].unit_sale_price_snapshot == Decimal('900')
    assert cumulative[1].total_amount == Decimal('900')


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


def test_compact_receipt_reference_and_no_duplicate_address():
    from types import SimpleNamespace
    from app.receipt_printing import compact_order_reference, receipt_address

    assert compact_order_reference("CMD-20261107-022506-E85E") == "022506-E85E"
    assert compact_order_reference("SERVER-PREVIEW") == "SERVER-PREVIEW"
    assert receipt_address(SimpleNamespace(
        name="Terasse sous manguier", address="Sous manguier",
    )) is None
    assert receipt_address(SimpleNamespace(
        name="Terasse sous manguier", address="Carrefour Essomba",
    )) == "Carrefour Essomba"


def test_80mm_rawbt_and_browser_receipt_use_bar_setting(env):
    app, owner, bar, foreign, product, _, _, _ = env
    long_name = "TERASSE SOUS MANGUIER LARGE FORMAT 80MM"
    bar.name = long_name
    bar.printer_width_mm = 80
    assert foreign.printer_width_mm == 58
    value = order(env)
    db.session.commit()
    client = app.test_client()
    _login(client, owner.email)
    page = client.get(f"/bars/{bar.id}/checkout/orders/{value.id}/receipt")
    assert page.status_code == 200
    assert "width: 80mm" in page.text
    assert "size: 80mm auto" in page.text
    assert "font-size: 14px;" in page.text  # Readable 80mm product names
    assert "font-size: 20px;" in page.text  # Enlarged total
    links = re.findall(r'data-rawbt-intent="([^"]+)"', page.text)
    assert len(links) == 2
    ticket = _decode_intent(links[0])
    assert long_name in ticket.splitlines()
    assert "ARTICLE" in ticket and "QTE" in ticket and "TOTAL" in ticket
    assert b'\x1d!\x11' in _decode_payload(links[0])  # Enlarged totals/status
    assert all(len(line) <= 48 for line in ticket.splitlines())
    assert receipt_columns(bar) == 48
    assert receipt_columns(foreign) == 32


def test_escpos_title_total_and_wide_item_fallback():
    from app.receipt_printing import PRINT_STYLES, _rawbt_receipt_intent
    from types import SimpleNamespace

    bar = SimpleNamespace(name="Terasse sous manguier", address=None,
                          phone=None, printer_width_mm=58)
    item = SimpleNamespace(product_name_snapshot="Biere avec un nom tres tres long",
                           quantity=Decimal("2"), unit_sale_price_snapshot=Decimal("900"),
                           total_amount=Decimal("1800"))
    bill = SimpleNamespace(reference="CMD-20261107-022506-E85E",
                           currency="XAF", table_label_snapshot=None,
                           total_amount=Decimal("1800"), status="CONFIRMED")
    balance = {"return_credit": Decimal(0), "amount_due": Decimal(1800),
               "net_settled": Decimal(0), "net_sale": Decimal(1800),
               "customer_credit": Decimal(0)}
    def intent_for(width):
        bar.printer_width_mm = width
        return _rawbt_receipt_intent(
            bar=bar, order=bill, lines=[item], payments=[], refunds=[],
            balance=balance, server_name=None, customer_name=None,
            payment_times={}, refund_times={}, payment_labels={},
            sale_time="07/11/2026 02:45", issued_at=None,
            mode_label="FACTURE DETAILLEE",
        )

    for paper, capacity in ((58, 32), (80, 48)):
        encoded = intent_for(paper)
        payload = _decode_payload(encoded)
        lines = _decode_intent(encoded).splitlines()
        assert all(len(line) <= capacity for line in lines)
        assert any("Biere avec un nom" in line for line in lines)
        assert "A PAYER: 1,800 FCFA" in "\n".join(lines)
        assert PRINT_STYLES["tall"] in payload
        assert b"\x1b3\x36" in payload  # sufficient line spacing for larger text
        assert b"\x1b2" in payload  # reset line spacing after the receipt
