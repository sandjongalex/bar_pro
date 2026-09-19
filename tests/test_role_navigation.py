import re

from app.extensions import db
from app.models import StaffAssignment, User, utcnow
from app.order_services import order_service
from test_workflows import env


def _csrf_from(page):
    match = re.search(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', page.text)
    assert match is not None, page.text
    return match.group(1)


def _login(client, email, password="test-password"):
    page = client.get("/login")
    return client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": _csrf_from(page)},
        follow_redirects=False,
    )


def _cashier(bar):
    cashier = User(
        email="cashier-nav@example.invalid",
        display_name="Cashier Navigation",
        category="EMPLOYEE",
    )
    cashier.set_password("test-password")
    db.session.add(cashier)
    db.session.flush()
    db.session.add(
        StaffAssignment(
            bar_id=bar.id,
            user_id=cashier.id,
            role="CASHIER",
            started_at=utcnow(),
        )
    )
    db.session.commit()
    return cashier


def test_cashier_navigation_only_surfaces_operational_destinations(env):
    app, _, bar, _, _, _, _, _ = env
    cashier = _cashier(bar)
    client = app.test_client()
    assert _login(client, cashier.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/cashier-session")
    assert page.status_code == 200
    assert 'class="nav-role-label">Poste caissière' in page.text
    assert "Remises" in page.text
    assert "Retours" in page.text
    assert "Historique" in page.text
    assert "Session / clôture" in page.text
    assert "Dettes fournisseurs" not in page.text
    assert '<span class="nav-link-text">Équipe</span>' not in page.text

    assert 'data-context-nav' in page.text
    assert 'aria-label="Retour vers Caisse"' in page.text
    assert '<strong>Session de caisse</strong>' in page.text
    assert 'aria-label="Raccourcis caisse"' in page.text
    assert 'data-context-shortcuts' in page.text


def test_server_navigation_is_focused_on_orders_and_notifications(env):
    app, _, bar, _, product, server_user, _, _ = env
    order_service.create(
        server_user,
        bar.id,
        "SERVER-NAV-ORDER",
        [{"product_id": product.id, "quantity": 2}],
        notes="Terrasse",
    )
    db.session.commit()

    client = app.test_client()
    assert _login(client, server_user.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    assert 'class="nav-role-label">Espace serveuse' in page.text
    assert '<span class="nav-link-text">Nouvelle commande</span>' in page.text
    assert '<span class="nav-link-text">Mes commandes</span>' in page.text
    assert '<span class="nav-link-text">Notifications</span>' in page.text
    assert "Dettes fournisseurs" not in page.text
    assert '<span class="nav-link-text">Stock</span>' not in page.text

    assert 'data-context-nav' in page.text
    assert 'aria-label="Retour vers Mes commandes"' in page.text
    assert '#mes-commandes' in page.text
    assert '<strong>Nouvelle commande</strong>' in page.text
    assert 'aria-label="Raccourcis service"' in page.text
    assert '>＋ Nouvelle commande</a>' in page.text
    assert '>Mes commandes</a>' in page.text

    # Serveuse: le flux opérationnel commence par le lieu/table avant les produits.
    assert 'data-server-table-step' in page.text
    assert 'id="serverTableGrid"' in page.text
    assert 'data-table-label="Sans table"' in page.text
    assert 'id="serverTableCurrent">Sans table' in page.text
    assert '3. Envoyer à la caisse' in page.text
    assert 'À traiter par la caisse' in page.text

    # Étape 2 UI: sélection tactile rapide + dock panier responsive.
    assert 'server_pos.css' in page.text
    assert 'server_pos_ui.js' in page.text
    ui_script = client.get('/static/server_pos_ui.js')
    assert ui_script.status_code == 200
    assert 'server-product-qty' in ui_script.text
    assert 'server-cart-dock' in ui_script.text
    assert 'max-width: 980px' in ui_script.text

    # Étape 3 UI: Mes commandes devient un suivi opérationnel en cartes filtrables.
    assert 'class="panel-card mt-4 server-orders-panel"' in page.text
    assert 'data-server-order-filter="active"' in page.text
    assert 'data-server-order-filter="waiting"' in page.text
    assert 'data-server-order-card' in page.text
    assert 'data-order-state="waiting"' in page.text
    assert 'SERVER-NAV-ORDER' in page.text
    assert 'En attente caisse' in page.text
    assert '2×' in page.text
    assert 'Water' in page.text
    assert 'setupOrderFilters' in ui_script.text

    # Étape 4 UI: Note / Annuler sont transformés en éditeurs inline côté serveuse.
    assert 'setupInlineOrderActions' in ui_script.text
    assert 'server-order-inline-editor' in ui_script.text
    assert 'server-orders-return' in ui_script.text
    assert 'Confirmer l’annulation' in ui_script.text
    server_css = client.get('/static/server_pos.css')
    assert server_css.status_code == 200
    assert '.server-order-inline-editor' in server_css.text
    assert '.server-order-inline-controls' in server_css.text

    # Étape 5 UI: bascule Nouvelle commande / Mes commandes en un toucher.
    assert 'data-server-bottom-link="new"' in page.text
    assert 'data-server-bottom-link="orders"' in page.text
    assert f'/bars/{bar.id}/orders/new#posForm' in page.text
    assert f'/bars/{bar.id}/orders/new#mes-commandes' in page.text
    assert 'setupSectionNavigation' in ui_script.text
    assert 'server-section-switcher' in ui_script.text
    assert 'data-server-section-link="orders"' in ui_script.text
    assert '.server-section-switcher' in server_css.text


def test_owner_keeps_full_grouped_navigation(env):
    app, owner, bar, _, _, _, _, _ = env
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/orders/new")
    assert page.status_code == 200
    assert '<span class="nav-link-text">Ventes</span>' in page.text
    assert '<span class="nav-link-text">Stock</span>' in page.text
    assert '<span class="nav-link-text">Achats</span>' in page.text
    assert '<span class="nav-link-text">Finances</span>' in page.text
    assert 'class="nav-role-label">Poste caissière' not in page.text
    assert 'class="nav-role-label">Espace serveuse' not in page.text
    assert 'aria-label="Raccourcis ventes"' in page.text


def test_owner_catalog_context_goes_back_to_stock(env):
    app, owner, bar, _, _, _, _, _ = env
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/catalog")
    assert page.status_code == 200
    assert 'data-context-nav' in page.text
    assert 'aria-label="Retour vers État du stock"' in page.text
    assert '<span>Stock</span>' in page.text
    assert '<strong>Produits &amp; catégories</strong>' in page.text
    assert 'aria-label="Raccourcis stock"' in page.text
    assert '>Produits</a>' in page.text
    assert '>État du stock</a>' in page.text
    assert '>Inventaires</a>' in page.text


def test_owner_purchase_context_links_related_windows(env):
    app, owner, bar, _, _, _, _, _ = env
    client = app.test_client()
    assert _login(client, owner.email).status_code == 302

    page = client.get(f"/bars/{bar.id}/purchases")
    assert page.status_code == 200
    assert 'aria-label="Raccourcis achats"' in page.text
    assert '>Achats</a>' in page.text
    assert '>Fournisseurs</a>' in page.text
    assert '>Dettes</a>' in page.text