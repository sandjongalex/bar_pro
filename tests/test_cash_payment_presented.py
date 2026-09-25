from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, func

from app.cash_services import cash_service
from app.extensions import db
from app.finance_totals import order_balance
from app.models import CashMovement, Payment
from app.order_services import order_service
from app.payment_services import payment_service
from test_workflows import env


def test_cash_payment_records_presented_applied_and_change(env):
    _, owner, bar, _, product, _, _, _ = env
    product.sale_price = Decimal("5600")
    db.session.commit()

    order = order_service.create(
        owner,
        bar.id,
        "CASH-PRESENTED-CHANGE",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, order.id)
    session = cash_service.open(owner, bar.id, "CASH-PRESENTED-SESSION", 0)
    payment = payment_service.record(
        owner,
        bar.id,
        order.id,
        "CASH-PRESENTED-PAYMENT",
        "CASH",
        10000,
        5600,
        4400,
        cash_session_id=session.id,
    )
    db.session.commit()

    assert Decimal(payment.amount_presented) == Decimal("10000")
    assert Decimal(payment.amount_applied) == Decimal("5600")
    assert Decimal(payment.change_given) == Decimal("4400")
    assert order.payment_status == "PAID"
    # The drawer receives the actual sale, not the gross note handed over.
    drawer_delta = db.session.scalar(
        select(func.sum(CashMovement.amount_delta)).where(
            CashMovement.bar_id == bar.id,
            CashMovement.payment_id == payment.id,
        )
    )
    assert Decimal(drawer_delta) == Decimal("5600")


def test_cash_payment_can_be_partial_when_client_gives_less_than_due(env):
    _, owner, bar, _, product, _, _, _ = env
    product.sale_price = Decimal("8000")
    db.session.commit()

    order = order_service.create(
        owner,
        bar.id,
        "CASH-PRESENTED-PARTIAL",
        [{"product_id": product.id, "quantity": 1}],
    )
    order_service.confirm(owner, bar.id, order.id)
    session = cash_service.open(owner, bar.id, "CASH-PARTIAL-SESSION", 0)
    payment_service.record(
        owner,
        bar.id,
        order.id,
        "CASH-PARTIAL-PAYMENT",
        "CASH",
        5000,
        5000,
        0,
        cash_session_id=session.id,
    )
    db.session.commit()

    assert order.payment_status == "PARTIAL"
    assert Decimal(order_balance(order)["amount_due"]) == Decimal("3000")


def test_cashier_ui_derives_applied_amount_from_money_given():
    script = Path("app/static/cash_payment_ui.js").read_text(encoding="utf-8")
    layout = Path("app/templates/layout.html").read_text(encoding="utf-8")

    assert "Montant donné par le client" in script
    assert "Paiement partiel" in script
    assert "Monnaie à rendre" in script
    assert "Math.min(received, due)" in script
    assert "cash-presented-quick" in script
    assert "cash_payment_ui.js" in layout


def test_cashier_money_given_field_starts_empty():
    script = Path("app/static/cash_payment_ui.js").read_text(encoding="utf-8")

    assert "presentedInput.value = '';" in script
    assert "presentedInput.setAttribute('value', '');" in script
    assert "Saisir le montant" in script
    assert "presentedInput.value = String(due)" not in script
