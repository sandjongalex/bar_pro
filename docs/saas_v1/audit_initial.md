# BAR_PRO SaaS V1 — audit initial (19 octobre 2026)

## Périmètre, preuve et limites
Référence : `main` au commit `5c3d8be9e8371bbb8af0c0cc2f9c924df45f9e50` (19/10/2026, 20:20 UTC). Audit statique des modèles, des contrôles d'accès, des blueprints et services critiques, des tests et de la CI ; vérifications ciblées des autres domaines à partir de l'arborescence. **Ce document ne constitue ni un pentest exhaustif de toutes les routes ni un audit dynamique de MySQL/PythonAnywhere en production.** Toute absence de preuve est marquée « à vérifier ».

## Architecture constatée
- Monolithe Flask (factory `app/__init__.py`, blueprints `app/*.py`), pages Jinja2 et JavaScript natif, API REST `/api/v1`. Extensions SQLAlchemy, Migrate/Alembic, Flask-Login, Flask-WTF, Flask-Limiter (`app/extensions.py`).
- MySQL/PyMySQL cible de production ; SQLite pour tests/développement. Schéma Alembic versionné dans `migrations/versions/`. Ne pas confondre les propositions historiques de `docs/ARCHITECTURE.md` avec le code exécuté : `AGENTS.md` le précise.
- `User.category` = `SUPER_ADMIN|OWNER|EMPLOYEE`, `StaffAssignment.role` = `BAR_ADMIN|CASHIER|SERVER`. `Bar.owner_id` permet plusieurs bars par propriétaire ; un salarié a une unique affectation active (contrainte + `app/employee_context.py`).
- Tables métier `Tenant` portent `bar_id` ; `tenant_args` ajoute clé unique composite et index. Relations composites présentes notamment pour commandes, achats, dettes clients et sous-commandes.
- Login web : cookie de session Flask-Login + CSRF. API : JWT ES256 Bearer avec `bar_id`, version credentials, token révoquable/rotatif ; un jeton pour un bar ne doit pas autoriser un autre bar (`app/auth.py`).
- `app/permissions.py` décide des droits serveur et de l'état `SUSPENDED`, avec garde de service actif pour CASHIER/SERVER ; `app/__init__.py` et `app/auth.py` ajoutent des gardes web. Les permissions existantes restent larges pour certaines opérations financières, sans délégation plafonnée.
- Commandes `app/order_services.py`, sous-commandes `app/order_suborder_service.py`, éditions `app/suborder_edit_service.py`, caisse `app/cash_services.py`, paiements `app/payment_services.py`, achats/inventaires/stocks ; toutes ces couches doivent conserver les contrats de transaction, stock, CSRF et concurrence.
- Clients : `app/customer_models.py`, `app/customer_services.py`, `app/customers_web.py`. `record_manual_debt` et `record_manual_repayment` enregistrent des ajustements `CustomerLedgerEntry(entry_kind=ADJUSTMENT)`, avec `order_id=None`, sans vente ni mouvement de stock/caisse. Actuellement permission `customer_debt.manage` réservée au OWNER et SUPER_ADMIN.
- AJAX : `app/async_ui.py`, `app/static/barpro_async.js` ; envois réseau same-origin et jeton CSRF pour mutations web. PWA : `app/pwa.py`, `app/static/sw.js` ; worker ne met en cache que le shell public, pas les données de caisse.
- Abonnements partiels présents : `Plan`, `Subscription`, `SubscriptionPayment`, `app/subscriptions.py`, `app/subscription_services.py` (activation/paiement manuel). **La présence de ces objets ne prouve pas un cycle SaaS complet**.

## Écart entre code actuel et V1 décidée
| Sujet | Observé | V1 cible / écart |
| --- | --- | --- |
| Création bar | SUPER_ADMIN uniquement (`bars.create`, `app/bar_services.py`) | Auto-inscription propriétaire contrôlée, avec vérifications d'identité |
| Vérification propriétaire | User possède email, pas de téléphone vérifié dans le modèle étudié | Email **et** téléphone obligatoirement vérifiés |
| Essai | Abonnement manuel ; pas de mécanisme d'essai 14 jours vérifié | Essai 14 jours exactement ; à expiration, consultation seule **sans grâce** |
| Statut bar | `ACTIVE` / `SUSPENDED` | Ne pas détourner `SUSPENDED` pour lecture seule ; calculer un état SaaS d'accès distinct |
| Administrateur de bar | Droits statiques `BAR_ADMIN` ; `staff.manage` restreint | Délégations financières optionnelles, plafonds fixés par OWNER sous bornes plateforme, journal d'audit |
| Compte / récupération | Identité par email, tokens, version credentials | Récupération renforcée : pièces, délai de sécurité, notifications, audit, anti-contournement |
| Paiement abonnement | Manuel via API existante | V1 facturation, états, preuves, échéance, accès lecture seule et reprise sûre |

## Revue multi-tenant et IDOR — état de preuve
| Surface | Protection observée | Point restant à examiner |
| --- | --- | --- |
| `/bars/<bar_id>` web | `tenant_web_guard` compare employé et bar ; retourne 404 | Toutes les routes sans paramètre `bar_id`, toutes les méthodes et cas non authentifiés |
| API JWT | `api_required` compare claim `bar_id` et route ; jetons révoqués | Couverture effective sur **chaque** blueprint API et endpoints JSON d'ajout |
| ORM services | Majorité des accès affichés filtre `Model.bar_id == bar_id` | Revue exhaustive des ID secondaires (`order_id`, `suborder_id`, `payment_id`, `customer_id`, `supplier_id`) |
| UI AJAX | Contrôle de rôle dans le serveur et CSRF dans `barpro_async.js` | Contrôler tous les endpoints JSON cookie, pas uniquement le formulaire |
| Liste bars | `app/bars.py` récupère les bars puis filtre les droits côté Python | Optimiser avec requêtes limitées au tenant sans modifier la visibilité |
| Requêtes secondaires | Ex. `app/cashier_workspace_web.py` charge StaffAssignment/User à partir d'IDs calculés | Vérifier provenance tenant et interdiction d'une sélection client arbitraire |
| Export/rapport/impression | Blueprints de finance, factures, rapports existants | Contrôles d'accès et cohérence des IDs sur flux imprimables, export, lecture et filtres |
| Suspension | `permissions.evaluate` bloque écritures de bar suspendu | Nouvel état SaaS consultation-seule à appliquer à toutes les écritures, API et POST |

**Conclusion sécurité :** aucun accès inter-bar confirmé par les extraits et tests consultés ; **pas de garantie générale d'absence de vulnérabilité**. Les tables multi-tenant, jetons et tests existants sont une défense réelle, non une preuve d'exhaustivité.

## Constats prioritaires
- **P0 — non-régression réelle :** CI `main` (run `39318537195`, job all-python `118286241322`) : `335 passed, 1 failed` ; échec `tests/test_cashier_suborder_edit.py::test_cashier_can_edit_round_before_and_after_delivery_and_server_sees_changes` : page de workspace renvoie HTTP 302 vers `/bars/1/cashier-session` au lieu d'un formulaire CSRF attendu. Ne pas présenter la suite comme verte, ne pas contourner le garde de session pour « faire passer le test ». Voir `plan_tests.md`.
- **P1 — fonction manquante, pas vulnérabilité prouvée :** 14 jours/lecture seule et vérification double canal ne sont pas implémentés dans le flux inspecté.
- **P1 — gouvernance :** `permissions.py` donne à BAR_ADMIN les actions `payments.record`, `refunds.record`, `expenses.record`, `cash.operate` sans plafond paramétrable au niveau rôle/transaction.
- **P1 — surface de contrôle :** `WRITES` explicite des actions existantes ; une future écriture oubliée ne serait pas bloquée lors de l'expiration. Centraliser et tester le garde lecture seule au niveau service/transaction.
- **P2 — sécurité d'exploitation :** `app/config.py` tolère exceptionnellement `ALLOW_IN_MEMORY_RATELIMIT=1` ; sur multi-workers, une limite mémoire n'est pas partagée. Le plan PythonAnywhere gratuit nécessite une validation de déploiement/charge distincte.
- **P2 — couverture :** tests unitaires/intégration SQLite majoritaires ; pas de preuve de vérification MySQL réelle ou test de charge multi-processus.

## Fichiers réutilisables sans refonte
`app/__init__.py`, `app/permissions.py`, `app/auth.py`, `app/models.py`, `app/bar_services.py`, `app/audit.py`, `app/subscription_services.py`, `app/subscriptions.py`, `app/customer_services.py`, `app/order_suborder_service.py`, `app/order_services.py`, `app/cash_services.py`, `app/static/barpro_async.js`, `app/static/sw.js`, `tests/test_workflows.py`, `tests/test_employee_tenant_auth.py`. Les migrations existantes sont **immuables**.

## Sources d'observation
Code `main` : `app/__init__.py`, `app/auth.py`, `app/permissions.py`, `app/models.py`, `app/employee_context.py`, `app/bars.py`, `app/customers_web.py`, `app/customer_services.py`, `app/suborder_web.py`, `app/order_suborder_service.py`, `app/cashier_workspace_web.py`, `app/config.py`, `app/static/sw.js`, `tests/test_employee_tenant_auth.py`, `tests/test_owner_multibar_tenant_regression.py`, `tests/test_workflows.py`, workflows CI, schémas Alembic. Autres surfaces à soumettre à revue détaillée dans la phase suivante : tous les blueprints listés dans `app/__init__.py`.
