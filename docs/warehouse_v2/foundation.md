# BAR_PRO — Fondations des deux magasins (PROMPT 1)

État : **infrastructure additive uniquement**. Aucun transfert, réception ou inventaire
du code métier existant n'est redirigé vers ces tables dans cette phase.
Aucune interface utilisateur n'est ajoutée et aucune activation automatique n'existe.

## Modèle et source de vérité

Chaque bar possède exactement deux emplacements physiques identifiés par leur
**code unique** : `MAIN` (principal) et `CASHIER` (caisse). Le catalogue reste
unique, dans `products`.

- `warehouse_stock_balances` : quantité par `bar_id, location_id, product_id`.
- `warehouse_transit_balances` : quantité expédiée mais non réceptionnée, par produit et bar.
- `warehouse_stock_movements` : journal additif, avec destination physique ou
  `location_id=NULL` pour les deux types de mouvements de transit.
- `warehouse_allocations` et `warehouse_allocation_lines` : photographie propriétaire
  validée des deux quantités initiales par produit, du solde et de sa version
  historiques, ainsi que du coût unitaire et du point de référence des mouvements.
- `warehouse_transfers`, `warehouse_transfer_lines`,
  `warehouse_transfer_receipts` : primitives internes pour des expéditions
  idempotentes et des confirmations, non exposées dans l'interface pour le moment.

**Invariant :** pour chaque produit, stock total localisé = MAIN + CASHIER + TRANSIT.
Un transfert depuis MAIN retire la quantité de MAIN et l'ajoute simultanément en
TRANSIT. À réception, la quantité acceptée sort du TRANSIT et entre dans CASHIER.
Les deux écritures se font dans la même transaction DB ; rien ne touche les
`cash_movements` ou la comptabilité des ventes.

Les prix catalogue, `Product.valuation_unit_cost`, les coûts snapshots historiques
et les mouvements historiques restent intacts. Les transferts V2 utilisent un coût
figé par ligne et ne recalculent pas le coût moyen du bar.

## Préparer la migration en production

1. **Avant toute opération :** sauvegarder MySQL et vérifier un RESTORE sur
   une instance distincte. Exemple avec des variables de connexion fournies
   hors dépôt : `mysqldump --single-transaction --routines --triggers --hex-blob --databases <nom_base> > barpro-pre-warehouse.sql`.
   Protéger le fichier de sauvegarde ; vérifier sa taille et son empreinte.
2. Vérifier l'état Alembic et le schéma actuel : `flask --app wsgi:application db current`,
   puis `flask --app wsgi:application db upgrade` **sur une copie**.
   La révision `c6f2d4e8a1b0` ne supprime ni n'altère les anciennes tables.
3. Contrôler pour chaque bar deux `warehouse_locations` et vérifier que les
   anciennes quantités de `stock_balances`, ventes, achats, sessions et flux
   financiers n'ont pas changé. L'ajout des lieux est idempotent ; aucun solde
   V2 n'est prérempli depuis un solde historique.
4. Programmer un créneau de basculement opérationnel après validation des
   modules suivants. Suspendre les écritures commerciales du bar, traiter
   commandes et sous-commandes en attente, puis **terminer toutes les sessions
   de caisse et prises de service ouvertes** avec les rapprochements requis.
   Le service `stage_allocation` refuse les sessions ouvertes.
5. Faire l'inventaire physique distinct de MAIN et CASHIER, faire valider les
   écarts dans le système historique par les procédures existantes, puis
   soumettre explicitement au propriétaire une répartition par produit.
   **Ne jamais créer d'achat artificiel pour corriger un écart**.
6. Utiliser `warehouse_service.stage_allocation(owner, bar_id, quantities)`
   dans une transaction maîtrisée, puis commettre. Il exige que MAIN+CASHIER
   soit exactement égal au solde historique de chaque produit. Les produits
   omis ne sont acceptés que si leur solde historique est nul. Relancer la
   même opération ne crée aucun doublon ; toute divergence est refusée.
7. `warehouse_service.verify_staged(bar_id)` doit rester vrai avant la bascule.
   Un mouvement historique postérieur rend la photographie obsolète. Dans ce
   cas, NE PAS ACTIVER : une future procédure de restaging audité sera nécessaire.

**Important :** le statut `STAGED` n'est PAS une activation. Les autres services
utilisent encore `StockBalance` et `StockMovement`. Les API internes
`dispatch/receive` exigent `LIVE`, statut que cette phase n'expose pas en
production. Aucune commande de mise en service n'est fournie avant l'intégration
transactionnelle du stock aux achats, ventes, retours, inventaires, sessions et
rapports. Il serait dangereux de modifier ce statut manuellement en production.

## Procédure en cas de sessions de caisse ouvertes

- Refuser le staging avec `OPEN_CASH_SESSION` ou `OPEN_EMPLOYEE_SHIFT`.
- Coordonner une clôture normale de caisse et une passation de stock physique,
  puis reprendre la procédure avec un nouvel instant de référence.
- Ne jamais clôturer automatiquement une session, perdre des commandes en attente
  ni inventer un stock initial. Le mode "sous réserve" doit être ajouté lors de
  l'intégration des passations du prochain lot.
- Si la clôture n'est pas possible, maintenir le bar en mode historique et
  replanifier l'activation. Il n'y a pas de bascule en pleine vente dans PROMPT 1.

## Retour arrière

La révision Alembic conserve toutes les tables historiques. Pour une erreur
détectée **avant tout staging/transfert**, le downgrade de cette révision peut
retirer les seules nouvelles tables. Si une allocation, un mouvement ou un
transfert V2 existe, le downgrade refuse délibérément la suppression des données.
Dans ce cas, figer les écritures, comparer les journaux et restaurer une
sauvegarde vérifiée si nécessaire.

Une fois qu'une version ultérieure aura activé les deux magasins pour les
ventes, un retour au stock historique ne pourra pas se faire par un simple
downgrade : il faudra un plan dédié de rapprochement des ventes et transferts.

## Tests

- `python -m pytest -q tests/test_warehouse_v2_foundation.py -k 'not mysql'`
- `python -m pytest -q tests/test_schema.py`
- Workflow GitHub Actions `warehouse-v2-foundation.yml` : tests de
  non-régression SQLite et migration réelle sur une base MySQL 8 éphémère.
- Rappeler le générateur avant les tests de schéma : `python scripts/schema_document.py`.

## Hors périmètre du PROMPT 1

Aucun choix de magasin dans les écrans métiers, aucune activation LIVE, aucune
écriture des achats/ventes existants dans les nouveaux soldes, aucune passation
obligatoire à la clôture, aucune gestion opérationnelle des litiges, et aucun
nouveau rôle de magasinier. Ces opérations seront traitées en PR distinctes.
