# Bar Manager Pro — Spécifications

> État au 9 septembre 2026 : ce document conserve des exigences et une architecture cible.
> Il ne constitue pas une liste de fonctionnalités disponibles. Les routes réellement
> enregistrées sont dans [ROUTES.md](ROUTES.md), le schéma installé dans
> [DATA_MODEL.md](DATA_MODEL.md), les tests exécutés et les écarts dans [PROGRESS.md](PROGRESS.md).


Version : 0.4 — 2026-09-09. Statut : architecture, rôles, isolation, modèle relationnel et contrat API des exigences connues documentés ; spécifications métier globales incomplètes.

## 1. Sources

SRC-001 : demande initiale de transformation du cahier des charges en six références. À l'inspection initiale, le dossier était vide. Le cahier des charges complet reste absent (EXT-001).

SRC-002 : demande utilisateur « PROMPT 02 — Architecture, rôles, permissions et multi-tenant », reçue après création des six documents. Elle confirme la stack, les cinq rôles, le tenant bar, la suspension et les conventions, sans décrire tous les workflows.

SRC-003 : demande utilisateur « PROMPT 03 — Modèle relationnel et diagramme de données ». Elle impose les entités nommées, le dictionnaire complet MySQL, les snapshots de vente/coût, l'historique stock immuable, paiements/remboursements distincts, absence de suppression financière validée et caisse ouverte unique. DATA_MODEL.md couvre ces exigences ; la source globale EXT-001 reste absente.

SRC-005 : demande utilisateur « PROMPT 05 — Fondations du dépôt ». Elle autorise l'implémentation du socle Flask exécutable, sans module métier prématuré, et exige les configurations, extensions, tests, santé, CLI et vérifications décrites dans ARCHITECTURE.md et PROGRESS.md.

## 2. Terminologie et statuts

| Terme | Définition canonique |
| --- | --- |
| Bar / tenant | Unité unique d'isolation des données métier. |
| Propriétaire | Identité OWNER possédant un ou plusieurs bars ; un propriétaire unique par bar dans la structure retenue. |
| Employé | Identité affiliée à un seul bar actif à la fois, avec rôle BAR_ADMIN, CASHIER ou SERVER. « Affiliation active » ne signifie pas que le bar est ACTIVE. |
| Super-administrateur | Identité SUPER_ADMIN à portée plateforme, intervenant explicitement et avec audit sur un bar. |
| Permission | Action nommée et portée, accordée par la matrice centrale et soumise aux conditions métier. |
| État métier | Situation persistante d'un objet métier ; distincte d'un statut documentaire. |
| Fonctionnalité | Comportement observable relié à une source et à des critères AC. |
| V1 | Première version ; périmètre exhaustif encore à extraire de EXT-001. |
| CONFIRMED | Exigence explicitement issue d'une source disponible. |
| DECIDED_TECH | Choix structurel documenté pris dans le cadre de la demande. |
| POLICY_DECISION | Politique initiale définie pour remplir la matrice demandée, sans prétendre la citer d'une source absente. |
| OPEN_BUSINESS | Règle métier restant à définir avant implémentation dépendante. |
| EXTERNAL_PREREQUISITE | Information ou accès externe manquant ; aucune valeur inventée. |

## 3. Gouvernance documentaire

DEC-001 : six documents Markdown UTF-8 ; les six chemins explicites priment sur la mention initiale de « quatre autres documents ».

DEC-002 : identifiants permanents REQ, FCT, ROLE, PERM, STATE, FIN, STK, TEN, AUTH, AUD, AC, AMB, DEC et EXT. Aucun identifiant supprimé n'est recyclé.

DEC-003 : une règle a un document propriétaire ; les autres renvoient à son identifiant. SPECIFICATIONS possède sources, exigences et inventaire ; BUSINESS_RULES possède matrice, rôles détaillés et invariants ; ARCHITECTURE possède décisions techniques ; DATA_MODEL possède contraintes de représentation ; API_CONTRACT possède conventions d'interface ; PROGRESS possède avancement, ambiguïtés et prérequis.

DEC-004 : distinguer exigences confirmées, décisions et questions ouvertes.

DEC-005 : report initial des choix de stack, désormais remplacé pour Flask/MySQL par DEC-006 à DEC-014 dans [ARCHITECTURE.md](ARCHITECTURE.md). L'hébergement reste indéterminé.

## 4. Inventaire connu et périmètre

| ID | Capacité | Source | Acceptation et limite |
| --- | --- | --- | --- |
| FCT-001 | Authentification et autorisation des cinq rôles | SRC-002 | AUTH-001 à AUTH-005 ; AC-AUTH-006, 007, 012, 014, 015 |
| FCT-002 | Accès aux bars possédés et affiliation employé à un bar | SRC-002 | TEN-001 à TEN-004 ; AC-AUTH-001 à 005, 016 |
| FCT-003 | Suspension : lecture propriétaire, gel des écritures | SRC-002 | TEN-005 ; AC-AUTH-008, 009, 011, 013 |
| FCT-004 | Audit des interventions super-admin | SRC-002 | AUD-001 ; AC-AUTH-010, 011 |
| FCT-005 | Interface Jinja2 et API /api/v1 partageant les services | SRC-002 | Même politique et mêmes effets pour un appel HTML/API ; AC-AUTH-004, 006 |
| FCT-006 | Domaines staff, catalog, inventory, suppliers, purchases, orders, payments, cash, expenses, reports, subscriptions | SRC-002 | Frontières dans ARCHITECTURE ; workflows détaillés en attente de EXT-001 |

Modules complémentaires auth, bars, audit et api complètent la liste de quinze modules. Leur existence architecturale n'atteste aucune implémentation. Chaque fonctionnalité métier future devra préciser entrées, résultat, erreurs, effets persistants et critères observables ; EXT-001 bloque encore l'inventaire exhaustif.

## 5. Rôles et permissions

Les cinq rôles confirmés sont SUPER_ADMIN, OWNER, BAR_ADMIN, CASHIER et SERVER. La matrice normative **permission × rôle** et la revue des cinq rôles se trouvent dans [BUSINESS_RULES.md](BUSINESS_RULES.md), section 7 : ROLE-001 à ROLE-005, PERM-001 à PERM-032. Elle fixe une politique initiale restrictive pour les droits non détaillés dans SRC-002. Aucun contrôleur ni client ne maintient sa propre matrice.

## 6. Hors périmètre et limites

Aucune liste exhaustive d'exclusions V1 n'est fournie. Ne pas assimiler « non documenté » à « exclu de V1 ». Pour cette étape, aucun module métier ni modèle n'est développé. Aucun accès MySQL depuis un client n'est autorisé (SRC-002).

Les suppressions physiques, promotions globales, remboursements et transferts de propriété ne disposent pas d'autorisation applicative implicite : ils sont refusés par la politique actuelle, sans préjuger de leur futur périmètre V1. Un workflow incomplet doit rester indisponible même si sa permission a été définie.

## 7. Traçabilité SRC-001

| ID | Exigence initiale | Destination et état actualisé |
| --- | --- | --- |
| REQ-001 | Inspecter le dépôt | PROGRESS : initialement vide, désormais six documents |
| REQ-002 | Lire la source et docs | Six documents lus ; EXT-001 absent |
| REQ-003 | Recenser les fonctionnalités | §4, inventaire partiel |
| REQ-004 | Recenser les rôles | §5 ; cinq rôles confirmés SRC-002 |
| REQ-005 | Recenser les permissions | BUSINESS_RULES §7 ; politique initiale fixée |
| REQ-006 | Recenser les états | BUSINESS_RULES §2 et §8 ; cycles structurels initiaux fixés, conditions métier encore partielles |
| REQ-007 | Règles financières | FIN-001 ; workflows et arrondis ouverts |
| REQ-008 | Règles de stock | BUSINESS_RULES §4 et STK-001 à STK-004 ; invariants retenus, valorisation/négatifs encore ouverts |
| REQ-009 | Multi-tenant | TEN-001 à TEN-007 fixées |
| REQ-010 | Exclusions V1 | §6 ; exhaustivité bloquée |
| REQ-011 | Acceptation observable | AC-AUTH-001 à 016 ; autres fonctionnalités ouvertes |
| REQ-012 | Ambiguïtés et décisions techniques | ARCHITECTURE et PROGRESS |
| REQ-013 | Aucun module métier | Respecté ; documentation uniquement |
| REQ-014 | Aucun secret, prérequis externes signalés | PROGRESS registre EXT |
| REQ-015 | Terminologie et références autonomes | §2–3 |
| REQ-016 | Six références cohérentes | Six fichiers mis à jour |
| REQ-017 | Couverture exhaustive du cahier des charges | Non vérifiable sans EXT-001 |
| REQ-018 | Absence de contradiction documentaire | Relecture et contrôle structurel ; PROGRESS |
| REQ-019 | Périmètre, règles, rôles et acceptation explicites | Atteint pour autorisation/isolation ; globalement incomplet |
| REQ-020 | Compte rendu exact et progression | PROGRESS |

## 8. Traçabilité SRC-002

| ID | Exigence PROMPT 02 | Référence normative / observation attendue |
| --- | --- | --- |
| REQ-021 | Inspecter puis lire SPECIFICATIONS, BUSINESS_RULES, ARCHITECTURE, PROGRESS | Six références relues ; PROGRESS |
| REQ-022 | Flask create_app, Blueprints, services, modèles hors routes | DEC-006 ; structure et règles de dépendance dans ARCHITECTURE |
| REQ-023 | Jinja2 et API /api/v1 | DEC-006, DEC-013 ; API_CONTRACT |
| REQ-024 | MySQL via SQLAlchemy, aucun accès client MySQL | DEC-006, DEC-007 ; isolation réseau cible ; AC-AUTH-014 |
| REQ-025 | Quinze modules nommés | Table et diagramme logique ARCHITECTURE |
| REQ-026 | Cinq rôles et matrice explicite | ROLE-001 à 005, PERM-001 à 032 |
| REQ-027 | Service central testable, pas de duplication ni sécurité par bouton | DEC-011 ; AUTH-001, AUTH-005 ; AC-AUTH-006 |
| REQ-028 | Toute donnée métier appartient à un bar | TEN-001, DATA_MODEL ; AC-AUTH-005 |
| REQ-029 | Propriétaires limités à leurs bars, employés à leur bar | TEN-002, TEN-003 ; AC-AUTH-001 à 004, 016 |
| REQ-030 | Vérifier le bar de tout objet identifié | TEN-004, TEN-007 ; AC-AUTH-002, 005 |
| REQ-031 | Bar suspendu lisible propriétaire, aucune écriture métier | TEN-005 ; AC-AUTH-008, 009, 013 |
| REQ-032 | Interventions super-admin auditées | TEN-006, AUD-001 ; AC-AUTH-010, 011 |
| REQ-033 | UTC, timezone du bar | DEC-009 ; stockage UTC, conversion au fuseau IANA pour affichage |
| REQ-034 | DECIMAL et XAF par défaut | DEC-010, FIN-001 ; stockage exact sans float |
| REQ-035 | Identifiants numériques ou UUID, choix documenté | DEC-008 ; BIGINT, jamais preuve d'autorisation |
| REQ-036 | Diagramme, revue des cinq rôles, scénarios deux propriétaires/deux bars | ARCHITECTURE ; BUSINESS_RULES §7 |
| REQ-037 | Documentation suffisamment précise pour structure des modèles | DATA_MODEL ; identité, affiliation, portée et contraintes fixées |
| REQ-038 | Décisions, risques, état exact, prérequis et progression | PROGRESS ; aucune exécution logicielle revendiquée |

## 9. Vérifications documentaires

AC-DOC-001 : six fichiers présents et liens locaux résolus. AC-DOC-002 : couverture exhaustive de EXT-001 non vérifiable. AC-DOC-003 : critères fonctionnels détaillés disponibles pour autorisation/isolation seulement. AC-DOC-004 : rôles/permissions/tenants fixés, finances/stock globaux incomplets. AC-DOC-005 : relecture de cohérence entre six fichiers. AC-DOC-006 : aucun secret ni module ajouté.

Les scénarios AC-AUTH sont des résultats attendus revus mentalement, pas des tests exécutés. Pour déclarer la sécurité opérationnelle, il faudra les exécuter contre les services, HTML, API et MySQL réels.

## 10. Traçabilité SRC-003

| ID | Exigence PROMPT 03 | Référence / état |
| --- | --- | --- |
| REQ-039 | Inspecter l'existant et lire les quatre références obligatoires | Six documents relus ; PROGRESS |
| REQ-040 | Concevoir les entités nommées | DATA_MODEL §3 : 34 entités, ApiToken et TokenRevocation distinctes |
| REQ-041 | Ajouter une table seulement si une règle l'exige | UserSession seule addition, justifiée par DEC-013 ; aucune table de projection active supplémentaire |
| REQ-042 | Colonnes, types, nullabilité, FK, UNIQUE, index | Dictionnaire complet T01 à T35 et conventions applicables |
| REQ-043 | Contraintes métier, suppression, bar_id et timestamps | DATA_MODEL §2, §3, §5, §6 ; TEN-008, FIN-002 |
| REQ-044 | Argent DECIMAL, quantités précises | DEC-010, DEC-016 ; DECIMAL(19,4) et DECIMAL(20,6) |
| REQ-045 | Snapshots prix de vente/coût dans OrderLine | FIN-003, T17 ; AC-DATA-006 |
| REQ-046 | Historique stock immuable | STK-001, T07 ; AC-DATA-011/012 |
| REQ-047 | Paiements/remboursements distincts ; pas de suppression financière validée | FIN-002/004, T20/T21 ; AC-DATA-008/012 |
| REQ-048 | Protection fiable contre deux caisses ouvertes dans un bar | FIN-007, T22, DATA_MODEL §4 ; AC-DATA-002 à 004 |
| REQ-049 | ERD Mermaid et revue de toutes les cardinalités | DATA_MODEL §8 : 35 tables, 110 FK de base et registre des cardinalités ; FK renforcées dans les fiches |
| REQ-050 | Compatibilité MySQL, pas de colonnes génériques ambiguës, duplication justifiée | DEC-015 ; snapshots explicités ; clés de cohérence et projection StockBalance distinguées ; sources de mouvement typées |
| REQ-051 | Vérifier index bar/dates/états/références/recherches et opérations sans suppression destructrice | DATA_MODEL §6–7, contrôle documentaire local ; pas de mesure SQL exécutée |
| REQ-052 | Structures pour chaque fonctionnalité du cahier des charges | Liste SRC-003 couverte ; exhaustivité du cahier des charges complet non vérifiable sans EXT-001 |
| REQ-053 | DATA_MODEL, règles révélées, compte rendu et progression honnête | Six références actualisées ; aucun modèle applicatif, DDL ou test MySQL exécuté |

Capacités supplémentaires explicitement connues : approvisionnement et règlements fournisseurs, inventaires, tables/clients, retours/remboursements, garde et remise d'espèces, offres/abonnements, révocation API et idempotence. Leurs structures figurent dans la matrice de couverture de DATA_MODEL §9. Les règles de calcul et permissions manquantes restent des préconditions d'activation, pas des fonctionnalités simulées.

Terminologie canonique ajoutée : StaffAssignment = affiliation employé historique ; CashSession = session du tiroir physique ; StaffCashLedger = journal de garde des espèces ; CashHandover = remise de garde vers le tiroir ; Inventory = document de comptage ; StockMovement = événement de quantité ; OrderReturn = retour commercial ; Refund = restitution d'argent ; AuditLog = observation immuable de portée BAR ou PLATFORM.

## 11. Traçabilité SRC-005

| ID | Exigence PROMPT 05 | Référence / état |
| --- | --- | --- |
| REQ-054 | Inspecter le dépôt et lire tous les documents docs | PROGRESS : six documents lus avant implémentation |
| REQ-055 | Factory, config, extensions, Blueprints, dépendances, environnement, tests, migrations, WSGI et README | Fichiers racine et app/ ; ARCHITECTURE DEC-022 |
| REQ-056 | SQLAlchemy, Migrate, Login, WTF/CSRF, limitation maintenue, journalisation, dev/test/prod | app/extensions.py et app/config.py ; DEC-023/024 |
| REQ-057 | Secrets d'environnement, MySQL production et base de test distincte | .env.example, app/config.py, README ; aucune valeur de production |
| REQ-058 | Healthcheck, erreurs 404/403/500, logging sans secrets et CLI | app/web.py, app/errors.py, app/cli.py |
| REQ-059 | Projet importable et tests factory/healthcheck | tests/test_app.py ; résultats réels dans PROGRESS |
| REQ-060 | Installer, pytest, création test et routes Flask | PROGRESS : commandes exécutées et résultats consignés |
| REQ-061 | Ne pas utiliser le serveur développement en production ni secret réel | README, .env.example et validations de production |
| REQ-062 | Compte rendu et PROGRESS exacts | PROGRESS ; aucun module métier, DDL ou migration métier revendiqué |

## 12. Traçabilité SRC-004

| ID | Exigence PROMPT 04 | Référence / état |
| --- | --- | --- |
| REQ-063 | Inspecter le dépôt et lire les six documents | PROGRESS : inspection PROMPT 04 et six références relues |
| REQ-064 | API JSON versionnée sous /api/v1, succès et erreur normalisés | API_CONTRACT §1–2 ; health API existant normalisé |
| REQ-065 | HTTP, pagination, tri, recherche, filtres, dates et Decimal | API_CONTRACT §5–6 ; conventions de représentation §2 |
| REQ-066 | Bar courant, permissions, suspension et erreurs métier | API_CONTRACT §3–5 ; TEN et PermissionService inchangés |
| REQ-067 | Access/refresh, expiration, rotation, révocation, jti, déconnexion et suspension utilisateur | API_CONTRACT §3 ; AUTH-007 et ApiToken/TokenRevocation actualisés |
| REQ-068 | Idempotency-Key pour commande, paiement, remboursement, remise et réception achat | API_CONTRACT §7 ; IdempotencyRecord ; AC-API-005/006 |
| REQ-069 | Empêcher la fuite tenant par réponse idempotente | API_CONTRACT §7 ; clé portée par bar/acteur/opération et recontrôle avant restitution |
| REQ-070 | Endpoints nécessaires au projet, sans contourner les services ni données bancaires sensibles | API_CONTRACT §9 et §11 ; routes sans permission marquées bloquées |
| REQ-071 | Contrat utilisable séparément mobile/serveur et compte rendu exact | API_CONTRACT §10–11 ; PROGRESS |

SRC-004 suit chronologiquement PROMPT 05 dans le dépôt, mais son contrat complète le socle existant sans modifier la séparation web Flask-Login / API JWT. Les endpoints de domaine restent documentés, non implémentés.
