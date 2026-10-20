# BAR_PRO — Inventaires V2 : tests de concurrence réels sur MySQL

## Risque examiné

Une vente POS et une validation d'inventaire peuvent survenir en même temps.
Une simple exécution successive de tests SQLite ne garantit pas que `SELECT
... FOR UPDATE` protège le journal des mouvements dans InnoDB.

Le code actuel impose un verrou sur la ligne du **bar** via
`permissions.require(...)` pour chaque opération commerciale :
confirmation de commande, soumission/validation et mouvement de stock.
Les quantités sont lues sous verrou transactionnel avant d'être ajustées.

## Tests de non-régression

Le workflow `Inventory V2 real MySQL concurrency` utilise un serveur
MySQL **éphémère** démarré par GitHub Actions, une base réservée exclusivement
aux tests, et des transactions réellement séparées dans deux threads avec
deux connexions SQLAlchemy.

Trois scénarios :
1. La vente verrouille d'abord le bar. La validation attend son commit,
   puis ajuste le stock après la vente.
2. La validation verrouille d'abord le bar. La vente attend son commit,
   puis confirme la commande sur le stock ajusté.
3. Deux validations du même inventaire sont tentées en parallèle. Une seule
   doit être acceptée ; l'autre échoue sur l'état immuable.

Chaque test vérifie le stock final (7 unités pour le scénario de vente),
l'unicité des mouvements `SALE` et `INVENTORY_ADJUSTMENT`, l'absence de
double correction, et la concordance entre le stock machine et la somme des
mouvements enregistrés.

Pour l'exécuter localement : **ne jamais lancer sur une base de production**.
La suite refuse explicitement toute base autre que
`barpro_inventory_concurrency_ci` sur `127.0.0.1/localhost`, et exige
`CI=true` et `BAR_PRO_MYSQL_CONCURRENCY_TEST=1`.

Aucun test de concurrence n'ouvre de connexion sur PythonAnywhere ni sur une
base existante. Aucune migration nouvelle n'est introduite pour cette phase.

## Limites

La preuve concerne les chemins `order_service.confirm` et
`inventory_approval.decide` sous le moteur MySQL de CI. Une fois ces tests
validés, étendre les simulations à des sous-commandes, retours de boissons,
approvisionnements et plusieurs caissières, puis ajouter une surveillance des
exceptions SQL de production sans enregistrer de données privées.
