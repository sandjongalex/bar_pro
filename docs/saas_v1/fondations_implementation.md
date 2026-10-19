# BAR_PRO SaaS V1 — PROMPT 2 : fondations de gouvernance

## Base et dépendances
Implémentation sur branche `feature/saas-v1-foundation` depuis `main` `5c3d8be9`. Les documents d'audit SaaS V1 sont inclus dans cette branche sans fusion préalable de la PR d'audit. Les cinq rôles EXISTANTS sont conservés (`User.category` + `StaffAssignment.role`) ; aucun rôle doublé.

## Statut administratif distinct
- Tables ajoutées : `bar_governance` (`bar_id` PK, statut, suspension_level 0/1/2, motif et auteur) et `owner_governance` (`owner_id` PK, statut et motif). États : `DRAFT`, `PENDING`, `INCOMPLETE`, `APPROVED`, `REJECTED`, `SUSPENDED`.
- **Aucune écriture de migration sur les bars ou utilisateurs existants**. L'absence de ligne de gouvernance signifie `APPROVED`, niveau 0. Les autres états préparent l'onboarding ; **seul SUSPENDED bloque** dans cette phase, pour ne pas interrompre des commerces existants.
- Ne pas confondre avec `bars.status` historique ACTIVE/SUSPENDED et `subscriptions.status`. Une suspension de compte OWNER interdit son propre login et révoque ses credentials, mais **ne modifie jamais** les bars possédés ni les employés.
- Niveau 1 : les pages et API GET restent disponibles à l'utilisateur autorisé ; les actions mutantes métier sont refusées au serveur, y compris endpoints AJAX et API.
- Niveau 2 : aucune lecture opérationnelle, même propriétaire ; GET `/bars/<id>/support`, API GET `/api/v1/bars/<id>/governance` limitées à une vue de statut et assistance.

## Autorisation et sécurité
- `permissions.evaluate/require` applique identité OWNER, tenant, rôle, shift, suspension administrative et suspension historique ; `api_required` protège les méthodes mutantes et les vues en niveaux 1 et 2 ; le garde web bloque aussi les POST/PUT/PATCH/DELETE.
- SUPER_ADMIN n'a plus accès aux écritures commerciales : commandes, caisse, remboursements, dettes, stock, achat, catalogue et **reset destructeur**. Les modifications de gouvernance sont auditées et ne touchent jamais aux transactions.
- Le changement d'affectation/role termine l'ancienne autorisation : incrément `credentials_version` pour invalider cookie Flask-Login et JWT émis auparavant. Le retrait/suspension d'un OWNER invalide ses propres sessions, sans cascade vers ses salariés.
- Exceptions historiques : les anciens endpoints qui écrivent sans appel à `permissions.require` doivent continuer d'être examinés ; le garde HTTP stoppe les écritures sans passer par les services, mais un appel Python direct hors services ne peut être sécurisé par le garde web seul. Examiner les appels de background avant toute mise en production.

## Opérations support de l'API
Toutes les API ci-dessous demandent un JWT ES256 et un tenant accessible :
- `GET /api/v1/bars/<bar_id>/governance` : statut minimal, utilisateurs autorisés.
- `PATCH /api/v1/bars/<bar_id>/governance` : SUPER_ADMIN seulement, `{"status":"SUSPENDED","suspension_level":1|2,"reason":"..."}`. Pour reprise `{"status":"APPROVED","suspension_level":0,"reason":"..."}`.
- `PATCH /api/v1/platform/owners/<owner_id>/governance` : SUPER_ADMIN, `{"status":"SUSPENDED","reason":"..."}` puis `APPROVED` pour réactivation.
- Validation stricte du statut et niveau ; aucune opération SaaS envoyée à la caisse/stock/facture.
- Pas encore d'interface SaaS d'inscription, facturation ou vérification email/téléphone ; **hors scope du PROMPT 2**.

## Migration et retour arrière
Révision `ba5a5a1f0001` après `d1e2f3a4b5c6`, deux tables additives et contraintes/index MySQL/SQLite. Établir sauvegarde et version MySQL avant toute exploitation ; exécuter les tests et `db upgrade` sur une **copie hors production**. Le `downgrade` refuse la suppression lorsque des entrées de gouvernance existent. Ne pas exécuter sur PythonAnywhere prod sans validation opérateur.

## Tests et limites
Tests : 5 rôles, propriétaire multi-bars, changement de rôle et jetons révoqués, niveaux 1/2, appels API directs et IDOR, indépendance suspension compte/établissement, interdiction SUPER_ADMIN sur commerce et reset. Suite complète et frontend exécutés par workflow dédié. `docs/DATA_MODEL.md` est régénéré par la CI en conservant son test d'égalité avec le schéma.

**Point de non-régression :** la suite initiale de `main` présentait un échec sur le test de sous-commandes (redirection attendue vers la session de caisse). La **précondition de test** a été corrigée en ouvrant une session valide, en isolant les contextes des deux navigateurs et en comparant numériquement les quantités. Aucun garde de caisse n'a été retiré. La suite Python a ensuite atteint 347 tests réussis sur le commit `c0d61a67`. Chaque nouveau commit de sécurité nécessite de nouveaux résultats CI avant approbation.

## Conservation des traces administratives
Un reset opérationnel autorisé du bar n'efface pas les traces de `bars.governance.change` dans le journal d'audit, et ne supprime pas `bar_governance`. Les autres journaux restent soumis au comportement historique du reset. Une affectation répétée sans changement de rôle ne provoque plus de déconnexion superflue.

## Revue finale de sécurité et préproduction

- La garde HTTP n'autorise au SUPER_ADMIN qu'un endpoint web explicite de gestion administrative du personnel (`staff_web.manage`) pour les mutations dotées d'un `bar_id`. Les mouvements de service (`shifts.manage`) restent **opérationnels** et sont interdits au SUPER_ADMIN, même en accès direct aux services.
- La confirmation de lecture des notifications (`/notifications/<id>/read`) n'a pas de paramètre de bar dans son URL : elle applique donc explicitement la permission tenant `notifications.acknowledge`, incluse dans `WRITES` et les vérifications de suspension. Le tableau de bord filtre les notifications par bars consultables ; aucun contenu d'un bar verrouillé au niveau 2 n'est transmis via une sélection d'un autre bar.
- Régressions ajoutées : création d'un employé autorisée au SUPER_ADMIN, action de service refusée, écriture de notification refusée aux niveaux 1/2, notification verrouillée masquée sur le dashboard multi-bars, reprise de lecture après levée de suspension.
- CI MySQL 8 **jetable**, sans connexion à la production : migration depuis la révision précédente `d1e2f3a4b5c6` avec **propriétaire/bar préexistants**, conservation de ces données après migration additive, puis exercices réels de gouvernance multi-tenant, permissions, niveaux 1/2, audit et suspension du compte propriétaire. Cette simulation automatisée **ne remplace pas** un déploiement de préproduction connecté à une copie sauvegardée et anonymisée des données réelles.
- **Avant déploiement réel :** revue manuelle des autres routes métier, sauvegarde vérifiée, répétition de la migration sur copie préproduction, observation des tâches asynchrones et validation du processus de rollback. La CI ne modifie pas PythonAnywhere.
