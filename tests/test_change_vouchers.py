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
