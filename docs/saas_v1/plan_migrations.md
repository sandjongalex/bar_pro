# BAR_PRO SaaS V1 — plan de migrations (AUCUNE migration appliquée)

## État initial
- `app/models.py` comporte `users`, `bars`, `staff_assignments`, `plans`, `subscriptions`, paiements d'abonnements, audit et journaux.
- Migrations Alembic nombreuses, incluant `d4e5f6a7b8c9` (comptes clients), `d1e2f3a4b5c6` (échéance dette manuelle), `b8c9d0e1f2a3` (sous-commandes), `c9d0e1f2a3b4` (services employés). Réutiliser sans réécrire les versions existantes.
- Production MySQL ; tests SQLite : les contraintes, verrous et colonnes calculées diffèrent. Une compilation SQL MySQL hors ligne n'est pas une migration MySQL réussie.

## Séquence proposée pour les prochaines phases
1. **Cartographie non destructive** : établir branche/commit de référence, heads Alembic et table `alembic_version`, version serveur MySQL, colonnes et index réels ; sauvegarde chiffrée vérifiée + stratégie de restauration **avant** action prod. Pas de secrets ou dumps dans Git.
2. **Vérification propriétaire** : table d'états/preuves email/téléphone et challenge expirant ; ajout progressif nullable, historique d'horodatages ; comptes existants à état `LEGACY_PENDING` contrôlé, sans déconnexion brutale.
3. **Droits SaaS** : modèle d'essai/bar et d'accès effectif, échéances UTC, preuve d'origine et historique ; backfill déterministe préservant l'accès existant jusqu'à bascule explicite. Ne pas mettre `READ_ONLY` dans `bars.status` (`ACTIVE|SUSPENDED` actuellement).
4. **Plafonds** : permissions administrateur par bar et action, montants décimaux et contraintes, compteurs/cumul atomiques et approbations ; index `(bar_id, actor_id, action, period)`.
5. **Abonnements** : réutiliser `Plan/Subscription/SubscriptionPayment` et régler l'unicité des abonnements actifs ; prolongation seulement après confirmation de paiement fiable et journal d'événement idempotent.
6. **Récupération** : demandes horodatées, accusés de preuve et notification, délai de sécurité, révocation des familles de tokens, journal d'accès aux pièces ; politique de rétention minimale.
7. **Feature flags + déploiement** : code compatible ancien/nouveau schéma, activation progressive par cohorte et bar, contrôle de lecture seule centralisé, observation et rollback du code sans perte d'historique.

## Ordre opérationnel par migration
Pour chaque révision : (a) `upgrade` sur copie de test MySQL anonymisée ; (b) vérifier index/contrainte/fk/volumétrie ; (c) backfill transactionnel réexécutable ; (d) tests avec anciens comptes et nouvelles inscriptions ; (e) déploiement code derrière feature flag ; (f) bascule après approbation et monitoring. Réserver les contraintes `NOT NULL` à une deuxième migration après nettoyage. Aucune migration destructive ou `db.create_all` en production.

## Rollback
Privilégier désactivation feature flag + déploiement code précédent compatible schéma étendu. Les écritures financières et preuves de vérification ne doivent jamais être effacées au rollback ; procédure de restauration vérifiée hors production. Les révisions futures doivent indiquer explicitement leur réversibilité, leur coût, les éventuels blocages MySQL et la compatibilité de lecture.

## Validation technique
`python -m flask --app wsgi:application db heads` ; `python -m flask --app wsgi:application db history` ; `python -m pytest -q tests/test_schema.py tests/test_config.py` en test. Ne pas exécuter `db upgrade` sur PythonAnywhere production depuis cette branche.
