# Contrat API courant

Ce document décrit exclusivement l’API Flask enregistrée dans le code actuel.
[`ROUTES.md`](ROUTES.md) reste l’inventaire technique généré depuis `app.url_map`.
[`API_CONTRACT_DESIGN.md`](API_CONTRACT_DESIGN.md) conserve une conception historique et
ne prouve pas qu’une route existe. Les règles métier détaillées restent dans
[`SPECIFICATION.md`](SPECIFICATION.md) et [`BUSINESS_RULES.md`](BUSINESS_RULES.md).

Sources vérifiées : `app/__init__.py`, `app/web.py`, `app/auth.py`, `app/bars.py`,
`app/catalog.py`, `app/stock.py`, `app/inventories.py`, `app/purchases.py`,
`app/orders.py`, `app/finance.py`, `app/permissions.py`, `app/errors.py`,
`app/validation.py`, services métier appelés et tests HTTP dans `tests/`.

## Index

| Domaine | Routes |
| --- | --- |
| Santé | `GET /api/v1/health` |
| Authentification | `POST /api/v1/auth/tokens`, `/tokens/refresh`, `/logout` |
| Bars et personnel | `GET/POST /api/v1/bars`, `PATCH /api/v1/bars/{bar_id}`, `POST /staff` |
| Catalogue | `GET/POST /api/v1/bars/{bar_id}/products` |
| Stock | `GET /stock/alerts`, `POST /stock/movements` |
| Inventaires | `GET/POST /inventories`, détail, comptage, validation, annulation |
| Fournisseurs et achats | fournisseurs, achats, soldes et paiements fournisseur |
| Commandes et retours | création, confirmation, service, annulation, retours |
| Finance et caisse | paiements, remboursements, caisse, mouvements, gardes/remises |

`B` désigne `/api/v1/bars/{bar_id}`.

## Conventions réelles

Les succès utilisent généralement :

```json
{"success": true, "data": {}, "meta": {}}
```

Les erreurs JSON contrôlées utilisent :

```json
{"success": false, "error": {"code": "FORBIDDEN", "message": "Accès interdit", "details": null}}
```

Cette enveloppe n’est pas normalisée artificiellement : certains contrôleurs capturent eux-mêmes
les erreurs et changent le code HTTP ou le message. Les identifiants retournés sont souvent des
chaînes, tandis que certains corps acceptent aussi des entiers. Les montants et quantités retournés
sont sérialisés en chaînes décimales quand ils proviennent des modèles.

Les routes métier sous `/api/v1/bars` exigent `Authorization: Bearer <access_token>`.
Le `bar_id` du chemin doit correspondre au `bar_id` signé dans le JWT ; sinon la réponse est `404`.
Les cookies web Flask-Login ne sont pas acceptés comme identité API. Les blueprints API sont exemptés
de CSRF ; le web HTML conserve Flask-Login et CSRF.

Le hook global refuse un JSON non objet sur les routes `/api/` quand `request.is_json` est vrai.
Les routes qui appellent `request.get_json()` sans `silent=True` peuvent aussi laisser Flask produire
une erreur HTTP si le corps ou le type de contenu ne convient pas.

Les codes observés et déduits du code actuel :

| Code | Cas réel |
| --- | --- |
| `200` | lecture, transition ou mutation réussie sans création |
| `201` | création ou écriture financière créée |
| `400` | champ requis manquant, JSON non objet, ou contrôleur qui mappe une règle métier en `400` |
| `401` | bearer absent/invalide, access expiré/révoqué, refresh invalide/réutilisé |
| `403` | permission serveur refusée par `PermissionService` |
| `404` | bar signé incompatible, ressource absente ou masquage tenant |
| `409` | conflit d’état capturé ou contrainte SQL `IntegrityError` |
| `422` | `ValueError`/`TypeError` non capturée par la route |

Aucune route ne traite `Idempotency-Key` comme rejeu HTTP opérationnel. Les références uniques
peuvent provoquer un `409`, mais ne rejouent pas une réponse précédente. Aucun webhook prestataire
n’est exposé.

## Authentification

### `POST /api/v1/auth/tokens`

Public. Corps JSON requis :

| Champ | Type | Requis | Règle |
| --- | --- | --- | --- |
| `email` | chaîne | oui | normalisé en minuscules après `strip()` |
| `password` | chaîne | oui | vérifié par hash utilisateur |
| `bar_id` | entier non booléen | oui | l’utilisateur doit pouvoir lire ce bar |

Réponse `200` :

```json
{
  "success": true,
  "data": {
    "token_type": "Bearer",
    "access_token": "<access-jwt-es256>",
    "expires_in": 900,
    "refresh_token": "<refresh-opaque>"
  },
  "meta": {}
}
```

Erreurs : `401 INVALID_CREDENTIALS`, `404 NOT_FOUND` si `bar_id` est absent, non entier ou inaccessible.

L’access token est un JWT ES256 signé avec la clé configurée. `api_required` exige `exp`, `iat`,
`nbf`, `sub`, `bar_id`, `jti`, `token_use`, `credentials_version` et `sid`, avec issuer et audience.
Le serveur recharge l’utilisateur, vérifie `is_active`, `credentials_version`, la ligne `ApiToken`,
la révocation hors rotation et le droit courant `bars.read`.

### `POST /api/v1/auth/tokens/refresh`

Public au sens bearer : cette route n’utilise pas `Authorization`. Corps JSON :

| Champ | Type | Requis | Règle |
| --- | --- | --- | --- |
| `refresh_token` | chaîne | oui | jeton opaque stocké par empreinte SHA-256 |

Réponse `200` identique à l’émission. L’ancien refresh est révoqué avec raison `rotation`, un
nouveau refresh est créé dans la même famille. La réutilisation d’un refresh déjà révoqué révoque
la famille et retourne `401 REFRESH_TOKEN_REUSED`. Autres échecs : `401 INVALID_REFRESH_TOKEN`.

### `POST /api/v1/auth/logout`

Public au sens bearer : cette route utilise le refresh token du corps, pas le bearer.
Corps JSON : `{ "refresh_token": "<refresh-opaque>" }`.

Réponse toujours réussie même si le refresh est absent ou inconnu :

```json
{"success": true, "data": {}, "meta": {}}
```

Si le refresh existe, toute sa famille est révoquée avec raison `logout`.

## Santé

### `GET /api/v1/health`

Public. Retourne l’état du processus seulement. Pas d’authentification, pas de test base de données.
Pagination, filtres et tri : non proposés.

## Bars et personnel

### `GET /api/v1/bars`

Bearer requis. Retourne uniquement le bar signé par le token, dans un tableau.
Pagination, filtres et tri : non proposés.

Réponse :

```json
{"success": true, "data": [{"id": "1", "name": "Bar Demo", "status": "ACTIVE"}], "meta": {}}
```

Autorisation : `bars.read` déjà vérifiée par `api_required`.

### `POST /api/v1/bars`

Bearer requis. Autorisation : acteur `SUPER_ADMIN` via `create_bar`.

Corps JSON utilisé :

| Champ | Type | Requis | Default / règle |
| --- | --- | --- | --- |
| `owner_id` | identifiant | oui | doit viser un utilisateur `OWNER` actif |
| `name` | chaîne | oui | transmis au modèle |
| `timezone` | chaîne | oui | transmis au modèle |
| `currency` | chaîne | non | `XAF` |
| `address` | chaîne/null | non | `null` |
| `phone` | chaîne/null | non | `null` |
| `stock_alert_threshold` | décimal | non | `0` |
| `credit_sales_enabled` | booléen | non | `false` via `bool(...)` |
| `copy_from_id` | identifiant | non | copie catégories/produits du bar source si même propriétaire |

Réponse `201` : bar sérialisé. Effet : crée un bar ; si `copy_from_id` est fourni, copie catalogue
et soldes à zéro. Erreurs capturées : `403 FORBIDDEN` ou `404 NOT_FOUND`.

### `PATCH /api/v1/bars/{bar_id}`

Bearer requis. Autorisation : `bars.update_settings` (`SUPER_ADMIN`, `OWNER`).

Champs acceptés : `name`, `address`, `phone`, `timezone`, `stock_alert_threshold`,
`credit_sales_enabled`. `currency` différente est refusée. `logo_key` est refusé car le stockage
logo n’est pas disponible. `timezone` doit être connue par `zoneinfo`.

Réponse `200` : bar sérialisé. Erreurs : permission capturée en `403/404`; validations non capturées
par la route, donc `422`.

### `POST /api/v1/bars/{bar_id}/staff`

Bearer requis. Autorisation : `staff.manage` (`SUPER_ADMIN`, `OWNER`).

Corps : `user_id` requis, `role` requis parmi `BAR_ADMIN`, `CASHIER`, `SERVER`.
L’utilisateur doit exister, être `EMPLOYEE`, et l’affiliation créée reste active tant que `ended_at`
est nul.

Réponse `201` :

```json
{"success": true, "data": {"id": "12", "role": "CASHIER"}, "meta": {}}
```

## Catalogue

### `GET /api/v1/bars/{bar_id}/products`

Bearer requis. Autorisation : `catalog.read`.

Paramètres :

| Paramètre | Type | Default | Effet |
| --- | --- | --- | --- |
| `q` | chaîne | aucun | filtre `name` ou `sku` par `ilike` |
| `category_id` | entier | aucun | filtre catégorie |
| `active` | chaîne | aucun | `true` donne `True`, toute autre valeur donne `False` si présent |
| `page` | entier | `1` | pagination Flask-SQLAlchemy |

Tri : `Product.name`, puis `Product.id`. `per_page` n’est pas exposé par la route et vaut `20`.

Réponse `200` : `data` contient `id`, `name`, `sku`, `category_id`, `sale_price`, `is_active`,
`image_key`; `meta` contient `page`, `pages`, `total`.

### `POST /api/v1/bars/{bar_id}/products`

Bearer requis. Autorisation : `catalog.manage`.

Corps JSON ou formulaire multipart. Champs requis : `category_id`, `sku`, `name`, `base_unit`,
`sale_price`, `valuation_unit_cost`. Champs optionnels : `stock_alert_threshold` défaut `0`,
`units_per_case`, `image`. Les uploads image sont actuellement refusés par le service
(`IMAGE_STORAGE_UNAVAILABLE`) malgré la présence du paramètre.

Validation : catégorie du même bar, décimaux finis avec précision explicite, prix et seuils >= 0,
`units_per_case > 0` si fourni.

Réponse `201` : produit sérialisé comme la liste. Erreurs capturées : `400 BUSINESS_RULE_VIOLATION`.

## Stock

### `GET /api/v1/bars/{bar_id}/stock/alerts`

Bearer requis. Autorisation : `inventory.read`.

Retourne les soldes existants dont `quantity <= Product.stock_alert_threshold`.
Pagination, filtres et tri : non proposés.

Réponse `200` :

```json
{"success": true, "data": [{"product_id": "1", "quantity": "2.000000"}], "meta": {}}
```

### `POST /api/v1/bars/{bar_id}/stock/movements`

Bearer requis. Autorisation : `inventory.adjust`.

Corps :

| Champ | Type | Requis | Règle |
| --- | --- | --- | --- |
| `product_id` | identifiant | oui | produit du bar |
| `movement_type` | chaîne | oui | route limitée à `INITIAL`, `ADJUSTMENT`, `LOSS` |
| `quantity_delta` | décimal QTY | oui | sens cohérent : `LOSS < 0`, `INITIAL/ADJUSTMENT` non nul selon service |
| `reason` | chaîne | non dans route | service exige une chaîne non vide |

Effet : passe par `StockService.move`, verrouille produit et solde, refuse stock négatif.
Réponse `201` : `{id, quantity_delta}`. Erreurs capturées : `400 BUSINESS_RULE_VIOLATION`.
Les mouvements `SALE`, `PURCHASE`, `RETURN`, `INVENTORY_ADJUSTMENT` sont produits par les services
de commandes, achats, retours et inventaires, pas par cette route manuelle.

## Inventaires

Payload d’inventaire : `id`, `reference`, `reason`, `status`, `counted_at`, `posted_at`,
`cancelled_at`, `lines[]` avec `product_id`, `product_name`, `unit`, `expected_quantity`,
`counted_quantity`, `difference_quantity`.

### `GET /api/v1/bars/{bar_id}/inventories`

Bearer requis. Autorisation : `inventory.read`.
Paramètre : `page` entier, défaut `1`. `per_page` fixe `20`.
Tri : `created_at DESC`, `id DESC`.
Réponse `200` avec `meta.page`, `meta.pages`, `meta.total`.

### `GET /api/v1/bars/{bar_id}/inventories/{inventory_id}`

Bearer requis. Autorisation : `inventory.read`. Pagination, filtres et tri : non proposés.
`404` si inventaire absent ou hors tenant.

### `POST /api/v1/bars/{bar_id}/inventories`

Bearer requis. Autorisation : `inventory.adjust`.

Corps : `reference` requis, `product_ids` liste non vide d’identifiants, `reason` optionnel avec
default `"Inventaire"` dans la route. Le service exige une référence et une raison non vides,
refuse doublons et produits hors bar, crée des snapshots de quantité et de version.

Réponse `201` : payload complet. Erreurs : `400` si corps JSON non objet, `404`, `409` sur conflit
d’unicité, `422` sur validation.

### `PATCH /api/v1/bars/{bar_id}/inventories/{inventory_id}/counts`

Bearer requis. Autorisation : `inventory.adjust`.

Corps : `{ "quantities": { "<product_id>": "<qty>" } }`. La route transmet le dictionnaire au service.
Quantités décimales QTY, finies, >= 0. L’inventaire doit être `DRAFT`.

Réponse `200` : payload complet. Erreurs : `404`, `422`.

### `POST /api/v1/bars/{bar_id}/inventories/{inventory_id}/post`

Bearer requis. Autorisation : `inventory.adjust`.

Corps : non utilisé. Tous les produits doivent être comptés et les versions de solde doivent rester
inchangées. Effet : mouvements `INVENTORY_ADJUSTMENT` via `StockService`, statut `POSTED`, transaction
appelante atomique.

Réponse `200`. `ValueError` est capturée en `409 STATE_CONFLICT`.

### `POST /api/v1/bars/{bar_id}/inventories/{inventory_id}/cancel`

Bearer requis. Autorisation : `inventory.adjust`. Corps non utilisé. Annule un inventaire `DRAFT`.
Réponse `200`. Erreurs non capturées : `404`, `422`, `403`.

## Fournisseurs et achats

Les endpoints de cette section utilisent `serialize(item)`, qui retourne les colonnes du modèle en
chaînes ou `null`.

### Fournisseurs

| Endpoint | Autorisation | Entrées | Réponse et effets |
| --- | --- | --- | --- |
| `GET B/suppliers` | `suppliers.read` | `q`, `active`, `page` défaut `1`, `page_size` défaut `50`, max `100`; tri `name,id` | `data` liste, `meta.page/page_size/has_more` |
| `POST B/suppliers` | `suppliers.manage` | `name` requis ; `phone`, `email`, `address` optionnels | `201`, crée un fournisseur actif |
| `GET B/suppliers/{supplier_id}` | `suppliers.read` | aucun filtre | `200`, fournisseur |
| `PATCH B/suppliers/{supplier_id}` | `suppliers.manage` | champs modifiables du service | `200`, fournisseur modifié |

Erreurs : permissions `403`, absent `404`, validation `422`, conflit SQL `409`.

### Achats

| Endpoint | Autorisation | Entrées | Réponse et effets |
| --- | --- | --- | --- |
| `GET B/purchases` | `purchases.read` | `status`, `supplier_id`, `page`, `page_size`; tri `id DESC` | liste avec `has_more` |
| `POST B/purchases` | `purchases.manage` | `supplier_id`, `reference`, `lines[]`, `supplier_invoice_reference?` | `201`, achat `DRAFT`, snapshots fournisseur/produits |
| `GET B/purchases/{purchase_id}` | `purchases.read` | — | achat sérialisé |
| `PATCH B/purchases/{purchase_id}` | `purchases.manage` | champs service, dont lignes si encore `DRAFT` | achat modifié |
| `POST B/purchases/{purchase_id}/receive` | `purchases.manage` | corps non utilisé | réception complète, mouvements stock `PURCHASE` |
| `POST B/purchases/{purchase_id}/cancel` | `purchases.manage` | `reason` requis | annule si état compatible |
| `GET B/purchases/{purchase_id}/balance` | `purchases.read` | — | `{due_amount}` |
| `POST B/purchases/{purchase_id}/payments` | `purchases.manage` | `reference`, `amount`, `method`, `reason`, `cash_session_id?`, `provider_code?`, `provider_transaction_id?` | `201`, paiement fournisseur |

Lignes d’achat : `product_id`, `quantity`, `unit_cost`; remise/taxe éventuelles selon service.
Les paiements fournisseur hors espèces sont saisis manuellement. Les paiements espèces modifient
la caisse si `cash_session_id` est fourni.

### Paiements fournisseur

| Endpoint | Autorisation | Entrées | Réponse et effets |
| --- | --- | --- | --- |
| `GET B/supplier-payments` | `purchases.read` | `purchase_id?`, `page`, `page_size`; tri `id DESC` | liste avec `has_more` |
| `POST B/supplier-payments/{supplier_payment_id}/reverse` | `purchases.manage` | `reference`, `reason`, `cash_session_id?` | `201`, écrit une contre-écriture |

## Commandes et retours

### `POST /api/v1/bars/{bar_id}/orders`

Bearer requis. Autorisation : `orders.create`.

Corps : `reference`, `lines`, `table_id?`, `customer_id?`, `notes?`.
Chaque ligne : `product_id`, `quantity`, `note?`. Produits actifs du même bar, quantités positives,
pas de doublon produit. Effet : crée une commande `DRAFT` avec snapshots et totaux.

Réponse `201` : `{id, status}`. `ValueError` capturée en `400 BUSINESS_RULE_VIOLATION`.

### Transitions de commande

| Endpoint | Autorisation | Corps | Effet | Erreurs route |
| --- | --- | --- | --- | --- |
| `POST B/orders/{order_id}/confirm` | `orders.edit` | non utilisé | `DRAFT → CONFIRMED`, retrait stock `SALE` une seule fois | `409` sur `ValueError` |
| `POST B/orders/{order_id}/serve` | `orders.edit` | non utilisé | `CONFIRMED → SERVED`, pas de second retrait stock | `409` |
| `POST B/orders/{order_id}/cancel` | `orders.edit` | `reason` optionnel dans route, requis par service | annule `DRAFT` ou `CONFIRMED` non encaissée ; retour stock si confirmée | `409` |
| `POST B/orders/{order_id}/returns` | `orders.edit` | `reason`, `lines[]` | crée un retour commercial `POSTED` après service | `409` |

Lignes de retour : `order_line_id`, `quantity`, `disposition` (`RESTOCK` ou `LOSS`).
Le cumul retourné est plafonné à la quantité de ligne. `RESTOCK` crée un mouvement stock `RETURN`;
`LOSS` ne remet pas en stock. Le retour commercial ne rembourse pas automatiquement.

## Paiements, remboursements et caisse

Les paiements `CARD`, `MOBILE_MONEY` et `BANK_TRANSFER` sont des saisies manuelles. Aucun prestataire
ou webhook n’est appelé.

### Paiements

| Endpoint | Autorisation | Entrées | Réponse et effets |
| --- | --- | --- | --- |
| `POST B/payments` | `payments.record` | `order_id`, `reference`, `method`, `amount_presented`, `amount_applied`, `change_given?=0`, `cash_session_id?`, `staff_assignment_id?`, `provider_code?`, `provider_transaction_id?` | `201`, paiement et mise à jour statut paiement |
| `GET B/payments` | `payments.read` | `order_id?`, `cash_session_id?`, `staff_assignment_id?`, `page`, `page_size` | liste `id DESC`, `has_more` |

Règles : commande `CONFIRMED` ou `SERVED`; `amount_presented = amount_applied + change_given`;
`amount_applied > 0`; paiement plafonné au dû. Pour `CASH`, choisir exactement `cash_session_id`
ou `staff_assignment_id`; la caisse ou garde reçoit l’écriture. Hors espèces, pas de caisse/garde,
pas de monnaie rendue, `provider_code` et `provider_transaction_id` doivent être fournis ensemble
s’ils existent.

### Soldes, remboursements et annulation payée

| Endpoint | Autorisation | Entrées | Réponse et effets |
| --- | --- | --- | --- |
| `GET B/orders/{order_id}/balance` | `payments.read` | — | `total_paid`, `total_refunded`, `return_credit`, `net_sale`, `net_paid`, `amount_due`, `refundable_overpayment` |
| `POST B/refunds` | `refunds.record` | `payment_id`, `reference`, `amount`, `reason`, `order_return_id?`, `cash_session_id?`, `staff_assignment_id?` | `201`, remboursement traçable |
| `GET B/refunds` | `payments.read` | `order_id?`, `cash_session_id?`, `staff_assignment_id?`, `page`, `page_size` | liste |
| `POST B/orders/{order_id}/cancel-with-refunds` | `refunds.record` + `orders.edit` | `reference`, `reason`, emplacement espèces si nécessaire | rembourse les paiements restants puis annule une commande `CONFIRMED` |
| `GET B/returns` | `orders.read` | `order_id?`, `page`, `page_size` | liste des `OrderReturn` |

Un remboursement reprend la méthode du paiement. Le cumul remboursé ne peut pas dépasser le paiement.
S’il est lié à un retour, le retour doit être `POSTED`, appartenir à la même commande, et le cumul
lié au retour ne peut pas dépasser son crédit ni le trop-perçu remboursable.

### Sessions de caisse

| Endpoint | Autorisation | Entrées | Réponse et effets |
| --- | --- | --- | --- |
| `POST B/cash-sessions` | `cash.operate` | `reference`, `opening_amount` | `201`, ouvre une seule session `OPEN` par bar |
| `GET B/cash-sessions` | `cash.read` | `page`, `page_size` | liste |
| `GET B/cash-sessions/{session_id}` | `cash.read` | — | session + `expected_amount` calculé |
| `POST B/cash-sessions/{session_id}/close` | `cash.operate` | `counted_closing_amount`, `reason?` | ferme ; `reason` requis si écart |
| `POST B/cash-sessions/{session_id}/movements` | `cash.operate` | `kind`=`DEPOSIT`/`WITHDRAWAL`, `amount`, `reason` | `201`, mouvement manuel |

Les retraits manuels ne peuvent pas rendre la caisse négative. Les mouvements exigent une session ouverte.

### Mouvements, gardes et remises

| Endpoint | Autorisation | Entrées | Réponse et effets |
| --- | --- | --- | --- |
| `GET B/cash-movements` | `cash.read` | `cash_session_id?`, `page`, `page_size` | liste |
| `POST B/cash-movements/{movement_id}/reverse` | `cash.operate` | `reason` | `201`, contre-écriture d’un mouvement manuel non déjà reversé |
| `POST B/cash-handovers` | `cash.operate` | `staff_assignment_id`, `cash_session_id`, `reference`, `amount` | `201`, crée une remise `DRAFT` |
| `GET B/cash-handovers` | `cash.read` | `cash_session_id?`, `staff_assignment_id?`, `page`, `page_size` | liste |
| `POST B/cash-handovers/{handover_id}/post` | `cash.operate` | corps non utilisé | transfert atomique garde → caisse, statut `POSTED` |
| `POST B/cash-handovers/{handover_id}/cancel` | `cash.operate` | corps non utilisé | statut `CANCELLED` si `DRAFT` |
| `GET B/staff-cash` | `cash.read` | `staff_assignment_id?`, `page`, `page_size` | lignes de garde |
| `GET B/staff-cash/{staff_id}` | `cash.read` | — | solde `{balance}` ou `404` si affiliation inconnue |

Pagination des listes dynamiques finance : `page` défaut `1`, `page_size` défaut `50`, borné à `1..100`,
tri `id DESC`, `meta.has_more`.

## Autorisations

Les permissions sont calculées côté serveur dans `PermissionService`.

| Action | Rôles admis |
| --- | --- |
| `bars.read`, `catalog.read`, `orders.read`, `orders.create`, `orders.edit` | `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN`, `CASHIER`, `SERVER` |
| `catalog.manage`, `suppliers.*`, `purchases.*`, `inventory.*` | `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN` |
| `payments.read`, `payments.record`, `refunds.record`, `cash.read`, `cash.operate` | `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN`, `CASHIER` |
| `bars.update_settings`, `staff.manage` | `SUPER_ADMIN`, `OWNER` |
| `bars.create` | `SUPER_ADMIN` |

Le rôle local provient d’une affiliation active `StaffAssignment`. Un bar suspendu bloque les écritures
métier, sauf réactivation non exposée par l’API courante ; les employés perdent aussi l’accès lecture.

## Exemples fictifs

```http
POST /api/v1/auth/tokens
Content-Type: application/json

{"email": "owner@example.invalid", "password": "mot-de-passe", "bar_id": 1}
```

```http
POST /api/v1/bars/1/orders
Authorization: Bearer <access-jwt-es256>
Content-Type: application/json

{"reference": "ORD-001", "lines": [{"product_id": 10, "quantity": "2"}]}
```

```http
POST /api/v1/bars/1/payments
Authorization: Bearer <access-jwt-es256>
Content-Type: application/json

{
  "order_id": 42,
  "reference": "PAY-001",
  "method": "CASH",
  "amount_presented": "500.0000",
  "amount_applied": "400.0000",
  "change_given": "100.0000",
  "cash_session_id": 7
}
```
