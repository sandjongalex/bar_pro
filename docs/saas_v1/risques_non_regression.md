# BAR_PRO SaaS V1 — registre de risques de non-régression

| ID / Niveau | Risque | Mesures de maîtrise / tests attendus |
| --- | --- | --- |
| R01 / **Critique** | Lecture/écriture inter-bar via URL, query string, API ou ID secondaire | Tester en matrice OWNER multi-bars, employés distincts, API JWT et endpoints AJAX ; scoper toutes les tables avec `bar_id`; 404 sans énumération |
| R02 / **Critique** | Fin des 14 jours ne bloque pas une écriture API ou AJAX | Couvrir toutes méthodes mutantes, redirections web et jobs ; tester exactement l'instant de fin, plus aucune grâce ; autoriser lecture |
| R03 / **Critique** | Double vente, double débit stock, double remboursement après retries ou sous-commandes | Préserver idempotence, verrous, versions/revisions, transaction unique et tests avec rejeu |
| R04 / **Critique** | Rupture des sous-commandes, ordre de livraison et fusion | Tester édition avant/après livraison, validation serveur, calcul indépendant et cumul uniquement au moment prévu |
| R05 / Élevé | Administrateur dépasse son plafond par fractionnement, concurrence ou auto-validation | Limites par opération + cumul + verrou atomique + audit de refus, pas de privilèges par interface |
| R06 / Élevé | Séparation dette manuelle/stock/caisse violée | Assertions sur `CustomerLedgerEntry.ADJUSTMENT` et absence de `Order`/`CashMovement`/`StockMovement` ; remboursements hors caisse indépendants |
| R07 / Élevé | Paiement d'abonnement dupliqué ou reprise d'accès sans confirmation | Références uniques, gestion de concurrence, idempotence, historisation, tests non-paiement et reconfirmation |
| R08 / Élevé | Récupération de compte facilite prise de contrôle | Double canal, preuves, délai, notifications, neutralisation des anciens tokens, audit, anti-replay |
| R09 / Élevé | Employé sans service / bar suspendu consulte des données | Gardes web, API, pages dashboard, endpoints JSON, tests OFF_DUTY |
| R10 / Moyen | PWA remet en cache historique ou données sensibles | Conserver SW réseau seulement pour pages métier ; vérifier cache après logout et changement de compte |
| R11 / Moyen | Changements de template/Javascript cassent CSRF, 58mm RawBT ou filtres async | Couvrir DOM, headers, scroll/filtres et impression, tests navigateurs réels 360/390/768 + ancienne tablette |
| R12 / Moyen | MySQL diffère des tests SQLite ; migrations/locks échouent | Répéter suite sur MySQL de validation, backup + rollback, CI tests migrations ; ne pas prétendre équivalence |
| R13 / Moyen | Limiteur en mémoire multi-workers | Redis/stockage partagé dès disponibilité ; limiter réellement les OTP et connexions |

## Défaut existant à ne pas masquer
Sur `main` commit `5c3d8be` : GitHub Actions « Cashier secondary pages async safety », run `39318537195`, job `118286241322` : `335 passed, 1 failed`, échec `tests/test_cashier_suborder_edit.py::test_cashier_can_edit_round_before_and_after_delivery_and_server_sees_changes`. La page `/bars/<id>/cashier/workspace?order_id=<id>` renvoie vers `/bars/<id>/cashier-session`, le test attend un CSRF de formulaire. Il faut préciser si le garde de session est correct et que le test doit ouvrir sa session ou si le parcours caisse est cassé. Pas de contournement de sécurité.

## Critères bloquants avant un merge fonctionnel
Tests existants corrigés sans assouplissement de sécurité ; nouveau test IDOR par rôle/objet ; lecture seule après essai confirmée ; tests comptables/stock et sous-commandes inchangés ; scénario de récupération complet ; MySQL validation et revue humaine des migrations ; comparaison d'URLs/routes et captures d'interface.
