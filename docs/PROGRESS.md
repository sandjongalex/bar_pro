# État réel du projet — 9 septembre 2026

Cette liste remplace les anciennes annonces par PROMPT : les 10 tests initiaux ne validaient
pas les modules métier ajoutés ensuite. Une table, une route enregistrée ou un contrat de
conception ne signifie pas qu'un module complet est livré. Aucune validation production
ni concurrence MySQL n'est revendiquée.

## Vérifications exécutées

- Première suite avant corrections : **10 tests réussis**.
- Première suite étendue : **31 tests réussis** (JWT, tenant, services et parcours HTTP).
- Vérifications schéma/configuration : **15 tests réussis**, dont comparaison Alembic/modèles,
  contrôle des CHECK/colonnes/nullabilité, dictionnaire généré et compilation SQL MySQL.
- Suite complète après fournisseurs/achats : **68 tests réussis**.
- Commandes réelles `python -m flask --app wsgi:application db upgrade`, `db check`, `routes`,
  `check-config` : **codes de sortie 0** sur une nouvelle base SQLite vide. Traces locales :
  `verification/audit-final-r09qizva/`. Aucun `create_all` ne remplace le test de migration.
- Révision atteinte : `c3d4e5f6a7b8`, 35 tables applicatives. [ROUTES.md](ROUTES.md) est généré depuis Flask.
- SQL MySQL : compilation hors connexion réussie. **Pas d'upgrade sur un serveur MySQL exécuté** :
  mysql, mysqld et Docker sont absents de l'environnement ; aucune connexion serveur fournie.

## Parcours terminés dans le périmètre testé localement

| Domaine | Parcours livré | Preuve principale |
| --- | --- | --- |
| Socle Flask | Factory, configuration, santé, erreurs API, migrations et enregistrement des routes | test_app, test_schema, test_config |
| Auth JWT | Connexion, signature ES256, claims obligatoires, expiration/audience/type/version, refresh tournant, lien parent, révocation de famille lors d'un rejeu ou logout | test_auth, test_workflows |
| Auth web | Connexion avec CSRF utilisable, cookie séparé de l'API, version des credentials revalidée, redirection correcte | test_auth, test_workflows |
| Tenant | Bar signé/chemin, propriétaire et affiliation active, références secondaires, refus inter-bar HTML/API, suspension des écritures | test_workflows |
| Stock | Entrées/sorties décimales, refus du négatif, version, sens des mouvements, stock retiré une seule fois à confirmation, restauration à annulation | test_workflows |
| Inventaires | Brouillon, snapshot/version, comptage, validation unique, rejet incomplet/obsolète, produit sans solde initial | test_workflows |
| Fournisseurs et achats | CRUD fournisseur API, création/modification d'achat DRAFT avec snapshots, annulation DRAFT, réception complète unique, mouvement lié à la ligne, dette calculée, règlement fournisseur et contre-écriture | test_workflows |
| Commandes | Création, confirmation, service sans second retrait, annulation non encaissée, lignes figées après confirmation ; formulaire web de création/confirmation avec CSRF | test_workflows |
| Retours commerciaux | Documents/lignes persistés, quantité cumulée plafonnée, RESTOCK ou LOSS, prix de vente historique | test_workflows |
| Paiements manuels | Partiel/solde, plafond de vente nette, monnaie exacte, paiement hors espèces sans caisse, refus brouillon/serveur | test_workflows |
| Tiroir de caisse | Ouverture unique, paiement CASH lié à un mouvement, clôture fond + mouvements, écart motivé dans audit, refus fermé | test_schema, test_workflows |

Ces parcours ne constituent pas la livraison de toutes les fonctions des domaines financiers.
Les scénarios de concurrence utilisent des verrous dans le code, mais leur efficacité MySQL
reste à tester avec plusieurs connexions réelles. Les tests SQLite sont séquentiels.

## Corrections de cohérence et de fonctionnement

- `DATA_MODEL.md` décrit désormais le schéma réel et est comparé aux modèles par un test.
  Le design historique est conservé dans `DATA_MODEL_DESIGN.md`, explicitement non livré.
- États de commande DRAFT/CONFIRMED/SERVED/CANCELLED ; conversion des anciens POSTED/CLOSED
  lors de la migration de cycle. Montants Payment ventilés et CHECK de cohérence ajouté.
- FK tenant manquantes sur CashMovement, StaffCashLedger et Expense ; cohérence commande/produit
  renforcée sur OrderReturnLine. Pas de fausse affirmation de triggers ou d'immutabilité SQL.
- BINARY/VARBINARY pour clés binaires indexées MySQL ; units_per_case INTEGER UNSIGNED cohérent.
- Suppression de la modification globale des types de l'extension SQLAlchemy par models.py.
- Permissions absentes ajoutées suivant la matrice ; opérations de vente autorisées au serveur
  sans lui accorder la gestion du stock. Route manuelle limitée à INITIAL/ADJUSTMENT/LOSS.
- Toutes les mutations API métier utilisent Bearer sans CSRF ; les formulaires web gardent CSRF.
- Correction du double comptage des paiements et de la clôture ignorant les mouvements.
- Retours reliés aux documents persistés ; plus de restitution illimitée de la même ligne.
  Les mouvements conservent les coûts et unités historiques des lignes sources.
- Consultation de dette fournisseur soumise à la permission d’achat ; rôle SERVER refusé.
- Fournisseurs et achats complétés dans le périmètre API testé : fournisseurs listés/créés/modifiés/désactivables, achats brouillon modifiables, annulation brouillon, réception unique, règlement SupplierPayment cash ou manuel hors espèces et contre-écriture unique.
- Refus des nombres non finis, précision excessive et uploads non stockés ; coût de valorisation
  retiré de la projection catalogue de vente. Les erreurs SQL ne sont plus renvoyées au client.

## Modules restant partiels ou à réaliser

| Module | Reste à faire |
| --- | --- |
| Auth complète | Logout-all, rotation opérationnelle des clés, rate limiting testé avec Redis réel, sessions serveur UserSession (actuellement cookies Flask signés), tests de concurrence refresh |
| Bars/personnel | Tests complets de création/copie/paramétrage/affiliation, liste plateforme, suspension/réactivation exposées et auditées, fin/changement d'affiliation, stockage des logos |
| Catalogue | CRUD complet catégories/produits, upload réel et limites, pagination uniforme, alertes incluant les produits sans solde et seuil de bar |
| Commandes avancées | Correction différentielle après confirmation, consultations/édition API complètes, tables/clients, règles de crédit et fiscales |
| Retours/remboursements | API de consultation des retours, remboursement Refund traçable et plafonné, annulation d'une commande encaissée, rapprochement de trop-perçu après retour |
| Paiements externes | Références prestataires, confirmation/webhooks, rapprochement ; CARD/MOBILE_MONEY/BANK_TRANSFER sont actuellement des saisies manuelles |
| Caisse complète | Dépôts/retraits, garde employé StaffCashLedger, remise CashHandover, remboursement et corrections additives ; pas de service opérationnel pour ces tables |
| Dépenses | Services/routes/tests Expense et ExpenseCategory |
| Abonnements | Services/routes/tests Plan, Subscription et SubscriptionPayment |
| Rapports | Requêtes métier, tableaux, exports et contrôles de portée dédiés |
| Audit | Couverture de toutes les mutations, lectures super-admin, refus et pannes ; seuls certains événements sont enregistrés actuellement |
| Idempotence HTTP | Traitement IdempotencyRecord, rejeu sûr, conflits de hash et tests ; unicité référence SQL ne remplace pas ce mécanisme |
| Interface web | Interfaces de gestion financière/catalogue/personnel, tests navigateur responsive ; vues de consultation actuellement rendues par tests HTTP |
| Base en production | Triggers/privilèges d'immutabilité, contraintes cible restantes, mesure EXPLAIN et validation MySQL réelle des migrations/concurrences |

## PythonAnywhere

[DEPLOYMENT.md](DEPLOYMENT.md), `deploy/pythonanywhere_wsgi.py` et
`deploy/production.env.example` préparent WSGI, MySQL/PyMySQL utf8mb4, recyclage du pool,
clés ES256 externes, cookies HTTPS et Redis partagé. Les contrôles de configuration sont
exécutés sans connexion ni secret réel. Aucun compte, clé, mot de passe ou déploiement n'est créé.
La version MySQL, le réseau Redis et les capacités du compte PythonAnywhere restent à vérifier.

Le cahier des charges global absent reste un prérequis pour définir fiscalité, règles de crédit,
valorisation et périmètre exhaustif. Les documents de conception ne sont pas une preuve de livraison.

## Compl�ment finance � 9 septembre 2026

Services, API et �cran `/bars/{bar_id}/finance` : paiements manuels en tiroir ou garde
employ�, remboursement li� au paiement et facultativement au retour, soldes nets,
annulation CONFIRMED avec remboursement atomique, mouvements manuels, contrepassation,
remises DRAFT/POSTED/CANCELLED et historique pagin� API. Aucun changement de sch�ma.
Les remboursements r�utilisent le mode et la devise du paiement. Une correction sans
retour r�tablit la dette ; un remboursement li� au retour est �galement plafonn� au
trop-per�u et � la valeur restante du retour. Toute sortie requiert des esp�ces suffisantes.
Une affiliation termin�e peut solder sa garde mais ne peut recevoir de nouveau paiement.
La validation locale reste SQLite et HTTP ; int�gration prestataire et concurrence MySQL
r�elle ne sont pas couvertes. Les paiements externes restent des saisies manuelles.

## Complément inventaires — 15 septembre 2026

- Parcours web avec CSRF : création avec sélection de produits, saisie partielle du comptage,
  enregistrement, validation, annulation du brouillon, consultation paginée et impression.
- API : consultation détaillée, pagination (20 inventaires par page), annulation ; erreurs
  de saisie 422, ressource absente 404, conflit de validation ou référence dupliquée 409.
- Références et motifs contrôlés ; identifiants normalisés, doublons et produits hors bar
  refusés ; quantités finies, non négatives et limitées à six décimales.
- Instantané initial conservé ; vérification de toutes les versions avant ajustement,
  mouvements liés aux lignes, absence de mouvement pour un écart nul, états terminés figés.
- Tests dédiés : `tests/test_inventories.py` (services, API, CSRF, isolation, permissions,
  suspension, annulation, précision, rejouage et rollback des ajustements multiples).
- Limite : vérification SQLite/HTTP ; concurrence sur serveur MySQL et rendu navigateur
  non validés. Les noms et unités affichés proviennent du catalogue courant.
