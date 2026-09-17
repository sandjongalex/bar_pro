# Bar Manager Pro — Spécification du comportement actuel

Ce document décrit le comportement réellement observé dans `app/` et `tests/`.
[`SPECIFICATIONS.md`](SPECIFICATIONS.md) reste l'historique des exigences et
du cahier des charges incomplet ; ne pas l'utiliser comme preuve de livraison.

Niveaux de preuve : **testé** = code couvert par tests automatisés locaux ;
**observé code** = route/service/template présent sans preuve complète ; **documenté**
= exigence conservée dans les documents historiques, non prouvée livrée.

## Synthèse par domaine

| Domaine | État | Preuves principales |
| --- | --- | --- |
| Authentification web/API | testé | `app/auth.py`, `tests/test_auth.py`, `tests/test_workflows.py` |
| Tenant, rôles, suspension | testé | `app/permissions.py`, `tests/test_workflows.py`, `tests/test_finance.py`, `tests/test_inventories.py` |
| Bars/personnel | partiel | `app/bars.py`, `app/bar_services.py`, `tests/test_workflows.py` |
| Catalogue | partiel | `app/catalog.py`, `app/catalog_services.py`, `tests/test_workflows.py` |
| Stock | testé | `app/stock_service.py`, `tests/test_workflows.py`, `tests/test_inventories.py` |
| Inventaires | testé | `app/inventory_services.py`, `app/inventories.py`, `tests/test_inventories.py` |
| Fournisseurs/achats | testé API/service | `app/purchase_services.py`, `tests/test_workflows.py` |
| Commandes/retours | testé | `app/order_services.py`, `tests/test_workflows.py` |
| Paiements/remboursements | testé | `app/payment_services.py`, `tests/test_finance.py`, `tests/test_workflows.py` |
| Caisse/garde/remises | testé | `app/cash_services.py`, `tests/test_finance.py`, `tests/test_workflows.py` |
| Interfaces web disponibles | testé HTTP partiel | `app/templates/`, `tests/test_workflows.py`, `tests/test_finance.py`, `tests/test_inventories.py` |

## Comportements implémentés

### Authentification et sessions

| Élément | Description |
| --- | --- |
| Objectif | Authentifier les utilisateurs web et API, séparer cookie web et Bearer API. |
| Acteurs | Utilisateur actif `SUPER_ADMIN`, `OWNER` ou `EMPLOYEE`. |
| Préconditions | Compte actif, mot de passe valide, bar accessible pour l'API. |
| Entrées | Web : email, mot de passe, CSRF. API : JSON email, mot de passe, `bar_id`, puis refresh token opaque. |
| Traitement | Web : `login_user`, session signée, `credentials_version`. API : émission access JWT ES256 15 min et refresh opaque stocké par empreinte SHA-256. |
| Règles | Un cookie web n'authentifie jamais une route API ; `api_required` vérifie issuer, audience, dates, `token_use`, `credentials_version`, `sid`, révocation et bar signé. |
| Sorties | Web : redirection. API : enveloppe `{success,data,meta}` avec `access_token` et `refresh_token`. |
| Erreurs | 400 CSRF web manquant ; 401 credentials/token invalides ; 404 bar inaccessible à l'émission. |
| Permissions | L'accès métier reste soumis à `PermissionService`; l'authentification seule ne donne pas de droit métier. |
| Effets | `ApiToken` et `TokenRevocation` pour refresh/rotation/logout ; pas de `UserSession` opérationnel. |
| Preuve | `app/auth.py`; `tests/test_auth.py::test_api_login_refresh_logout_and_disabled`, `tests/test_workflows.py::test_refresh_reuse_revokes_family_and_access`, `test_logout_revokes_access_and_refresh`, `test_jwt_expired_missing_claim_wrong_audience_and_version`. |

### Tenant, rôles et suspension

| Élément | Description |
| --- | --- |
| Objectif | Isoler chaque bar et appliquer les rôles serveur. |
| Acteurs | `SUPER_ADMIN`, `OWNER`, employés affiliés `BAR_ADMIN`, `CASHIER`, `SERVER`. |
| Préconditions | `bar_id` de route ou de token, utilisateur actif, affiliation active pour employé. |
| Traitement | `PermissionService.evaluate` déduit le rôle depuis `User.category`, `Bar.owner_id` ou `StaffAssignment`. `permissions.require` verrouille le bar pour actions d'écriture listées. |
| Règles | Propriétaire limité à ses bars ; employé limité à son bar ; objet hors bar traité comme indisponible. Bar suspendu : écritures refusées et employés bloqués. |
| Erreurs | 401 sans Bearer API ; 403 permission refusée ; 404 bar/objet hors portée dans plusieurs routes. |
| Effets | Aucun effet métier en cas de refus ; certains refus passent par rollback global. |
| Preuve | `app/permissions.py`, hook `tenant_web_guard`; `tests/test_workflows.py::test_api_tenant_and_bearer`, `test_web_csrf_login_and_foreign_routes`, `test_suspended_bar_writes_denied`; `tests/test_inventories.py::test_api_read_cancel_permissions_and_scope`; `tests/test_finance.py::test_refund_suspension_and_server_denied`. |

Exception : `bar_services.py` utilise un helper local fondé sur `permissions.evaluate`, pas `permissions.require`, pour `update_bar` et `assign_staff`; ne pas supposer un verrou uniforme sur ces chemins.

### Bars et personnel

| Élément | Description |
| --- | --- |
| Objectif | Créer un bar, modifier ses paramètres, affecter un employé. |
| Acteurs | Super-admin pour création ; `SUPER_ADMIN`/`OWNER` pour paramètres et staff selon `ROLE_ACTIONS`. |
| Préconditions | Propriétaire cible actif et catégorie `OWNER`; employé cible catégorie `EMPLOYEE`; rôle local valide. |
| Entrées | Bar : `owner_id`, `name`, `timezone`, devise facultative, seuil stock, copie facultative. Staff : `user_id`, `role`. |
| Traitement | `create_bar` crée `Bar`, peut copier catégories/produits d'un bar du même propriétaire ; `assign_staff` crée `StaffAssignment`. |
| Règles | Changement de devise refusé ; `logo_key` refusé ; timezone validée ; rôles locaux limités à `BAR_ADMIN`, `CASHIER`, `SERVER`. |
| Sorties | API bars sérialise identifiant, nom, statut, timezone, devise, contacts et paramètres. |
| Erreurs | `FORBIDDEN`, `OWNER_NOT_FOUND`, `INVALID_STAFF`, `CURRENCY_IMMUTABLE`, `LOGO_STORAGE_UNAVAILABLE`. |
| Effets | Création bar, copie catalogue sans stock initial autre que soldes zéro, création affiliation. |
| Preuve | `app/bars.py`, `app/bar_services.py`; preuves surtout indirectes dans fixtures et tests tenant. Tests complets bars/personnel indiqués partiels dans `docs/PROGRESS.md`. |

### Catalogue

| Élément | Description |
| --- | --- |
| Objectif | Lister et créer des produits propres à un bar. |
| Acteurs | Lecture : tous rôles autorisés ; gestion : `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN`. |
| Préconditions | Catégorie existante dans le même bar ; produit actif pour les workflows de vente/achat. |
| Entrées | `category_id`, `sku`, `name`, `base_unit`, `sale_price`, `valuation_unit_cost`, seuil facultatif, `units_per_case`. |
| Traitement | Validation Decimal, rattachement au bar, pagination de liste, filtre q/catégorie/actif. |
| Règles | Prix/coûts non négatifs ; upload image refusé (`IMAGE_STORAGE_UNAVAILABLE`). |
| Sorties | Produit API sans coût de valorisation dans la réponse catalogue. |
| Erreurs | 400 ou 404 selon route ; précision ou valeurs invalides refusées. |
| Effets | Création produit ; pas de mouvement de stock automatique. |
| Preuve | `app/catalog_services.py`, `app/catalog.py`; `tests/test_workflows.py::test_api_tenant_and_bearer`, `test_bad_quantities` pour validation numérique consommée par commandes. CRUD complet catégories/produits reste partiel dans `docs/PROGRESS.md`. |

### Stock

| Élément | Description |
| --- | --- |
| Objectif | Centraliser les mutations de quantité et maintenir `StockBalance`. |
| Acteurs | Selon type : inventaire pour `INITIAL/ADJUSTMENT/LOSS/INVENTORY_ADJUSTMENT`, commandes pour `SALE/RETURN`, achats pour `PURCHASE`. |
| Préconditions | Produit du bar, raison non vide, quantité finie à six décimales, sens cohérent. |
| Entrées | Type mouvement, produit, delta, raison, référence secondaire facultative. |
| Traitement | `StockService.move` verrouille produit et solde, crée un solde si absent, valide les références secondaires et écrit `StockMovement`. |
| Règles | Stock négatif refusé ; `INITIAL` unique ; route manuelle limitée à `INITIAL`, `ADJUSTMENT`, `LOSS`; références secondaires doivent partager bar et produit. |
| Sorties | Mouvement stock et solde/version mis à jour. |
| Erreurs | `INVALID_MOVEMENT_TYPE`, `INVALID_DIRECTION`, `ZERO_QUANTITY`, `INSUFFICIENT_STOCK`, `NOT_FOUND`. |
| Effets | Alimente ventes, achats, retours et inventaires ; versions utilisées par inventaire. |
| Preuve | `app/stock_service.py`; `tests/test_workflows.py::test_order_server_confirm_serve_and_stock_once`, `test_cancel_restores_stock_once`, `test_failed_multiline_confirm_rolls_back`, `test_foreign_secondary_reference_rejected_by_service_and_database`; `tests/test_inventories.py`. |

### Inventaires

| Élément | Description |
| --- | --- |
| Objectif | Compter physiquement des produits, comparer au stock attendu et ajuster atomiquement. |
| Acteurs | `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN` via `inventory.adjust`; lecture via `inventory.read`. |
| Préconditions | Bar actif, produits du bar, référence/motif non vides, identifiants produits valides et uniques. |
| Entrées | Création : `reference`, `product_ids`, `reason`. Comptage : dictionnaire `product_id -> quantité`. Validation : aucune entrée supplémentaire. |
| Traitement | Création capture `expected_quantity_snapshot` et `balance_version_snapshot`. Comptage met à jour les lignes. Validation exige toutes les quantités et versions inchangées, puis appelle `stock_service.move` pour chaque écart non nul. |
| Règles | État `DRAFT` seul modifiable ; `POSTED` et `CANCELLED` figés ; quantité non négative, finie, six décimales max ; absence de mouvement si écart nul. |
| Sorties | Payload inventaire avec lignes, quantités attendues/comptées/différences ; pages web liste, détail et impression. |
| Erreurs | 404 ressource/produit hors bar ; 409 validation incomplète, obsolète ou état terminé ; 422 saisie invalide. |
| Permissions | `inventory.read` pour lire, `inventory.adjust` pour créer/compter/poster/annuler. |
| Effets | Mouvements `INVENTORY_ADJUSTMENT`, mise à jour stock/version, rollback complet si un ajustement échoue. |
| Preuve | `app/inventory_services.py`, `app/inventories.py`, templates `inventory_*`; `tests/test_inventories.py::*`, notamment `test_post_snapshot_difference_and_exactly_once`, `test_incomplete_stale_multiline_is_atomic`, `test_failure_during_stock_adjustment_rolls_back_every_line`, `test_web_create_count_post_print_with_csrf`. |

### Fournisseurs et achats

| Élément | Description |
| --- | --- |
| Objectif | Gérer fournisseurs, achats brouillon, réception et règlement fournisseur. |
| Acteurs | `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN`; serveur refusé. |
| Préconditions | Fournisseur actif du bar, produits du bar, lignes non vides. |
| Entrées | Fournisseur : nom/contact. Achat : fournisseur, référence, lignes `product_id/quantity/unit_cost`. Paiement : référence, montant, méthode, motif, caisse pour espèces. |
| Traitement | Achat `DRAFT` avec snapshots fournisseur/produit/unité/coût ; `receive` poste tout l'achat et crée mouvements `PURCHASE`; paiement fournisseur calcule dette et peut créer mouvement de caisse si `CASH`. |
| Règles | Achat reçu une seule fois ; brouillon modifiable/annulable ; montant règlement <= dette ; contre-écriture unique de paiement fournisseur. |
| Sorties | API fournisseurs, achats, balance fournisseur, supplier payments. |
| Erreurs | 403 rôle sans droit ; 422 transition invalide ; `SUPPLIER_PAYMENT_LIMIT`, `PURCHASE_NOT_DRAFT`, `PURCHASE_NOT_PAYABLE`. |
| Effets | Stock augmenté à réception ; dette fournisseur calculée ; caisse débitée/créditée pour règlements espèces et reversals. |
| Preuve | `app/purchase_services.py`, `app/purchases.py`; `tests/test_workflows.py::test_purchase_receipt_and_duplicate_prevention`, `test_supplier_purchase_payment_and_reversal_workflow`, `test_cash_supplier_payment_updates_drawer`, `test_purchase_cancel_supplier_api_and_permissions`, `test_server_cannot_read_supplier_debt`. |

### Commandes et retours commerciaux

| Élément | Description |
| --- | --- |
| Objectif | Créer, confirmer, servir, annuler ou retourner une commande. |
| Acteurs | `orders.create` et `orders.edit` : tous rôles incluant `SERVER` dans le bar. |
| Préconditions | Produits actifs du bar, quantités positives, stock suffisant à confirmation. |
| Entrées | Commande : référence, lignes, table/client/notes facultatifs. Retour : lignes originales, quantité, disposition `RESTOCK` ou `LOSS`, motif. |
| Traitement | Création en `DRAFT`; confirmation retire le stock ; service passe `SERVED`; annulation restaure si non encaissée ; retour crée `OrderReturn` et `OrderReturnLine`. |
| Règles | Cycle implémenté `DRAFT -> CONFIRMED -> SERVED`. Lignes éditables seulement en `DRAFT`. Retrait du stock à confirmation uniquement, aucun second retrait au service. Annulation `DRAFT` ou `CONFIRMED` non encaissée ; `CONFIRMED` encaissée exige remboursement via finance ; `SERVED` passe par retours. |
| Retours | Cumul retourné <= quantité vendue. `RESTOCK` réintègre le stock ; `LOSS` ne retire rien de plus. Crédit retour calculé au prix historique snapshot. |
| Sorties | Statuts commande, documents de retour, solde financier recalculé. |
| Erreurs | `ORDER_NOT_DRAFT`, `ORDER_EMPTY`, `ORDER_NOT_CONFIRMED`, `ORDER_NOT_CANCELLABLE`, `REFUND_REQUIRED`, `RETURN_LIMIT_EXCEEDED`. |
| Effets | Stock `SALE`/`RETURN`, `payment_status` recalculé, audit pour annulation/retour. |
| Preuve | `app/order_services.py`, `app/orders.py`; `tests/test_workflows.py::test_order_server_confirm_serve_and_stock_once`, `test_cancel_restores_stock_once`, `test_return_persists_and_enforces_cumulative_limit`, `test_snapshot_price_is_used_after_catalogue_change`, `test_web_order_form_creates_and_confirms`. |

### Paiements et remboursements

| Élément | Description |
| --- | --- |
| Objectif | Enregistrer des encaissements manuels et restitutions d'argent traçables. |
| Acteurs | `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN`, `CASHIER`; `SERVER` refusé. |
| Préconditions | Commande `CONFIRMED` ou `SERVED`; montant appliqué positif ; paiement non supérieur au reste dû. |
| Entrées | Paiement : commande, référence, méthode, montant présenté, montant appliqué, monnaie rendue, caisse ou garde si espèces, référence prestataire hors espèces facultative. Remboursement : paiement, référence, montant, motif, retour facultatif, lieu espèces si nécessaire. |
| Traitement | Paiement crée `Payment`, écrit caisse/garde si `CASH`, recalcule solde. Remboursement crée `Refund`, sort les espèces si besoin, recalcule solde. |
| Règles | Paiement partiel autorisé ; `amount_presented = amount_applied + change_given`; monnaie rendue seulement hors dette mais pas stockée comme mouvement positif ; hors espèces saisi manuellement, sans prestataire. |
| Remboursement | Distinct du retour commercial. Sans retour : correction de paiement pouvant rouvrir une dette. Avec retour : même commande, retour `POSTED`, cumul <= crédit du retour, <= paiement initial et <= trop-perçu courant. |
| Sorties | Paiement/remboursement, balance `total_paid`, `total_refunded`, `return_credit`, `net_sale`, `net_paid`, `amount_due`, `refundable_overpayment`. |
| Erreurs | 403 rôle/suspension ; 404 référence absente ; 409 doublon référence SQL ; 422 limite ou validation. |
| Effets | Caisse/garde si espèces, statut paiement commande, annulation encaissée possible par `cancel_paid`. |
| Preuve | `app/payment_services.py`, `app/finance.py`; `tests/test_workflows.py::test_payment_partial_cash_change_and_close`, `test_payment_rejects_draft_and_server`; `tests/test_finance.py::test_return_refund_and_repayment`, `test_paid_cancellation_atomic`, `test_insufficient_cash_rolls_back_cancellation`, `test_finance_api_permissions_and_refund_limits`, `test_duplicate_refund_rolls_back_and_foreign_return_rejected`. |

### Caisse, garde et remises

| Élément | Description |
| --- | --- |
| Objectif | Gérer tiroir de caisse, espèces détenues par employé et transfert vers tiroir. |
| Acteurs | `cash.operate` : `SUPER_ADMIN`, `OWNER`, `BAR_ADMIN`, `CASHIER`. |
| Préconditions | Bar actif, caisse ouverte pour mouvements tiroir, affiliation staff existante pour garde. |
| Entrées | Session : référence, fond initial, montant compté. Mouvement : dépôt/retrait, montant, motif. Handover : staff, caisse, référence, montant. |
| Traitement | Ouverture crée `CashSession`; paiements/remboursements espèces appellent `cash_service.entry`; clôture calcule attendu ; handover `DRAFT` puis `POSTED` transfère garde -> tiroir. |
| Règles | Une seule caisse ouverte par bar ; sortie ne peut pas rendre tiroir ou garde négatif ; motif obligatoire en cas d'écart clôture et mouvements manuels ; contrepassation manuelle unique ; handover DRAFT seulement postable/annulable. |
| Sorties | Session, mouvements de caisse, ledger staff, remise, solde attendu et solde de garde. |
| Erreurs | Caisse fermée, caisse du mauvais bar, espèces insuffisantes, reversal double, handover non DRAFT. |
| Effets | Met à jour les journaux financiers sans réécrire l'historique ; aucune nouvelle vente lors d'une remise. |
| Preuve | `app/cash_services.py`, `app/finance.py`, `app/finance_web.py`; `tests/test_finance.py::test_staff_handover_and_refund`, `test_manual_movements_reversal_and_closed_guard`, `test_web_finance_csrf_and_workflow`; `tests/test_workflows.py::test_cash_duplicate_closed_and_foreign_rejected`. |

### Interfaces web disponibles

| Interface | Comportement actuel | Preuve |
| --- | --- | --- |
| Login | Formulaire CSRF, session web, redirection. | `app/auth.py`, `tests/test_auth.py`, `tests/test_workflows.py::test_web_csrf_login_and_foreign_routes`. |
| Dashboard | Liste les bars accessibles et actions rapides. | `app/web.py`, `app/templates/dashboard.html`. |
| Bars | Liste texte simple des bars accessibles. | `app/bars.py`. |
| Catalogue | Recherche/liste produits. | `app/catalog.py`, `app/templates/catalog.html`. |
| Stock | Historique paginé. | `app/stock.py`, `app/templates/stock.html`. |
| Commande rapide | Crée et confirme une commande en un POST. | `app/orders.py`, `app/templates/order_quick.html`, `test_web_order_form_creates_and_confirms`. |
| Inventaires | Création, comptage, validation, annulation, impression. | `app/inventories.py`, `app/templates/inventory_*`, `test_web_create_count_post_print_with_csrf`. |
| Finance | Paiements, remboursements, caisse, mouvements, remises. | `app/finance_web.py`, `app/templates/finance.html`, `test_web_finance_csrf_and_workflow`. |

## Fonctionnalités partielles

| Domaine | Partiel constaté |
| --- | --- |
| Bars/personnel | Pas de tests complets de création/copie/paramétrage/affiliation ; suspension/réactivation exposées partiellement ; logos refusés. |
| Catalogue | CRUD catégories absent côté API actuelle ; upload produit refusé ; alertes stock limitées aux soldes existants. |
| Commandes | Pas de correction différentielle après confirmation ; tables/clients existent dans le modèle mais workflows limités ; crédit/fiscalité à déterminer. |
| Paiements externes | `CARD`, `MOBILE_MONEY`, `BANK_TRANSFER` sont des saisies manuelles avec références éventuelles ; pas de webhook ni confirmation prestataire. |
| Audit | `record` écrit certains succès métier ; pas de couverture exhaustive des lectures super-admin, refus et pannes. |
| Web | Tests HTTP présents, pas de test navigateur responsive. |
| Production/concurrence | Verrous présents dans le code, mais concurrence MySQL réelle non validée. |

## Exigences documentées mais non implémentées

| Exigence | Statut |
| --- | --- |
| Cahier des charges global exhaustif | Absent (`EXT-001`) dans `docs/SPECIFICATIONS.md`; périmètre V1 complet à déterminer. |
| Idempotence HTTP via `IdempotencyRecord` | Table présente et contrat historique, mais pas de workflow API implémenté. |
| Sessions serveur via `UserSession` | Table présente ; web utilise cookie Flask-Login signé. |
| Logout-all et rotation opérationnelle des clés JWT | Documentés comme restants dans `docs/PROGRESS.md`. |
| Dépenses, abonnements, rapports | Modèles présents pour certains domaines ; services/routes/tests métier non livrés. |
| Fiscalité, arrondis métier, crédit client, valorisation stock | À déterminer ; ne pas inventer. |
| Intégrations prestataires paiement | À déterminer ; aucune intégration observée. |
| Validation production MySQL/Redis/PythonAnywhere | Infrastructure préparée, non déploiement effectif. |

## Notes de cohérence documentaire

[`docs/API.md`](API.md) décrit l'API actuellement implémentée
et ses compléments finance. [`docs/BUSINESS_RULES.md`](BUSINESS_RULES.md)
conserve la matrice et des règles normatives, avec certains paragraphes
historiques encodés anciennement. [`docs/PROGRESS.md`](PROGRESS.md) signale les
preuves et limites ; ses listes anciennes de fonctionnalités absentes doivent
être lues avec les compléments récents finance et inventaires.
