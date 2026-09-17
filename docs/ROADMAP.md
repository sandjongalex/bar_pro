# Bar Manager Pro — Roadmap factuelle

Ce document consolide l'état réel du projet et propose des priorités
techniques. Il ne vaut pas engagement produit. Dates, responsable, budget et
statut en cours sont **À déterminer** sauf preuve explicite dans le dépôt.

Sources principales : [PROGRESS.md](PROGRESS.md), [SPECIFICATION.md](SPECIFICATION.md),
[ARCHITECTURE.md](ARCHITECTURE.md), [API.md](API.md),
[BUSINESS_RULES.md](BUSINESS_RULES.md), `README.md`, `app/` et `tests/`.
Les résultats de tests cités depuis [PROGRESS.md](PROGRESS.md) restent datés
du 9 septembre 2026, avec complément inventaires du 15 septembre 2026. Aucun
test nouveau n'a été exécuté pendant la rédaction de cette roadmap.

## Parcours terminés dans le périmètre testé

| Élément | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Socle Flask, config, erreurs, migrations SQLite | Terminé localement | `tests/test_app.py`, `tests/test_config.py`, `tests/test_schema.py`, PROGRESS 2026-09-09 | Base exécutable et migrable localement | Variables env, Alembic | `pytest` ciblé et `flask db upgrade/check-config/routes` verts sur base test | P1 maintien |
| Auth JWT API et auth web | Terminé localement | `app/auth.py`, `tests/test_auth.py`, `tests/test_workflows.py` | Accès sécurisé web/API | Clés ES256 externes | Login, refresh, logout, révocation famille, CSRF web testés | P1 maintien |
| Isolation tenant, rôles, suspension | Terminé localement | `app/permissions.py`, `tests/test_workflows.py`, `tests/test_finance.py`, `tests/test_inventories.py` | Réduction fuite inter-bar | Données bar/staff cohérentes | Accès étranger 404/403, écritures suspendues refusées | P1 maintien |
| Stock centralisé | Terminé localement | `app/stock_service.py`, `tests/test_workflows.py`, `tests/test_inventories.py` | Cohérence quantités et historique | Produits, permissions, transactions | Stock négatif refusé, mouvement unique, rollback multi-ligne | P1 maintien |
| Commandes et retours | Terminé localement | `app/order_services.py`, `tests/test_workflows.py` | Vente opérationnelle | Stock, finance totals | DRAFT→CONFIRMED→SERVED, retrait unique, retours plafonnés RESTOCK/LOSS | P1 maintien |
| Inventaires API/web | Terminé localement | `app/inventory_services.py`, `app/inventories.py`, `tests/test_inventories.py` | Comptage physique et ajustements | StockService, produits | Snapshot/version, validation atomique, impression web | P1 maintien |
| Fournisseurs/achats/règlements | Terminé API/service local | `app/purchase_services.py`, `tests/test_workflows.py` | Approvisionnement et dette fournisseur | Stock, caisse | Achat DRAFT, réception unique, dette, paiement/reversal fournisseur | P2 maintien |
| Paiements/remboursements | Terminé localement | `app/payment_services.py`, `app/finance.py`, `tests/test_finance.py` | Encaissement et restitution tracés | Commandes, retours, caisse | Paiement partiel, remboursement plafonné, annulation encaissée atomique | P1 maintien |
| Caisse, garde, remises | Terminé localement | `app/cash_services.py`, `app/finance.py`, `tests/test_finance.py` | Gestion espèces tiroir/employé | StaffAssignment, CashSession | Ouverture, mouvements, reversal, handover DRAFT/POSTED/CANCELLED | P1 maintien |
| Interfaces web disponibles | Partiellement terminé HTTP | `app/templates/`, `tests/test_workflows.py`, `tests/test_finance.py`, `tests/test_inventories.py` | Exploitation web de base | Bootstrap CDN, CSRF | Login, commande rapide, inventaires, finance testés par client Flask | P2 durcir |

## Fonctionnalités partielles

| Élément | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Bars/personnel | Partiel | `app/bars.py`, `app/bar_services.py`, peu de tests dédiés | Administration incomplète | Permissions, audit, tests tenant | Tests création/copie/paramétrage/affiliation/fin d'affiliation ; exposer ou documenter suspension/réactivation | P1 |
| Catalogue | Partiel | `app/catalog.py`, `app/catalog_services.py`, PROGRESS | Gestion produits incomplète | Catégories, uploads, stock alerts | CRUD catégories/produits testé, upload décidé, alertes produits sans solde | P2 |
| Commandes avancées | Partiel | `app/order_services.py`, SPECIFICATION | Corrections après confirmation limitées | Stock, finance | Correction différentielle confirmée ou règle de refus documentée/testée | P2 |
| Paiements externes | Partiel volontaire | `Payment.method`, `payment_services.py`, tests finance | Risque de confusion opérationnelle | Prestataire à choisir | Décision produit : manuel confirmé ou intégration prestataire spécifiée | P3 |
| Finance web | Partiel UI | `app/finance_web.py`, `tests/test_finance.py` | Écran utile mais non testé navigateur | Templates, CSRF, responsive | Scénarios navigateur Playwright ou équivalent, accessibilité minimale | P2 |
| Audit | Partiel | `app/audit.py`, usages `record`, BUSINESS_RULES AUD-001 | Traçabilité incomplète | Permissions, transactions | Couverture mutations/refus/lectures sensibles, tests panne audit | P1 |
| Documentation | Partiel | README et PROGRESS contiennent anciennes phrases contredites par finance récente | Risque de décisions basées sur infos obsolètes | SPECIFICATION, ROADMAP | README/PROGRESS corrigés ou balisés comme historiques, sans contradiction | P1 |

## Travail en cours

Aucun travail en cours n'est prouvé par une branche, une tâche active, un statut
assigné ou des marqueurs de suivi dans le dépôt. Les compléments finance et
inventaires de [PROGRESS.md](PROGRESS.md) décrivent du travail déjà présent dans
le code et les tests, pas un chantier en cours.

| Élément | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Suivi projet opérationnel | À déterminer | Pas de responsable/date/budget dans le dépôt | Planification floue | Décision équipe | Ajouter un support de suivi ou garder roadmap documentaire | P3 |

## Exigences restantes documentées

| Élément | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Validation MySQL réelle | Restant | PROGRESS, DEPLOYMENT | Risque DDL/verrous/contraintes non validés | Serveur MySQL dédié | `db upgrade`, `db check`, tests intégrité/concurrence sur MySQL vide | P1 |
| Concurrence réelle | Restant | PROGRESS, DEPLOYMENT | Risque double caisse, stock, refresh, suspension | MySQL, connexions multiples | Tests deux connexions : caisse unique, stock concurrent, retours/paiements simultanés | P1 |
| Idempotence HTTP | Restant | `IdempotencyRecord`, API_CONTRACT, SPECIFICATION | Rejeu réseau non sûr | Design hash/réponse, transactions | Idempotency-Key implémenté et testé conflit/rejeu/tenant | P1 |
| Audit exhaustif | Restant | BUSINESS_RULES AUD-001, `app/audit.py` minimal | Traçabilité sécurité incomplète | Schéma audit, routes/services | Mutations, refus et lectures super-admin audités ; panne audit testée | P1 |
| Dépenses | Restant | Modèles `Expense*`, absence routes/services/tests | Domaine financier incomplet | Caisse, catégories, permissions | Services/routes/tests Expense et reversal | P2 |
| Abonnements | Restant | Modèles `Plan`, `Subscription*`, PROGRESS | Gestion offre non livrée | Règles produit à déterminer | Services/routes/tests cycle abonnement, sans effet caché sur bar | P3 |
| Rapports | Restant | PROGRESS, absence services dédiés | Pilotage métier limité | Agrégats fiables, permissions | Requêtes rapport bornées, exports et tests portée | P3 |
| Logout-all / clés JWT | Restant | PROGRESS, API_CONTRACT | Sécurité compte incomplète | Auth tokens | Logout-all, rotation clés et tests refresh concurrence | P2 |
| Sessions serveur `UserSession` | À déterminer | Table présente, web cookie Flask-Login | Ne pas confondre modèle et workflow | Décision architecture | Décider suppression, report ou implémentation testée | P3 |
| Fiscalité, crédit, valorisation | À déterminer | SPECIFICATIONS/BUSINESS_RULES | Règles métier manquantes | Cahier des charges global | Règles écrites avant implémentation | P3 |

## Bugs confirmés

| Élément | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Documentation obsolète sur remboursements/garde/remises | Confirmé documentaire | `README.md` et ancienne table PROGRESS disent encore "à réaliser", tandis que `payment_services.py`, `cash_services.py`, `finance.py`, `tests/test_finance.py` prouvent des parcours livrés | Lecteurs peuvent sous-estimer le périmètre réel | SPECIFICATION/ROADMAP | README et PROGRESS alignés ou annotés avec renvoi clair vers SPECIFICATION/ROADMAP | P1 |
| Encodage dégradé de compléments finance | Confirmé documentaire | `docs/PROGRESS.md` et `docs/BUSINESS_RULES.md` affichent des caractères corrompus dans les compléments finance | Lisibilité et risque d'interprétation | Source texte | Réécrire les paragraphes en UTF-8 propre sans changer les faits | P2 |

Aucun bug applicatif supplémentaire n'est confirmé par cette revue sans exécuter
de tests nouveaux. Les points ci-dessous sont donc des risques à reproduire ou
vérifier, pas des bugs déclarés.

## Risques à reproduire ou vérifier

| Élément | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Verrous `with_for_update` sous SQLite | Risque | Tests locaux SQLite séquentiels ; PROGRESS nie validation concurrence MySQL | Faux sentiment de sécurité concurrence | MySQL réel | Tests simultanés sur moteur cible | P1 |
| `bar_services.py` sans `permissions.require` | Risque technique | ARCHITECTURE, code `require` local basé sur `evaluate` | Verrou bar central non uniforme pour paramètres/staff | Refactor ciblé ou test concurrence | Décider d'utiliser `permissions.require` ou documenter exception testée | P1 |
| Routes avec accès ORM direct | Risque | ARCHITECTURE, `finance.py`, `inventories.py`, `web.py`, `stock.py` | Filtre tenant/perf possiblement divergent | Revue routes | Tests portée/perf par route de liste et payload | P2 |
| Listes et agrégats finance web | Risque perf | `finance_web.py` charge listes bornées et calcule balances par commande | N+1 ou lenteur avec volume | Données volumétriques | Profil/EXPLAIN ou tests performance avec volumes réalistes | P2 |
| Bootstrap CDN | Risque infra | `layout.html` | Dépendance réseau navigateur | Politique déploiement | Décision CDN vs vendoring, CSP documentée | P3 |
| Rate limit Redis | Risque prod | `requirements.txt`, `config.py`, DEPLOYMENT | Production refusée sans stockage durable ; Redis non testé | Service Redis | Test configuration production avec Redis réel | P2 |
| Uploads refusés | Risque produit | `IMAGE_STORAGE_UNAVAILABLE`, `LOGO_STORAGE_UNAVAILABLE` | Fonctions visuelles attendues impossibles | Stockage fichiers | Décision stockage, limites, scan, tests | P3 |

## Dette technique

| Élément | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Documentation fragmentée | Dette | PROGRESS ancien + compléments + SPECIFICATION récente | Contradictions faciles | ROADMAP, SPECIFICATION | Un document "état actuel" canonique, PROGRESS historique daté | P1 |
| Services/routes très compactes | Dette | Modules plats avec lignes longues et logique mixte | Maintenance plus difficile | Tests existants | Refactor uniquement après tests, sans changer comportement | P3 |
| Gestion d'erreurs non uniforme | Dette | API_CONTRACT note harmonisation à faire ; routes retournent 400/409/422 variables | Clients API doivent gérer cas multiples | Contrat API | Table d'erreurs actuelle puis harmonisation testée | P2 |
| Audit minimal | Dette | `record` succès seulement sur certaines opérations | Observabilité sécurité limitée | Audit design | Couverture audit et tests refus/panne | P1 |
| Absence tests navigateur | Dette | Tests client Flask seulement | UI responsive/accessibilité non vérifiée | Navigateur test | Scénarios web critiques automatisés | P2 |
| Pas de workflow idempotent | Dette | `IdempotencyRecord` inutilisé | Risque doublons sur retry | API design | Middleware/service idempotence testé | P1 |

## Priorités techniques proposées

Ces priorités sont proposées pour réduire le risque, pas des engagements.

| Priorité | Élément | Impact | Dépendances | Critère d'acceptation |
| --- | --- | --- | --- | --- |
| P1 | Corriger la cohérence documentaire README/PROGRESS | Évite décisions sur informations fausses | SPECIFICATION/ROADMAP | Plus aucune phrase non datée ne classe remboursements/remises/inventaires web comme absents |
| P1 | Valider MySQL et concurrence | Sécurise la cible production | Instance MySQL, scripts tests | Migrations + scénarios concurrence critiques verts |
| P1 | Implémenter ou cadrer idempotence HTTP | Sécurise retries API | Design hash/réponse | Tests rejeu même hash, conflit hash, tenant, rollback |
| P1 | Étendre audit | Sécurité et traçabilité | Liste actions sensibles | Tests mutations/refus/lectures super-admin |
| P1 | Uniformiser verrouillage permissions sur bars/personnel | Cohérence écriture tenant | Revue `bar_services.py` | Tests démontrant verrou ou exception assumée |
| P2 | Compléter bars/personnel/catalogue | Administration exploitable | Règles produit | CRUD et permissions testés |
| P2 | Tests navigateur web critiques | Qualité interface | Outil navigateur | Login, commande, inventaire, finance testés en rendu réel |
| P2 | Gestion d'erreurs API | Stabilité client | API_CONTRACT | Codes et enveloppes documentés/testés |
| P3 | Dépenses, rapports, abonnements | Extension fonctionnelle | Règles métier | Spécification validée avant code |
| P3 | Uploads et CDN | Produit/infra | Stockage et sécurité | Décision explicite et tests limites |

## Prochaines étapes recommandées

| Étape | Statut | Preuve | Impact | Dépendances | Critère d'acceptation | Priorité proposée |
| --- | --- | --- | --- | --- | --- | --- |
| Nettoyer PROGRESS/README | À faire | Contradictions documentaires confirmées | Clarifie périmètre | ROADMAP, SPECIFICATION | Historique préservé, état actuel non contradictoire | P1 |
| Préparer environnement MySQL de test | À déterminer | DEPLOYMENT décrit cible | Débloque validation production | Accès MySQL | URI dédiée, données non prod, scripts reproductibles | P1 |
| Écrire tests concurrence | À faire | Risques PROGRESS | Vérifie verrous | MySQL, deux connexions | Tests caisse, stock, refresh, suspension/paiement | P1 |
| Concevoir idempotence | À faire | API_CONTRACT, table existante | Fiabilise API | Décision stockage réponse | ADR court + tests avant généralisation | P1 |
| Revue audit | À faire | AUD-001 vs `app/audit.py` minimal | Traçabilité | Liste actions sensibles | Matrice audit actuelle/future validée | P1 |
| Décider priorités produit restantes | À déterminer | Cahier des charges global absent | Évite inventions | Métier | Fiscalité/crédit/dépenses/rapports/abonnements classés explicitement | P3 |
