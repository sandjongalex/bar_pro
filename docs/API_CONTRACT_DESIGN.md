# Archive du contrat cible, non intégralement implémenté

Consulter [API.md](API.md) pour les opérations actuelles et [PROGRESS.md](PROGRESS.md) pour les limites.

# Bar Manager Pro — Contrat REST `/api/v1`

> État au 9 septembre 2026 : ce document conserve des exigences et une architecture cible.
> Il ne constitue pas une liste de fonctionnalités disponibles. Les routes réellement
> enregistrées sont dans [ROUTES.md](ROUTES.md), le schéma installé dans
> [DATA_MODEL.md](DATA_MODEL.md), les tests exécutés et les écarts dans [PROGRESS.md](PROGRESS.md).


Version : 0.4 — 2026-09-09. Source : SRC-004, « PROMPT 04 — Contrat API /api/v1 ».
Statut : contrat de référence défini pour les clients mobiles ; seuls les healthchecks de fondation sont implémentés. Aucun endpoint métier, JWT, token de rafraîchissement ou service n'est encore implémenté.

Références : [SPECIFICATIONS.md](SPECIFICATIONS.md), [ARCHITECTURE.md](ARCHITECTURE.md), [BUSINESS_RULES.md](BUSINESS_RULES.md), [DATA_MODEL.md](DATA_MODEL.md) et [PROGRESS.md](PROGRESS.md).

## 1. Portée, version et principes

Toutes les opérations mobiles utilisent HTTPS et le préfixe fixe `/api/v1`. L'API est JSON UTF-8 : `Content-Type: application/json` pour les requêtes avec corps et `Accept: application/json` pour les réponses. Aucun chemin API ne donne un accès MySQL, n'accède aux modèles directement ou ne reproduit les règles métier : route → validation de forme → service métier → PermissionService/repository contextualisé → réponse.

La navigation web continue d'utiliser Flask-Login et CSRF selon DEC-013. Les JWT décrits ici sont exclusivement acceptés par `/api/v1`; une session cookie web n'est jamais un substitut à `Authorization: Bearer <access_token>` dans l'API. Les routes mobiles mutantes ne reposent pas sur CSRF, mais exigent le bearer token, HTTPS et les contrôles de tenant/permission. Aucune donnée de carte, PAN, CVV, compte bancaire, secret de prestataire ou token brut n'est accepté, journalisé ou retourné.

`/health` est une sonde opérationnelle HTML hors contrat. `GET /api/v1/health` est le seul endpoint API déjà présent ; il respecte l'enveloppe de succès, est public et ne mesure pas MySQL. Toutes les autres routes listées sont des contrats à implémenter : leur présence dans ce document ne constitue pas un endpoint disponible ni une permission nouvelle.

## 2. Enveloppes communes

Succès :

```json
{
  "success": true,
  "data": {"id": "42", "name": "Lager"},
  "meta": {}
}
```

Erreur :

```json
{
  "success": false,
  "error": {
    "code": "BAR_SUSPENDED",
    "message": "Les écritures métier sont suspendues pour ce bar.",
    "details": null
  }
}
```

`success`, `data` et `meta` sont toujours présents dans un succès ; `success` et `error.code/message/details` le sont toujours dans une erreur. `data` est un objet pour une ressource, un tableau pour une collection et `null` pour une suppression logique acceptée. `meta` peut être `{}`. `details` est `null` sauf erreur de validation ou conflit où il ne contient que des champs, règles ou limites autorisés pour l'acteur — jamais une trace, SQL, secret, valeur d'un autre bar ou détail bancaire.

Toutes les clés JSON utilisent `snake_case`. Les identifiants BIGINT sont des chaînes décimales. Les montants sont des chaînes décimales sans séparateur de milliers, avec `currency` ISO 4217 associé ; exemples : `"1250.0000"`, `"XAF"`. Les quantités sont des chaînes avec au plus six décimales. Les instants sont ISO 8601 UTC avec `Z`, par exemple `"2026-09-09T12:30:00.000000Z"`. Les dates simples, lorsqu'une opération le demandera, sont `YYYY-MM-DD` et seront interprétées dans la timezone IANA du bar explicitement indiquée ; aucune date locale implicite.

Un GET ne provoque jamais d'écriture métier, d'audit de domaine ou de recalcul persistant. Hors réponse d'émission ou de rotation, les réponses ne retournent jamais `password_hash`, digest de session, refresh token, digest API, jti actif, secret, information bancaire sensible ou données hors tenant. Le refresh brut est retourné une seule fois par `POST /auth/tokens` ou `/auth/tokens/refresh` et n'est jamais ensuite relu depuis le serveur.

## 3. Authentification mobile et sélection du bar

### Jetons

`POST /api/v1/auth/tokens` reçoit identifiant, mot de passe, `bar_id` cible et l'empreinte facultative d'appareil non sensible. Il vérifie le compte actif et l'accès au bar ; l'accès au bar est contrôlé avant toute émission. Il retourne :

```json
{
  "success": true,
  "data": {
    "token_type": "Bearer",
    "access_token": "<jwt-redacted>",
    "access_expires_at": "2026-09-09T12:45:00Z",
    "refresh_token": "<opaque-token-redacted>",
    "refresh_expires_at": "2026-10-09T12:30:00Z",
    "bar": {"id": "12", "name": "Bar Central", "timezone": "Africa/Lagos", "currency": "XAF"}
  },
  "meta": {}
}
```

L'access token est un JWT JWS `ES256`, durée maximale **15 minutes**. Il porte exactement `iss`, `aud: "bar-manager-pro-api"`, `sub` (User.id), `bar_id`, `jti` UUID aléatoire, `iat`, `nbf`, `exp`, `token_use: "access"` et `credentials_version`. Il ne porte ni permission, ni rôle faisant autorité, ni nom, ni email, ni donnée financière. Le serveur impose l'algorithme ES256, valide signature, issuer, audience, `exp`/`nbf`, `token_use`, jti unique à l'émission et credentials_version. Les clés de signature, leur identifiant `kid`, issuer exact et rotation sont `EXTERNAL_PREREQUISITE` : configuration d'exploitation, jamais valeur de dépôt. Ces validations suivent les claims enregistrés, dont `exp` et `jti`, et les recommandations de validation JWT de [RFC 7519](https://www.rfc-editor.org/rfc/rfc7519) et [RFC 8725](https://www.rfc-editor.org/rfc/rfc8725).

Le refresh token est opaque, aléatoire sur 256 bits minimum, durée maximale **30 jours**, transporté uniquement dans le corps JSON de refresh/logout et jamais dans un cookie ou une URL. Il est stocké seulement sous SHA-256 dans `ApiToken`; la valeur brute n'est affichée qu'à l'émission ou à la rotation. `ApiToken.id` est le jti serveur, et deux colonnes de modèle requises avant implémentation sont `family_id BINARY(16) NOT NULL` et `rotated_from_id BIGINT UNSIGNED NULL`, avec FK même bar vers ApiToken. Elles permettent de révoquer une famille et de détecter la réutilisation. Cette évolution de colonnes, sans nouvelle table, est reflétée dans DATA_MODEL.md.

`POST /api/v1/auth/tokens/refresh` exige un refresh token valide. Il le verrouille, vérifie identité active, bar toujours accessible, affiliation/propriété actuelle, credentials_version, expiration et absence de TokenRevocation ; il crée un nouveau refresh de la même famille, révoque l'ancien et retourne une nouvelle paire. La réutilisation d'un refresh déjà roté ou révoqué révoque toute sa famille et retourne `401 REFRESH_TOKEN_REUSED`. La rotation est atomique. `POST /api/v1/auth/logout` reçoit le refresh token courant, révoque son jti et invalide la session mobile ; `POST /api/v1/auth/logout-all` incrémente credentials_version et révoque toutes les familles de l'utilisateur accessible à l'acteur. Une suspension ou désactivation de User invalide immédiatement access et refresh lors du contrôle serveur, et déclenche la révocation des familles en transaction. Un bar SUSPENDED n'émet ni ne rafraîchit de token : `403 BAR_SUSPENDED`.

Le refresh token définit le bar courant de la session mobile. Une identité propriétaire ayant plusieurs bars crée une paire distincte par bar via `/auth/tokens`; elle ne modifie jamais le `bar_id` d'un access token existant. SUPER_ADMIN demande explicitement un bar à l'émission ; l'accès reste audité. Chaque route tenant exige que le `{bar_id}` de son chemin égale le `bar_id` validé du JWT. Changer le chemin ou joindre l'identifiant d'un autre tenant donne `404 NOT_FOUND`, sans fuite. PermissionService recharge la catégorie, l'affiliation, la propriété et l'état courant depuis la base à chaque requête ; les claims ne contournent pas une révocation ou un changement de rôle.

### Endpoints d'authentification

| Méthode et chemin | Action / résultat | Authentification |
| --- | --- | --- |
| `POST /auth/tokens` | Émission access + refresh pour un bar accessible | Identifiant/mot de passe, rate limit connexion |
| `POST /auth/tokens/refresh` | Rotation atomique access + refresh | Refresh opaque |
| `POST /auth/logout` | Révocation du refresh courant | Refresh opaque |
| `POST /auth/logout-all` | Invalidation des familles de l'utilisateur | Bearer JWT ; compte propre |
| `GET /auth/me` | Identité, bar courant, permissions calculées éventuellement exposées comme capacités non autoritaires | Bearer JWT |
| `GET /health` | Sonde de disponibilité processus | Public |

`POST /auth/tokens` doit appliquer la limite `LOGIN_RATE_LIMIT` définie par la fondation, plus une limite par identifiant normalisé. Les réponses d'échec d'identifiant/mot de passe restent génériques (`401 INVALID_CREDENTIALS`) et ne révèlent aucun compte. Les endpoints auth doivent être implantés dans le module auth, appeler un service d'authentification et ne jamais manipuler les secrets dans une route.

## 4. Tenant, permissions et état suspendu

Les collections et ressources métier utilisent la forme canonique `/api/v1/bars/{bar_id}/…`. Aucun header `X-Bar-Id`, query string, rôle ou champ body ne sélectionne le tenant. Le `bar_id` de route est vérifié contre le JWT et les règles TEN-001 à TEN-008 avant de charger tout objet ou parent. Toute référence secondaire est chargée par `(bar_id, id)` ; la base applique les FK composites et les services répètent l'invariant.

Une route indique une permission PERM de BUSINESS_RULES.md. Le serveur appelle PermissionService dans le service avant tout effet. Une réponse 403 signifie une action connue mais interdite sur un bar accessible ; une ressource hors portée est 404. La visibilité d'un bouton ou d'une capacité renvoyée par `/auth/me` n'est jamais une autorisation.

Pour un bar ACTIVE, les opérations mutantes exigent permission, validation et invariants. Pour un bar SUSPENDED, le propriétaire garde les lectures permises ; les employés ne consultent plus les données métier ; un super-admin lit avec audit. Toute écriture commerciale retourne :

```json
{
  "success": false,
  "error": {
    "code": "BAR_SUSPENDED",
    "message": "Les écritures métier sont suspendues pour ce bar.",
    "details": {"bar_id": "12"}
  }
}
```

Les opérations de sécurité logout/révocation restent possibles. `bars.reactivate` reste l'opération de contrôle super-admin définie par PERM-008 ; elle est auditée et ne devient jamais un effet de gestion d'abonnement.

## 5. HTTP, validation et erreurs

| HTTP | Code stable | Quand l'utiliser |
| --- | --- | --- |
| 200 | `OK` | Lecture, édition ou action réussie sans création |
| 201 | `CREATED` | Ressource créée ; `Location` pointe vers sa ressource |
| 202 | `ACCEPTED` | Réservé à un futur traitement asynchrone traçable ; aucune opération actuelle ne l'emploie |
| 204 | jamais pour `/api/v1` | Les succès utilisent toujours l'enveloppe JSON |
| 400 | `INVALID_REQUEST` | JSON, type, champ protégé, taille ou format invalides |
| 401 | `AUTHENTICATION_REQUIRED`, `INVALID_TOKEN`, `TOKEN_EXPIRED`, `INVALID_CREDENTIALS`, `REFRESH_TOKEN_REUSED` | Absence/invalidité d'authentification |
| 403 | `FORBIDDEN`, `BAR_SUSPENDED` | Permission connue refusée ou écriture gelée dans un bar accessible |
| 404 | `NOT_FOUND` | Route/objet/tenant inexistant ou hors portée, même message générique |
| 409 | `IDEMPOTENCY_CONFLICT`, `STATE_CONFLICT`, `INVENTORY_STALE`, `CASH_SESSION_ALREADY_OPEN`, `REFERENCE_CONFLICT` | État ou clé incompatible sans effet partiel |
| 422 | `VALIDATION_FAILED`, `BUSINESS_RULE_VIOLATION`, `INSUFFICIENT_STOCK`, `PAYMENT_LIMIT_EXCEEDED`, `RETURN_LIMIT_EXCEEDED` | Forme JSON correcte mais règle de domaine non satisfaite |
| 429 | `RATE_LIMITED` | Limite de connexion ou d'API atteinte ; inclut `Retry-After` |
| 500 | `INTERNAL_SERVER_ERROR` | Erreur non prévue, sans détail interne |
| 503 | `AUDIT_UNAVAILABLE`, `SERVICE_UNAVAILABLE` | Audit requis ou dépendance indispensable indisponible ; aucun commit ni restitution protégée |

`VALIDATION_FAILED.details` est un objet de champs, par exemple `{"fields":{"quantity":"Doit être supérieure à zéro."}}`. `BUSINESS_RULE_VIOLATION.details` contient uniquement le code d'invariant public, par exemple `{"rule":"STK-004"}`. Les codes de conflit sont stables pour le client ; les messages sont destinés à l'utilisateur et peuvent être localisés dans une version ultérieure sans changer le code.

## 6. Collections, pagination, tri, recherche et filtres

Les GET de collection utilisent ces query parameters communs :

| Paramètre | Règle |
| --- | --- |
| `page[number]` | Entier >= 1, défaut 1 |
| `page[size]` | Entier 1..100, défaut 25 |
| `sort` | Liste séparée par virgule ; `-champ` décroissant, `champ` croissant ; seulement champs de la route |
| `q` | Recherche texte normalisée, max 100 caractères, sur champs documentés de la route |
| `filter[champ]` | Filtre exact/énuméré documenté ; valeurs répétées = OR dans ce champ, champs distincts = AND |
| `filter[created_from]`, `filter[created_to]` | Instants UTC ISO 8601 ; borne inférieure incluse, supérieure exclusive |
| `filter[date]` | Journée locale du bar, convertie en intervalle UTC semi-ouvert |

Chaque collection fournit `meta` ainsi :

```json
{
  "success": true,
  "data": [],
  "meta": {
    "page": {"number": 1, "size": 25, "total_items": 0, "total_pages": 0},
    "sort": ["-created_at", "id"],
    "filters": {"status": ["POSTED"]}
  }
}
```

Le serveur ajoute toujours `id` comme dernier tri déterministe. Le filtrage tenant est appliqué avant recherche, total, tri et pagination. Aucun endpoint ne propose une recherche globale entre bars. Si un champ de tri ou filtre n'est pas permis par la route : 400 `INVALID_REQUEST`; les dates invalides ou `created_from >= created_to` échouent aussi 400. Chaque tableau de routes §9 indique les filtres et tris autorisés ; sans indication, seul pagination et `sort=-created_at,id` sont permis.

## 7. Idempotence des mutations sensibles

`Idempotency-Key` est obligatoire pour : création/validation de commande, enregistrement de paiement, remboursement, POST d'une remise CashHandover, validation d'achat/réception (`POST …/purchases/{id}/post`) et toute future écriture externe. Il est recommandé pour toute autre création. Une clé est une chaîne opaque 16..128 octets ASCII ; elle ne va ni dans une URL ni dans les logs.

Le service calcule `request_hash = SHA-256` de la méthode HTTP, du chemin canonique avec bar, de l'opération métier, et du JSON canonique après validation de forme. Le serveur utilise `IdempotencyRecord` unique `(bar_id, actor_id, operation, idempotency_key)` dans la même transaction que le document, les journaux, l'audit et la réponse. Une ligne `PROCESSING` non achevée n'est jamais commitée seule.

| Cas | Réponse et effet |
| --- | --- |
| Première clé valide | Exécute une fois, conserve statut 2xx + corps expurgé, retourne le résultat avec `Idempotency-Replayed: false` |
| Même clé, même hash, même acteur/bar/opération | Ne réexécute rien ; revalide auth, tenant et permission de lecture du résultat ; retourne le résultat mémorisé avec `Idempotency-Replayed: true` |
| Même clé, hash différent | 409 `IDEMPOTENCY_CONFLICT`, `details: {"operation":"payments.record"}` ; aucun effet |
| Même clé hors tenant, autre acteur, autre opération | Pas de correspondance visible ; nouvelle portée ou 404/403 selon contrôle normal, jamais réponse mémorisée étrangère |
| Clé conservée mais acteur perd droits/bar suspendu | Le résultat n'est pas restitué si lecture interdite ; réponse 403/404/BAR_SUSPENDED selon règle actuelle, sans rejouer l'écriture |

Le corps stocké est une représentation sûre du résultat, sans access/refresh token, digest, secret, détails bancaires, données d'un autre bar ou informations internes. La clé est liée à l'acteur, au tenant et à l'opération : ce triple verrou, suivi de PermissionService avant restitution, empêche une fuite inter-tenant. La politique de rétention reste ouverte ; aucune purge/réutilisation n'est permise avant sa définition.

## 8. Ressources JSON partagées

Les réponses de liste exposent les projections adaptées aux permissions ; par exemple `catalog.read` ne divulgue pas `valuation_unit_cost` aux rôles sans droit financier. Les champs `*_snapshot` et les références historiques sont en lecture seule. `bar_id`, `owner_id`, `created_by_id`, `recorded_by_id`, statut de sécurité, jti, digests, totaux dérivés et timestamps serveur sont protégés : les schémas les refusent dans un body non explicitement prévu.

Les créations reçoivent les valeurs métier, pas bar_id. L'API tire bar_id du chemin validé. Les opérations `post`, `close`, `cancel`, `reactivate`, `refresh` et `logout` sont des commandes explicites, pas des PATCH génériques. Un PATCH ne peut modifier que les champs éditables d'un brouillon ou d'un référentiel actif ; aucune mutation du tenant, de la propriété, de la devise historique, d'un snapshot validé ou d'une écriture append-only.

## 9. Catalogue des endpoints

Les permissions viennent de PERM-001 à PERM-032. « À définir » signifie : contrat de chemin réservé, mais aucun client ne doit l'utiliser tant qu'une permission et une règle métier ne sont pas ajoutées. Toutes les routes tenant commencent par `/api/v1/bars/{bar_id}` ; ce préfixe est omis dans le tableau.

### Bars, personnel, catalogue et stock

| Méthode / chemin relatif | Permission | Usage, filtres et tri |
| --- | --- | --- |
| `GET /bars` | PERM-003 | Super-admin : liste plateforme, `status`, `owner_id`, `q`, `-created_at,id` ; métadonnées seulement |
| `POST /bars` | PERM-004 | Création contrôle ; owner existant + timezone IANA ; audit |
| `GET /bars/{bar_id}` | PERM-005 | Bar courant ; lecture auditée pour super-admin |
| `PATCH /bars/{bar_id}` | PERM-006 | Paramètres éditables, pas propriétaire/devise historique |
| `POST /bars/{bar_id}/suspend` | PERM-007 | Commande super-admin avec reason |
| `POST /bars/{bar_id}/reactivate` | PERM-008 | Commande super-admin avec reason |
| `GET /staff-assignments` | PERM-009 | `role`, `active`, `q`, `-started_at,id` |
| `POST /staff-assignments` | PERM-010 | Crée affiliation ; règle d'unicité active |
| `PATCH /staff-assignments/{id}` | PERM-010 | Termine/renouvelle affiliation ; jamais promotion globale |
| `GET /product-categories` / `POST /product-categories` | PERM-011 / 012 | `is_active`, `q`, `name,id` |
| `GET/PATCH /product-categories/{id}` | PERM-011 / 012 | Lecture, désactivation ou changement descriptif |
| `GET /products` / `POST /products` | PERM-011 / 012 | `category_id`, `is_active`, `sku`, `q`, `name,id` |
| `GET/PATCH /products/{id}` | PERM-011 / 012 | Prix courant éditable ; snapshots non modifiables |
| `GET /stock-balances` | PERM-013 | `product_id`, `q`, `product_name,id` ; projection lecture seule |
| `GET /stock-movements` | PERM-013 | `product_id`, `source`, dates, `-occurred_at,id` ; append-only |
| `POST /stock-adjustments` | PERM-014 | À définir : origine ADJUSTMENT, reason, quantité ; ne contourne pas les règles STK |
| `GET /inventories` / `POST /inventories` | PERM-013 / 014 | `status`, dates, `-counted_at,id` |
| `GET/PATCH /inventories/{id}` | PERM-013 / 014 | Brouillon seulement |
| `POST /inventories/{id}/post` / `POST /inventories/{id}/cancel` | PERM-014 | Validation/conflit `INVENTORY_STALE`, annulation |

### Fournisseurs, achats et règlements fournisseurs

| Méthode / chemin relatif | Permission | Usage, filtres et tri |
| --- | --- | --- |
| `GET /suppliers` / `POST /suppliers` | PERM-015 / 016 | `is_active`, `q`, `name,id` |
| `GET/PATCH /suppliers/{id}` | PERM-015 / 016 | Lecture, descriptif, désactivation |
| `GET /purchases` / `POST /purchases` | PERM-017 / 018 | `status`, `supplier_id`, dates, `-created_at,id` |
| `GET/PATCH /purchases/{id}` | PERM-017 / 018 | DRAFT seulement |
| `POST /purchases/{id}/lines` / `PATCH /purchases/{id}/lines/{line_id}` | PERM-018 | Lignes DRAFT ; snapshots à validation |
| `POST /purchases/{id}/post` / `POST /purchases/{id}/cancel` | PERM-018 | Idempotency-Key obligatoire à post ; réception complète ou annulation |
| `GET /supplier-payments` / `POST /supplier-payments` | À définir | `purchase_id`, dates, méthode ; contrat réservé, aucune permission actuelle |
| `POST /supplier-payments/{id}/reverse` | À définir | Contre-écriture liée, jamais suppression |

### Tables, clients, commandes, retours, paiements et remboursements

| Méthode / chemin relatif | Permission | Usage, filtres et tri |
| --- | --- | --- |
| `GET /tables` / `POST /tables` / `PATCH /tables/{id}` | À définir | Tables du bar, `is_active`, `q`, `label,id` |
| `GET /customers` / `POST /customers` / `PATCH /customers/{id}` | À définir | `is_active`, `q`, `phone`, `display_name,id` |
| `GET /orders` / `POST /orders` | PERM-019 / 020 | `status`, `table_id`, `customer_id`, `assigned_staff_id`, dates ; `-created_at,id`; Idempotency-Key obligatoire à création |
| `GET /orders/{id}` / `PATCH /orders/{id}` | PERM-019 / 021 | PATCH DRAFT seulement |
| `POST /orders/{id}/lines` / `PATCH /orders/{id}/lines/{line_id}` | PERM-021 | DRAFT seulement ; `product_id` du même bar |
| `POST /orders/{id}/post` / `POST /orders/{id}/close` / `POST /orders/{id}/cancel` | PERM-021 | Idempotency-Key obligatoire à post ; conditions de close à compléter |
| `GET /order-returns` / `POST /order-returns` | À définir | `order_id`, `status`, dates ; retour et restock éventuel |
| `GET/PATCH /order-returns/{id}` | À définir | DRAFT seulement |
| `POST /order-returns/{id}/post` / `POST /order-returns/{id}/cancel` | À définir | Retour commercial, sans remboursement implicite |
| `GET /payments` / `POST /payments` | PERM-022 / 023 | `order_id`, `method`, dates ; Idempotency-Key obligatoire pour POST |
| `GET /payments/{id}` | PERM-022 | Paiement validé immuable |
| `GET /refunds` / `POST /refunds` | À définir | `payment_id`, `order_return_id`, dates ; Idempotency-Key obligatoire |

### Caisse, dépenses, rapports, abonnement et audit

| Méthode / chemin relatif | Permission | Usage, filtres et tri |
| --- | --- | --- |
| `GET /cash-sessions` / `POST /cash-sessions` | PERM-024 / 025 | `status`, dates ; POST ouvre, conflit `CASH_SESSION_ALREADY_OPEN` |
| `GET /cash-sessions/{id}` / `POST /cash-sessions/{id}/close` | PERM-024 / 025 | Détail et clôture explicite |
| `GET /cash-movements` | PERM-024 | `cash_session_id`, `source`, dates, `-occurred_at,id` |
| `POST /cash-deposits` / `POST /cash-withdrawals` | PERM-025 | À définir : mouvement manuel justifié, jamais modification du passé |
| `GET /staff-cash-ledgers` / `GET /cash-handovers` | PERM-024 | `staff_assignment_id`, `status`, dates |
| `POST /cash-handovers` / `POST /cash-handovers/{id}/post` / `POST /cash-handovers/{id}/cancel` | PERM-025 | Idempotency-Key obligatoire à post ; garde/tiroir atomiques |
| `GET /expense-categories` / `POST /expense-categories` / `PATCH /expense-categories/{id}` | PERM-026 / 027 | `is_active`, `q`, `name,id` |
| `GET /expenses` / `POST /expenses` | PERM-026 / 027 | `expense_category_id`, dates, méthode, `-incurred_at,id` |
| `POST /expenses/{id}/reverse` | PERM-027 | Contre-écriture liée, jamais DELETE |
| `GET /reports/{report_name}` | PERM-028 | `report_name` dans liste fermée publiée ultérieurement ; filtres de période ; lecture seule |
| `GET /reports/{report_name}/export` | PERM-029 | Format explicitement accepté, même portée tenant |
| `GET /subscription` | PERM-030 | Abonnement courant et snapshots autorisés |
| `GET /subscription-payments` / `POST /subscription-payments` | À définir | Lecture/écriture plateforme à spécifier ; aucun mouvement de caisse implicite |
| `GET /audit-logs` | PERM-032 | Owner ou super-admin ; `actor_id`, `action`, `outcome`, dates, `-occurred_at,id` ; champs expurgés |

Les routes d'abonnement globales `GET/POST/PATCH /api/v1/plans` et opérations super-admin de cycle Subscription sont à définir avec les permissions plateforme correspondantes. Elles ne doivent pas apparaître simplement parce que Plan existe dans le modèle. De même, les permissions des retours, remboursements, règlements fournisseur, tables, clients, dépôts/retraits et jetons restent un prérequis explicite avant implémentation.

## 10. Exemples de parcours mobiles

Création de commande (sémantique, endpoint non encore implémenté) :

```http
POST /api/v1/bars/12/orders
Authorization: Bearer <access-token>
Idempotency-Key: 7ee5f7b0-5f5b-4a46-8b13-32fe7d6c5d66
Content-Type: application/json

{"table_id":"7","customer_id":"9"}
```

```json
{
  "success": true,
  "data": {"id":"501","reference":"ORD-000501","status":"DRAFT","currency":"XAF"},
  "meta": {}
}
```

Une répétition strictement identique retourne ce résultat logique, jamais une seconde commande. Changer `table_id` avec la même clé donne 409 `IDEMPOTENCY_CONFLICT`. Le POST de paiement et de remboursement emploie la même règle avant toute écriture CashMovement, StaffCashLedger ou Refund.

Liste de produits :

```http
GET /api/v1/bars/12/products?page[number]=1&page[size]=25&filter[is_active]=true&sort=name
Authorization: Bearer <access-token>
```

L'API retourne uniquement les produits de 12 que l'acteur peut lire ; elle ne retourne aucun coût de valorisation au serveur/serveuse. Un access token de 13 appelant ce chemin reçoit 404 `NOT_FOUND`.

## 11. Conditions d'implémentation et vérification

Avant d'implémenter une route, son service doit avoir : une permission référencée ou une nouvelle permission approuvée, ses préconditions/effets BUSINESS_RULES, son schéma DATA_MODEL et des tests HTML/API de tenant, erreur et idempotence. Les routes existantes de fondation doivent rester compatibles avec l'enveloppe ; `GET /api/v1/health` est donc normalisé dans le socle.

AC-API-001 : un mobile authentifié peut lister, filtrer, trier et paginer chaque ressource autorisée de son bar sans accès direct à MySQL.

AC-API-002 : un propriétaire multi-bar utilise une paire de tokens par bar ; changer le bar du chemin ou d'une référence secondaire ne divulgue rien et ne crée rien.

AC-API-003 : un access expiré échoue 401 ; un refresh valide le renouvelle une fois ; la réutilisation de l'ancien refresh révoque la famille.

AC-API-004 : désactivation User, changement d'affiliation, suspension Bar ou révocation empêchent la prochaine opération concernée selon les règles actuelles, même si l'access JWT n'a pas expiré.

AC-API-005 : une clé idempotente identique rejoue sans double effet ; une même clé/hash différent donne 409 ; un autre tenant/acteur ne reçoit jamais le résultat mémorisé.

AC-API-006 : chaque mutation sensible crée tous ses effets ou aucun, y compris audit, idempotence, stock, caisse et garde ; une réponse d'erreur ne cache pas de commit partiel.

AC-API-007 : aucun endpoint mobile ne retourne données bancaires sensibles, secret, hash, jti actif, refresh token stocké ou détail d'un autre tenant.

Revue documentaire effectuée : l'ensemble des modules et tables connus possède un chemin mobile proposé ; les capacités sans permission restent explicitement bloquées. Aucun endpoint métier ne peut être déclaré utilisable avant implémentation de son service et exécution de ces critères. EXT-001 reste nécessaire pour vérifier que le futur cahier des charges n'exige pas d'autres ressources ou règles.
