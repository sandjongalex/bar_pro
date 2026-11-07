from decimal import Decimal
from pathlib import Path

import pytest

from app.cash_services import cash_service
from app.change_voucher_models import ChangeVoucher, ChangeVoucherTransaction
from app.change_voucher_service import change_voucher_service
from app.extensions import db
from app.finance_totals import order_balance
from app.order_services import order_service
from app.payment_services import payment_service
from test_workflows import env


def _issue_voucher(owner, bar, product, session, *, sale_price="5600"):
    product.sale_price = Decimal(sale_price)
    db.session.commit()
    order = order_service.create(
        owner,
        bar.id,
        f"VOUCHER-ORIGIN-{sale_price}",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, order.id)
    payment = payment_service.record(
        owner,
        bar.id,
        order.id,
        f"VOUCHER-PAYMENT-{sale_price}",
        "CASH",
        10000,
        Decimal(sale_price),
        3000,
        cash_session_id=session.id,
        voucher_amount=Decimal("1400") if sale_price == "5600" else Decimal("10000") - Decimal(sale_price) - Decimal("3000"),
    )
    voucher_amount = Decimal(payment.amount_presented) - Decimal(payment.amount_applied) - Decimal(payment.change_given)
    voucher = change_voucher_service.issue(owner, bar.id, payment.id, voucher_amount)
    db.session.commit()
    return order, payment, voucher


def test_unreturned_change_becomes_liability_and_physical_cash(env):
    _, owner, bar, bar2, product, _, _, _ = env
    session = cash_service.open(owner, bar.id, "CHANGE-VOUCHER-CASH", 0)
    db.session.commit()

    order, payment, voucher = _issue_voucher(owner, bar, product, session)

    assert Decimal(payment.amount_presented) == Decimal("10000")
    assert Decimal(payment.amount_applied) == Decimal("5600")
    assert Decimal(payment.change_given) == Decimal("3000")
    assert Decimal(voucher.initial_amount) == Decimal("1400")
    assert Decimal(voucher.balance_amount) == Decimal("1400")
    assert voucher.status == "ACTIVE"
    assert voucher.origin_order_id == order.id
    assert order.payment_status == "PAID"

    # Physical cash is 10,000 received - 3,000 actually returned = 7,000.
    # Only 5,600 is sale revenue; 1,400 remains a customer liability.
    assert Decimal(cash_service.expected(session)) == Decimal("7000")
    balance = order_balance(order)
    assert Decimal(balance["net_paid"]) == Decimal("5600")
    assert Decimal(balance["change_voucher_credit"]) == Decimal("0")

    # Voucher codes are strictly tenant-scoped.
    with pytest.raises(LookupError):
        change_voucher_service.by_code(bar2.id, voucher.code)


def test_voucher_can_partially_pay_invoice_then_refund_remaining_cash(env):
    _, owner, bar, _, product, _, _, _ = env
    session = cash_service.open(owner, bar.id, "CHANGE-VOUCHER-REDEEM", 0)
    db.session.commit()
    _, _, voucher = _issue_voucher(owner, bar, product, session)

    product.sale_price = Decimal("4000")
    db.session.commit()
    target = order_service.create(
        owner,
        bar.id,
        "VOUCHER-TARGET",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, target.id)
    db.session.commit()

    before = Decimal(cash_service.expected(session))
    tx = change_voucher_service.redeem(owner, bar.id, voucher.code, target.id, Decimal("1000"))
    db.session.commit()

    assert tx.kind == "REDEEM"
    assert Decimal(tx.amount) == Decimal("1000")
    assert Decimal(voucher.balance_amount) == Decimal("400")
    assert voucher.status == "PARTIAL"
    assert target.payment_status == "PARTIAL"
    assert Decimal(order_balance(target)["amount_due"]) == Decimal("3000")
    assert Decimal(order_balance(target)["change_voucher_credit"]) == Decimal("1000")
    # Applying a voucher to an invoice does not move physical cash.
    assert Decimal(cash_service.expected(session)) == before

    refund = change_voucher_service.refund_cash(owner, bar.id, voucher.code, session.id, Decimal("400"))
    db.session.commit()
    assert refund.kind == "CASH_REFUND"
    assert Decimal(voucher.balance_amount) == Decimal("0")
    assert voucher.status == "SETTLED"
    assert voucher.settled_at is not None
    assert Decimal(cash_service.expected(session)) == before - Decimal("400")


def test_voucher_amount_cannot_exceed_balance_or_invoice_due(env):
    _, owner, bar, _, product, _, _, _ = env
    session = cash_service.open(owner, bar.id, "CHANGE-VOUCHER-LIMIT", 0)
    db.session.commit()
    _, _, voucher = _issue_voucher(owner, bar, product, session)

    product.sale_price = Decimal("500")
    db.session.commit()
    target = order_service.create(
        owner,
        bar.id,
        "VOUCHER-SMALL-TARGET",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, target.id)
    db.session.commit()

    with pytest.raises(ValueError, match="VOUCHER_AMOUNT_EXCEEDED"):
        change_voucher_service.redeem(owner, bar.id, voucher.code, target.id, Decimal("600"))
    db.session.rollback()

    assert Decimal(voucher.balance_amount) == Decimal("1400")
    assert Decimal(order_balance(target)["amount_due"]) == Decimal("500")


def test_change_voucher_ui_and_registration_are_present():
    script = Path("app/static/change_voucher_ui.js").read_text(encoding="utf-8")
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")
    app_factory = Path("app/__init__.py").read_text(encoding="utf-8")
    template = Path("app/templates/change_vouchers.html").read_text(encoding="utf-8")

    assert "actual_change_given" in script
    assert "Bon de monnaie à créer" in script
    assert "change-vouchers/pay" not in script  # URL is assembled from the cashier bar base.
    assert "`${base}pay`" in script
    assert "change_voucher_ui.js" in layout
    assert "change_vouchers_web_bp" in app_factory
    assert "Utiliser un bon sur une facture" in template
    assert "Rembourser un bon en espèces" in template


def test_voucher_print_uses_selected_bar_paper_width(env):
    app, owner, bar, _, product, *_ = env
    session = cash_service.open(owner, bar.id, "CHANGE-VOUCHER-80", 0)
    db.session.commit()
    _, _, voucher = _issue_voucher(owner, bar, product, session)
    bar.printer_width_mm = 80
    db.session.commit()

    from test_cashier_complete_flow import _login
    client = app.test_client()
    _login(client, owner.email)
    response = client.get(f"/bars/{bar.id}/cashier/change-vouchers/{voucher.id}/print")
    assert response.status_code == 200
    assert "width:80mm" in response.text
    assert "size:80mm auto" in response.text



def test_voucher_rawbt_58_80_and_outstanding_owner_totals(env):
    import base64
    import html
    import re

    from app.change_voucher_printing import rawbt_change_voucher_intent
    from app.change_voucher_service import change_voucher_liability
    from test_cashier_complete_flow import _login

    app, owner, bar, foreign, product, _, _, _ = env
    session = cash_service.open(owner, bar.id, "VOUCHER-RAWBT-PAPER", 0)
    db.session.commit()
    _, _, voucher = _issue_voucher(owner, bar, product, session)
    assert change_voucher_liability(bar.id) == {"count": 1, "amount": Decimal("1400")}
    assert change_voucher_liability(foreign.id) == {"count": 0, "amount": Decimal("0")}

    for paper, width in ((58, 32), (80, 48)):
        bar.printer_width_mm = paper
        intent = rawbt_change_voucher_intent(bar, voucher, owner)
        encoded, suffix = html.unescape(intent).removeprefix("intent:base64,").split("#Intent;")
        assert suffix == "scheme=rawbt;package=ru.a402d.rawbtprinter;end;"
        payload = base64.b64decode(encoded)
        assert payload.startswith(b"\x1b@\x1b!\x00\x1ba\x00")
        clean = re.sub(
            rb"\x1bE[\x00\x01]|\x1d![\x00\x01\x11]|\x1b3[\x20-\x40]|\x1b2",
            b"", payload[8:]
        )
        text = clean.decode("ascii")
        assert "BON DE MONNAIE" in text
        assert voucher.code in text
        assert "SOLDE: 1,400 XAF" in text
        assert all(len(line) <= width for line in text.splitlines())
        db.session.commit()
        client = app.test_client()
        _login(client, owner.email)
        response = client.get(f"/bars/{bar.id}/cashier/change-vouchers/{voucher.id}/print")
        # Owner is allowed to reprint their own bar's liability receipt.
        assert response.status_code == 200
        assert 'data-rawbt-intent="intent:base64,' in response.text
        page = client.get(f"/dashboard?bar_id={bar.id}")
        assert page.status_code == 200
        assert "Bons de monnaie non remboursés" in page.text

    remaining = change_voucher_liability(bar.id)
    assert remaining["amount"] == Decimal("1400")
    change_voucher_service.refund_cash(owner, bar.id, voucher.code, session.id, Decimal("400"))
    db.session.commit()
    assert change_voucher_liability(bar.id)["amount"] == Decimal("1000")
