# PROMPT 3 — Achats fournisseurs, réception exclusive dans MAIN

Branche : `feature/warehouse-v2-purchases`, fondée sur le PROMPT 2
(`feature/warehouse-v2-storekeeper`), lui-même fondé sur le PROMPT 1.
Ne pas fusionner directement dans `main` tant que les dépendances ne sont
pas validées.

## Séparation des opérations

**Achat fournisseur ≠ approvisionnement de caisse**. La réception d'un achat
fournisseur est toujours attribuée au lieu physique `MAIN`; elle n'écrit
jamais dans `CASHIER` ni dans `TRANSIT`. La sortie de `MAIN` vers la caisse
reste une opération indépendante de transfert avec dispatch et réception.

Les tables existantes `suppliers`, `purchases`, `purchase_lines`,
`supplier_payments`, `stock_movements` et `stock_balances` sont conservées.
Le suivi des casiers, bouteilles, conversions, prix unitaires, coût moyen
pondéré, dettes et paiements partiels reste celui des services existants.
Aucun changement au circuit `purchase_service.pay` ni
`purchase_service.reverse_payment`.

## Transition avant activation réelle des deux magasins

En l'absence d'une allocation `LIVE` (cas normal tant que les ventes et
inventaires n'ont pas été migrés), chaque achat réceptionné :
- modifie **une seule fois** le solde partagé `StockBalance` et son
  coût moyen, pour ne pas casser la vente existante ;
- crée une ligne `warehouse_purchase_receipt_lines` pour chaque
  `PurchaseLine`, attribuée explicitement à `MAIN`, en état
  `PRE_CUTOVER` ;
- **ne modifie pas** les soldes provisoires `WarehouseStockBalance`, qui ne
  sont pas encore fiables en exploitation quotidienne ;
- sera pris en compte dans la répartition physique du stock lorsque le
  propriétaire réalisera la réconciliation initiale du PROMPT 1.

Ce mode est volontairement transitoire. L'interface distingue `MAIN déclaré`
du `stock MAIN opérationnel` pour éviter une fausse précision. Après une
réception ou une vente historique, une allocation déjà `STAGED` peut devenir
périmée : ne jamais la promouvoir automatiquement en `LIVE`.

En `LIVE` **dans les tests isolés seulement** : le reçu crée en plus
`PURCHASE_IN` dans le journal V2 et incrémente seulement le solde physique
`MAIN`. Le stock historique est conservé temporairement pour les ventes,
sans être additionné aux totaux V2. Aucune route d'activation en production
n'est ajoutée dans ce prompt.

## Droits et contrôles

- `OWNER` : gestion des achats et confirmation des réceptions de ses propres bars.
- `BAR_ADMIN` : création, édition, annulation et correction uniquement avec
  délégation `warehouse.purchases.manage` du propriétaire ; confirmation
  physique distincte avec `warehouse.purchases.receive`.
- `STOREKEEPER` : confirmation physique uniquement avec délégation
  `warehouse.purchases.receive`, sans `purchases.read`, modification des
  factures ou accès aux paiements. Le droit
  `warehouse.purchases.manage` créé au PROMPT 2 ne lui donne **pas** accès
  aux opérations commerciales de ce prompt.
- `CASHIER` et `SERVER` : aucun accès aux réceptions fournisseurs.
- `SUPER_ADMIN` : lecture/support selon SaaS, aucun achat ou mouvement.

La confirmation est vérifiée côté serveur par `PurchaseService.receive`
sur les routes API et web ; les affectations et délégations sont liées au
`bar_id`. La page `/bars/<bar_id>/warehouse` montre les acquisitions
attendues au magasinier autorisé **sans** coûts fournisseurs ni paiements et
propose une confirmation avec CSRF.

## Historique et correction

La contrainte unique `(bar_id, purchase_line_id)` empêche les doubles
réceptions dans le nouveau journal. `Purchase.status` et le verrou
transactionnel préviennent une seconde inscription dans le stock partagé.
Toutes les écritures financières, mouvements hérités et journaux MAIN sont
réalisés dans **la transaction de l'appelant**.

La correction/annulation d'une réception non payée renverse le mouvement
hérité et, en `LIVE`, ajoute `PURCHASE_REVERSAL` dans MAIN. Aucune ligne
historique n'est effacée. Une sortie réelle postérieure bloque la correction
qui exigerait une réévaluation rétroactive. Un achat partiellement payé
requiert d'abord la procédure existante d'annulation de paiement.

Les achats `POSTED` créés avant la migration n'ont aucune ligne
`warehouse_purchase_receipt_lines`; leurs anciennes corrections continuent
d'utiliser le mécanisme de renversement hérité, sans inventer une réception
V2 rétroactive.

## MySQL PythonAnywhere

Migration additive `e8f4b6c0d3a2`, après `d7e3a5b9c2f1`.
Une table tenant-scopée, deux types supplémentaires dans le CHECK de journal
V2 ; aucun réécriture des achats/soldes/paiements historiques.
Le downgrade refuse de détruire des données réelles du nouveau journal.

Avant déploiement :
1. Exécuter un backup MySQL et vérifier sa restauration sur copie.
2. Valider `flask db upgrade` sur une copie anonymisée du vrai schéma.
3. Vérifier les contraintes InnoDB, la migration CHECK, les affectations
   existantes, et les historiques des achats/soldes fournisseurs.
4. Laisser les deux magasins **non activés** tant que la passation
   des caissières et la migration des ventes ne sont pas couvertes.
5. Ne pas activer ce module en production si PROMPT 1 et PROMPT 2 ne sont
   pas déjà migrés avec succès.

Tests : `tests/test_warehouse_v2_purchases.py`,
`tests/test_warehouse_v2_purchases_mysql.py`, régressions complètes
et `.github/workflows/warehouse-v2-purchases.yml`.
