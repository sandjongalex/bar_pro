# Contexte permanent de Bar Manager Pro

Ce document résume le contexte technique et métier stable du projet. Il ne
remplace pas les documents responsables : schéma dans
[`docs/DATA_MODEL.md`](docs/DATA_MODEL.md), routes dans
[`docs/ROUTES.md`](docs/ROUTES.md), contrat API dans
[`docs/API.md`](docs/API.md), règles métier détaillées dans
[`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md), état réel dans
[`docs/PROGRESS.md`](docs/PROGRESS.md).

## Objectif

Bar Manager Pro est une application Flask de gestion multi-bar. Elle vise à
servir des propriétaires de bars, leurs employés et un super-administrateur
plateforme. Le bar est l'unité métier centrale : catalogue, stock, commandes,
achats, caisse, retours, paiements, inventaires et documents financiers y sont
rattachés.

Le cahier des charges global est signalé comme incomplet dans les documents
existants : `EXT-001` reste absent dans
[`docs/SPECIFICATIONS.md`](docs/SPECIFICATIONS.md). Ne pas transformer une
structure présente ou une cible documentaire en fonctionnalité complète.

## Glossaire

| Terme | Sens dans le projet |
| --- | --- |
| Bar / tenant | Unité d'isolation des données commerciales ; voir `TEN-001` dans [`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md). |
| Propriétaire | Utilisateur `OWNER` possédant un ou plusieurs bars ; chaque bar a un propriétaire unique observé dans `Bar.owner_id`. |
| Employé | Utilisateur `EMPLOYEE` rattaché par `StaffAssignment` à un bar, avec un rôle local actif au plus. |
| Catégorie globale | Valeur `User.category` : `SUPER_ADMIN`, `OWNER` ou `EMPLOYEE`, définie par [`app/models.py`](app/models.py). |
| Rôle local | Valeur `StaffAssignment.role` : `BAR_ADMIN`, `CASHIER` ou `SERVER`, valable dans le bar d'affiliation. |
| Snapshot | Copie historique d'un nom, prix, unité, coût ou condition au moment d'une opération. |
| Garde | Espèces détenues par un employé et suivies dans `StaffCashLedger`, distinctes du tiroir. |
| Tiroir | Caisse ouverte du bar, représentée par `CashSession` et `CashMovement`. |

## Décisions explicitement documentées

- Stack applicative : Flask, Blueprints, services métier, Jinja2, API
  `/api/v1`, SQLAlchemy, Alembic, Flask-Login, Flask-WTF/CSRF, Flask-Limiter ;
  voir [`README.md`](README.md), `DEC-006` et `DEC-013` dans
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).
- API mobile : JWT Bearer signé `ES256`, refresh token opaque et bar courant
  porté par le token ; voir `DEC-025` dans
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) et
  [`app/auth.py`](app/auth.py).
- Web : session cookie Flask-Login, cookie sécurisé en production, CSRF sur les
  formulaires ; voir `DEC-013` dans
  [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) et les formulaires de
  [`app/templates/`](app/templates/).
- Base : SQLite pour développement/tests locaux ; MySQL via PyMySQL exigé en
  production par [`app/config.py`](app/config.py). Les tests SQLite et la
  compilation SQL MySQL ne prouvent pas un déploiement MySQL réel.
- Déploiement cible : préparation PythonAnywhere/MySQL, modèles de WSGI et
  d'environnement dans [`deploy/`](deploy/), Redis durable attendu pour le
  rate limit en production ; voir [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
- Frontend : vues serveur Jinja2 avec Bootstrap chargé depuis le CDN
  jsDelivr dans [`app/templates/layout.html`](app/templates/layout.html).
- Isolation : un bar est le tenant ; les propriétaires sont limités à leurs
  bars et les employés à leur affiliation active ; voir `TEN-001` à `TEN-007`
  dans [`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md).
- Rôles : les catégories globales `SUPER_ADMIN`, `OWNER`, `EMPLOYEE` sont
  distinctes des rôles locaux `BAR_ADMIN`, `CASHIER`, `SERVER` ; voir
  `AUTH-002` et [`app/permissions.py`](app/permissions.py).
- Calcul : montants en `DECIMAL(19,4)`, quantités en `DECIMAL(20,6)`,
  validation par `Decimal`, refus des valeurs non finies ; voir `FIN-001`,
  [`app/models.py`](app/models.py) et [`app/validation.py`](app/validation.py).
- Devise et paramètres de bar : `Bar.currency`, `Bar.timezone`,
  `stock_alert_threshold` et `credit_sales_enabled` existent ; la devise par
  défaut est `XAF` et le changement de devise est refusé par
  `bar_services.update_bar`.
- Historique : les lignes de commande, achat, abonnement, inventaire et stock
  conservent des snapshots pour préserver le passé malgré les changements de
  catalogue ou de paramètres.

## Comportements observés dans le code

- Les décisions de permission passent par `PermissionService` dans
  [`app/permissions.py`](app/permissions.py), avec verrouillage du bar pour les
  écritures connues.
- Les routes API protégées acceptent uniquement `Authorization: Bearer ...` ;
  une session web n'authentifie pas l'API (`api_required` dans
  [`app/auth.py`](app/auth.py)).
- Les vues web utilisent Jinja2 et Bootstrap pour le tableau de bord, le
  catalogue, l'historique de stock, les commandes rapides, les inventaires et
  l'écran finance.
- Les achats créent des brouillons, figent les snapshots de fournisseur et de
  ligne, puis la réception poste les mouvements de stock via
  [`app/stock_service.py`](app/stock_service.py).
- Les commandes sortent le stock à la confirmation, peuvent être servies,
  annulées si les règles le permettent, et recevoir des retours avec option
  `RESTOCK` ou `LOSS`.
- Les paiements et remboursements sont manuels. Les modes `CARD`,
  `MOBILE_MONEY` et `BANK_TRANSFER` enregistrent une référence fournie ; aucun
  workflow prestataire, webhook ou confirmation externe n'est observé.
- Les espèces peuvent être enregistrées dans une caisse ouverte ou dans la
  garde d'une affiliation employé, puis remises au tiroir par `CashHandover`.
- Les inventaires web/API créent un brouillon, capturent quantités attendues et
  versions de stock, enregistrent le comptage puis valident des ajustements.
- Les tables `UserSession` et `IdempotencyRecord` existent dans le modèle, mais
  leur présence ne prouve pas l'existence de workflows opérationnels de session
  serveur ni d'idempotence HTTP complète.

## Hypothèses de travail prudentes

- Le projet reste un monolithe Flask tant qu'une décision documentée ne change
  pas cette architecture.
- Toute donnée commerciale nouvelle doit être rattachée à un bar, sauf exception
  explicitement documentée comme `Plan` ou une donnée d'identité globale.
- Une technologie absente n'est pas considérée comme volontairement exclue sans
  preuve ; noter `À déterminer` plutôt que déduire une décision.
- Une route, une table ou une permission ne suffit pas à déclarer une capacité
  livrée : vérifier le service, la route, le template éventuel et les tests.
- Les documents historiques peuvent décrire une cible ou un état ancien ; le
  comportement courant se vérifie dans le code et [`docs/PROGRESS.md`](docs/PROGRESS.md).

## Fonctionnement métier général

Les achats partent d'un fournisseur et de lignes produit ; la réception
augmente le stock et peut créer une dette fournisseur. Le stock est modifié par
des mouvements typés et une projection `StockBalance`. Les commandes retirent
le stock lors de la confirmation et conservent prix, coût, nom et unité en
snapshots. Les retours créent des documents dédiés, plafonnés par la vente
historique ; ils peuvent réintégrer le stock ou constater une perte.

Les paiements soldent les commandes manuellement, avec espèces, carte, mobile
money ou virement. Les remboursements sont distincts des paiements, réutilisent
le mode et la devise du paiement, et peuvent être liés à un retour. La caisse
gère ouverture, mouvements, clôture, garde employé et remises. Les inventaires
capturent l'état attendu, saisissent un comptage et ajustent le stock après
validation.

## Contraintes connues

- Ne pas transformer la préparation PythonAnywhere en déploiement effectif :
  [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) indique explicitement qu'aucun
  déploiement ni secret réel n'est fourni.
- Ne pas annoncer une validation MySQL réelle sans upgrade et tests sur serveur
  MySQL accessible ; [`docs/PROGRESS.md`](docs/PROGRESS.md) limite les preuves
  à SQLite local et compilation SQL MySQL hors connexion.
- Ne pas recopier ici le schéma, les endpoints, la matrice complète des
  permissions ni l'avancement détaillé ; suivre les liens responsables.
- Les paiements hors espèces sont des saisies manuelles, pas une intégration de
  prestataire.
- Les modules dépenses, abonnements, rapports, audit exhaustif, idempotence HTTP
  et certains écrans de gestion restent partiels selon
  [`docs/PROGRESS.md`](docs/PROGRESS.md).

## À déterminer

- Cahier des charges global complet et périmètre V1 exhaustif.
- Règles fiscales, remises, arrondis d'affichage et encaissement par devise.
- Crédit client, surpaiement, rapprochement externe et politiques de paiement
  par prestataire.
- Valorisation de stock, conversions d'unités, lots, livraison fractionnée et
  règles futures autour du stock négatif.
- Workflows complets de dépenses, abonnements, rapports et audit de toutes les
  lectures/mutations sensibles.
- Sessions serveur fondées sur `UserSession` et idempotence HTTP fondée sur
  `IdempotencyRecord`, si elles doivent devenir opérationnelles.
- Validation réelle PythonAnywhere/MySQL/Redis, concurrence avec plusieurs
  connexions et disponibilité réseau du compte cible.
- Politique de stockage des images, logos et uploads, actuellement refusés ou
  non branchés dans les services observés.
