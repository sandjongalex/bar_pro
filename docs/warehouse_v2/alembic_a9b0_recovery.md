# Restauration de la révision de production a9b0c1d2e3f4

## Diagnostic confirmé (4 novembre 2026)

Base PythonAnywhere : `sandjong$bar_pro`. `alembic_version` contient
`a9b0c1d2e3f4`. Les magasins V2 sont absents. La migration originale
`a9b0c1d2e3f4_inventory_manual_cashier.py` a été créée par le commit
`8ca50706c6609304493cc55fee0883f67a212d41` puis a disparu du dépôt
lors du retour à l'état du 22 octobre.

La révision originale dépend de `b5c6d7e8f901` et modifie uniquement la
nullabilité de `inventory_cashier_assignments.cashier_shift_id` et
`cashier_user_id`. La migration V2 `c6f2d4e8a1b0` dépend maintenant de
`a9b0c1d2e3f4`, afin qu'une base ayant déjà cette révision puisse
appliquer directement les migrations suivantes, sans réexécuter son ALTER.

## Avant toute exécution en production

1. Conserver un backup MySQL complet et vérifier la restauration sur une copie.
2. Réaliser un essai de migration sur une copie de `sandjong$bar_pro`.
3. Arrêter temporairement les écritures applicatives pendant la mise à jour.
4. Mettre le code `main` à jour après fusion de la correction et installer
   les dépendances.
5. Vérifier **en lecture seule** :
   `SELECT version_num FROM alembic_version;`
   (attendu : `a9b0c1d2e3f4`) ;
   `information_schema.columns` sur les deux colonnes d'affectation
   (attendu : `IS_NULLABLE = YES`) ;
   `information_schema.tables` pour les quatre tables V2 (absentes
   au début de la procédure). Toute divergence bloque le déploiement.
6. Vérifier `flask db heads` : `e8f4b6c0d3a2`.
7. Seulement après validation, lancer `flask db upgrade`, puis
   `flask db current` et `flask db check`.
8. Redémarrer WSGI sur PythonAnywhere et vérifier les opérations métier.

## Strictement interdit

Ne jamais utiliser `flask db stamp head`, supprimer ou réécrire
`alembic_version`, `db.create_all()`, `db.drop_all()`, ou lancer des
migrations à répétition pour contourner une erreur de version.

La migration reste **additive** pour les magasins V2. La restauration de
`a9b0c1d2e3f4` ne se réexécute pas sur la base déjà marquée comme telle.
Les montants de caisse, dettes clients et fournisseurs et ventes existantes
restent inchangés.

**Limite de périmètre** : la restauration de schéma n'active pas les
magasins physiquement et ne migre pas les ventes/inventaires vers leurs
nouvelles balances.
