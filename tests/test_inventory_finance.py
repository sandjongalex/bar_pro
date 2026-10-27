"""Inventory finance integration tests: no fabricated cash and no double credits."""
from decimal import Decimal

import pytest
from sqlalchemy import select

from test_workflows import env, balance, order
from app.cash_services import cash_service
from app.customer_services import customer_service
from app.expense_services import expense_service
from app.extensions import db
from app.inventory_services import inventory_service
from app.inventory_finance_models import InventoryCashReconciliation, InventoryDebtRequest
from app.inventory_finance_services import (
    view, count_cash, add_expense, request_debt, decide_debt,
)
from app.models import CashMovement, CashSession, Customer, Expense, ExpenseCategory, Order


def setup_inventory(env, reference="FINANCE"):
    inv = inventory_service.create(env[1], env[2].id, reference,
                                   [env[4].id], "Rapprochement comptable")
    db.session.commit()
    return inv


def drawer(env, opening=1000):
    session = cash_service.open(env[1], env[2].id, "SESSION-TEST", opening)
    db.session.commit()
    return session


def customer(env):
    item = Customer(bar_id=env[2].id, display_name="Client Dupont")
    db.session.add(item)
    db.session.commit()
    return item


def test_count_uses_real_drawer_and_preserves_stock(env):
    inv = setup_inventory(env)
    session = drawer(env)
    with pytest.raises(ValueError, match="INVENTORY_CASH_REASON_REQUIRED"):
        count_cash(env[1], env[2].id, inv.id, "900")
    db.session.rollback()
    saved = count_cash(env[1], env[2].id, inv.id, "900",
                       "10 billets retrouvés en moins")
    db.session.commit()
    assert saved.expected_amount == Decimal("1000")
    assert saved.counted_amount == Decimal("900")
    assert saved.difference_amount == Decimal("-100")
    assert balance(env) == 10
    assert db.session.query(CashMovement).count() == 0
    assert view(env[1], env[2].id, inv.id)["changed_since_count"] is False
    cash_service.movement(env[1], env[2].id, session.id,
                          "DEPOSIT", "100", "Vente espèces tardive")
    db.session.commit()
    assert view(env[1], env[2].id, inv.id)["changed_since_count"] is True
    saved = count_cash(env[1], env[2].id, inv.id, "1100")
    db.session.commit()
    assert saved.expected_amount == saved.counted_amount == Decimal("1100")
    assert saved.difference_amount == 0
    assert db.session.query(InventoryCashReconciliation).count() == 1


def test_late_expense_posts_once_to_cash_session(env):
    inv = setup_inventory(env)
    session = drawer(env)
    category = ExpenseCategory(bar_id=env[2].id, name="Nettoyage")
    db.session.add(category)
    db.session.commit()
    expense = add_expense(env[1], env[2].id, inv.id,
                          category_id=category.id, amount=150,
                          description="Nettoyage oublié", method="CASH",
                          nonce="expenseOnce123")
    db.session.commit()
    assert expense.cash_session_id == session.id
    assert cash_service.expected(session) == Decimal("850")
    assert balance(env) == 10
    with pytest.raises(ValueError, match="INVENTORY_EXPENSE_ALREADY_RECORDED"):
        add_expense(env[1], env[2].id, inv.id,
                    category_id=category.id, amount=150,
                    description="Doublon", method="CASH", nonce="expenseOnce123")
    db.session.rollback()
    assert db.session.query(Expense).count() == 1
    assert db.session.query(CashMovement).count() == 1


def test_mobile_expense_not_removed_from_drawer(env):
    inv = setup_inventory(env)
    drawer(env)
    category = ExpenseCategory(bar_id=env[2].id, name="Téléphone")
    db.session.add(category)
    db.session.commit()
    add_expense(env[1], env[2].id, inv.id, category_id=category.id,
                amount="200", description="Internet", method="MOBILE_MONEY",
                nonce="mobileExpense456")
    db.session.commit()
    assert db.session.query(Expense).count() == 1
    assert db.session.query(CashMovement).count() == 0


def test_manual_debt_request_owner_only_no_cash_or_stock(env):
    inv = setup_inventory(env)
    session = drawer(env)
    cust = customer(env)
    req = request_debt(env[1], env[2].id, inv.id, customer_id=cust.id,
                       order_id=None, amount=300, reason="Ancien crédit client")
    db.session.commit()
    assert req.status == "PENDING"
    with pytest.raises(ValueError, match="INVENTORY_FINANCE_DEBTS_PENDING"):
        inventory_service.post(env[1], env[2].id, inv.id)
    db.session.rollback()
    assert balance(env) == 10
    answer = decide_debt(env[1], env[2].id, inv.id, req.id,
                         decision="approve", reason="Dette contrôlée")
    db.session.commit()
    assert answer.status == "APPROVED"
    assert answer.ledger_entry_id is not None
    assert customer_service.manual_debt_balance(env[2].id, cust.id) == 300
    assert cash_service.expected(session) == 1000
    assert db.session.query(CashMovement).count() == 0
    with pytest.raises(ValueError, match="INVENTORY_DEBT_ALREADY_DECIDED"):
        decide_debt(env[1], env[2].id, inv.id, req.id,
                    decision="approve", reason="Doublon")
    db.session.rollback()


def test_invoice_credit_must_equal_actual_unpaid_balance(env):
    sale = order(env, quantity=2)
    env[2].credit_sales_enabled = True
    db.session.commit()
    inv = setup_inventory(env)
    drawer(env)
    cust = customer(env)
    req = request_debt(env[1], env[2].id, inv.id, customer_id=cust.id,
                       order_id=sale.id, amount=100, reason="Deux bouteilles")
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_DEBT_AMOUNT_MISMATCH"):
        decide_debt(env[1], env[2].id, inv.id, req.id,
                    decision="approve", reason="Vente contrôlée")
    db.session.rollback()
    assert req.status == "PENDING"
    assert sale.customer_id is None
    assert cash_service.expected(db.session.scalar(select(CashSession).where(
        CashSession.bar_id == env[2].id))) == 1000


def test_no_financial_writes_after_stock_post_and_scoping(env):
    inv = setup_inventory(env)
    session = drawer(env)
    with pytest.raises(PermissionError):
        count_cash(env[7] if False else env[1], env[3].id, inv.id, "1000")
    db.session.rollback()
    inventory_service.count(env[1], env[2].id, inv.id,
                            {str(env[4].id): "10"})
    inventory_service.post(env[1], env[2].id, inv.id)
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_IMMUTABLE"):
        count_cash(env[1], env[2].id, inv.id, "1000")
    db.session.rollback()
    assert db.session.query(InventoryCashReconciliation).count() == 0


def test_html_inventory_views_show_financial_panel_and_csrf(env):
    inv = setup_inventory(env)
    drawer(env)
    client = env[0].test_client()
    import re
    text = client.get("/login").text
    csrf = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', text)
    assert csrf
    assert client.post("/login", data={
        "email": env[1].email, "password": "test-password",
        "csrf_token": csrf.group(1),
    }).status_code == 302
    response = client.get(f"/bars/{env[2].id}/inventories/{inv.id}")
    assert response.status_code == 200, response.text
    assert "Rapprochement financier" in response.text
    assert "Recette physique en espèces" in response.text
    assert "Ajouter une dépense oubliée" in response.text
    assert "Signaler une dette oubliée" in response.text
    assert "Rapprochement financier" in response.text


def test_duplicate_debt_request_fails_closed(env):
    inv = setup_inventory(env)
    cust = customer(env)
    args = dict(customer_id=cust.id, order_id=None,
                amount="350", reason="Dette oubliée",
                nonce="sameFormNonce1234")
    request_debt(env[1], env[2].id, inv.id, **args)
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_DEBT_DUPLICATE"):
        request_debt(env[1], env[2].id, inv.id, **args)
    db.session.rollback()
    assert db.session.query(InventoryDebtRequest).count() == 1


def test_assigned_cashier_can_report_debt_but_owner_must_decide(env):
    from test_inventory_v2_approval import make_count
    cashier, inv = make_count(env, reference="FINANCE-V2-CASHIER",
                              quantity="10")
    client_record = customer(env)
    debt = request_debt(cashier, env[2].id, inv.id,
                        customer_id=client_record.id, order_id=None,
                        amount="450", reason="Client doit régler demain")
    db.session.commit()
    with pytest.raises(PermissionError):
        decide_debt(cashier, env[2].id, inv.id, debt.id,
                    decision="approve", reason="Non autorisé")
    db.session.rollback()
    assert debt.status == "PENDING"
    decide_debt(env[1], env[2].id, inv.id, debt.id,
                decision="reject", reason="Le client a payé entre-temps")
    db.session.commit()
    assert debt.status == "REJECTED"
    assert customer_service.manual_debt_balance(env[2].id, client_record.id) == 0
    assert balance(env) == 10


def test_cashier_debt_permission_scope_is_tied_to_own_inventory(env):
    from test_inventory_v2_approval import make_count
    cashier, first = make_count(env, reference="FINANCE-V2-SCOPE")
    other = setup_inventory(env, reference="FINANCE-OTHER")
    client_record = customer(env)
    with pytest.raises(LookupError, match="NOT_FOUND"):
        request_debt(cashier, env[2].id, other.id,
                     customer_id=client_record.id, order_id=None,
                     amount="30", reason="Pas mon inventaire")
    db.session.rollback()


def test_owner_can_enter_cash_count_via_inventory_page(env):
    inv = setup_inventory(env)
    drawer(env)
    client = env[0].test_client()
    import re
    response = client.get("/login")
    token = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', response.text)
    assert token
    assert client.post("/login", data={
        "email": env[1].email, "password": "test-password",
        "csrf_token": token.group(1),
    }).status_code == 302
    form = client.get(f"/bars/{env[2].id}/inventories/{inv.id}")
    assert form.status_code == 200
    csrf = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', form.text)
    assert csrf is not None
    result = client.post(
        f"/bars/{env[2].id}/inventories/{inv.id}/finance",
        data={"csrf_token": csrf.group(1), "action": "count_cash",
              "counted_amount": "900", "reason": "100 FCFA manquants"},
    )
    assert result.status_code == 302
    assert db.session.scalar(select(InventoryCashReconciliation.counted_amount)) == 900
    html = client.get(f"/bars/{env[2].id}/inventories/{inv.id}").text
    assert "Manquant de caisse" in html
    assert "100" in html


def test_v2_owner_approval_requires_review_of_pending_debt(env):
    from test_inventory_v2_approval import make_count
    from app.inventory_approval import submit, decide
    cashier, inv = make_count(env, reference="FINANCE-V2-APPROVAL", quantity="10")
    cust = customer(env)
    req = request_debt(cashier, env[2].id, inv.id,
                       customer_id=cust.id, order_id=None,
                       amount="500", reason="Service client")
    submit(cashier, env[2].id, inv.id)
    db.session.commit()
    with pytest.raises(ValueError, match="INVENTORY_FINANCE_DEBTS_PENDING"):
        decide(env[1], env[2].id, inv.id,
               decision="approve", reason="Tout est compté")
    db.session.rollback()
    decide_debt(env[1], env[2].id, inv.id, req.id,
                decision="reject", reason="Montant non justifié")
    db.session.commit()
    decision = decide(env[1], env[2].id, inv.id,
                      decision="approve", reason="Dette écartée, stock conforme")
    db.session.commit()
    assert decision.status == "POSTED"
    assert req.status == "REJECTED"
    assert balance(env) == 10


def test_finance_ajax_does_not_redirect_or_reset_inventory(env):
    """JSON finance writes leave the inventory counting page in place."""
    import re
    inv = setup_inventory(env, reference="FINANCE-AJAX")
    drawer(env)
    client = env[0].test_client()
    page = client.get("/login")
    csrf = re.search(r'name=["\\']csrf_token["\\']\\s+value=["\\']([^"\\']+)["\\']', page.text)
    assert csrf
    login = client.post("/login", data={
        "email": env[1].email, "password": "test-password",
        "csrf_token": csrf.group(1),
    })
    assert login.status_code == 302
    url = f"/bars/{env[2].id}/inventories/{inv.id}"
    page = client.get(url)
    assert "inventory_finance_async.js" in page.text
    token = re.search(r'name=["\\']csrf_token["\\']\\s+value=["\\']([^"\\']+)["\\']', page.text)
    response = client.post(url + "/finance", data={
        "csrf_token": token.group(1), "action": "count_cash",
        "counted_amount": "900", "reason": "Test comptage AJAX",
    }, headers={"Accept": "application/json"})
    assert response.status_code == 200
    assert response.is_json
    assert response.json["success"] is True
    assert response.headers.get("Location") is None
    assert db.session.scalar(select(InventoryCashReconciliation.counted_amount)) == 900
    failed = client.post(url + "/finance", data={
        "csrf_token": token.group(1), "action": "count_cash",
        "counted_amount": "800", "reason": "",
    }, headers={"Accept": "application/json"})
    assert failed.status_code == 400
    assert failed.json["success"] is False
    assert db.session.scalar(select(InventoryCashReconciliation.counted_amount)) == 900

