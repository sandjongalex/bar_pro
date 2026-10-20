# BAR_PRO — Inventaires V2 : étape 3, rapprochement des ventes pendant le comptage

## Périmètre implémenté

Pour tout inventaire V2 **créé après cette migration**, BAR_PRO enregistre la
position dans le journal `stock_movements` à l'ouverture. Le stock machine
de référence est alors la quantité **actuelle** dans `stock_balances` au
moment de la création. Les inventaires V1 ne changent pas.

Lorsqu'une ligne reçoit un nouveau comptage physique, BAR_PRO mémorise un
second curseur dans le journal, lié à cette ligne. L'horodatage et le curseur
restent inchangés si la même quantité est sauvegardée ou si une note seule
est modifiée.

La lecture de rapprochement présente :

- Stock machine de référence au début, mouvements enregistrés avant comptage ;
- Stock machine attendu au comptage ;
- Ventes (`SALE`), entrées (`PURCHASE`), retours (`RETURN`), pertes
  (`LOSS`), corrections et autres mouvements après comptage ;
- Projection du stock physique au moment de la consultation ;
- Stock machine actuel et écart à examiner ;
- Signalements `NOT_COUNTED`, `RECONCILED`, `DIFFERENCE`,
  `REFERENCE_MISSING`, `COUNT_CHECKPOINT_MISSING`,
  `LEDGER_MISMATCH` et `NEGATIVE_PROJECTION`.

Formules indicatives :

```
stock_machine_au_comptage = stock_ouverture + somme(mouvements depuis ouverture jusqu'au curseur de comptage)
physique_projete_actuel = comptage_physique + somme(mouvements apres curseur de comptage)
ecart_estime = physique_projete_actuel - stock_machine_actuel
```

Le calcul utilise des quantités décimales et les mouvements de stock réels,
pas une estimation des ventes à partir des bouteilles manquantes.

## Garanties et limites

- Aucun ajustement de stock, vente, dette ou caisse n'est déclenché.
- Chaque endpoint de lecture vérifie le bar et les permissions de l'utilisateur ;
  une caissière ne lit que ses propres inventaires.
- Les mouvements de commande enregistrés lors de la confirmation, les retours
  remis en stock, les achats et les pertes sont lus dans le même journal.
- Le point de journal matérialise une **écriture enregistrée**, pas le moment
  garanti où la bouteille a physiquement quitté le bar. Une sous-commande
  livrée avant son écriture de stock ou une transaction retardée peut créer
  une différence qui nécessite un recomptage manuel.
- Une projection cohérente au journal n'est pas une preuve de comptage exact :
  un superviseur doit vérifier les anomalies et conditions matérielles.
- Les inventaires V2 déjà ouverts avant l'étape 3 conservent volontairement
  un curseur NULL ; **ils doivent être recréés ou recomptés sous une nouvelle
  référence**, ils ne sont pas silencieusement considérés fiables.
- En cas de journal incomplet ou de référence absente, l'écran l'indique
  clairement et ne calcule pas d'ajustement automatique.
- Le workflow de validation définitive et les plafonds d'écart restent
  explicitement bloqués jusqu'à l'étape suivante.
- Tests de concurrence transactionnelle à exécuter sur une vraie base MySQL
  avant tout usage commercial de validation de stock.

## Routes

- `GET /bars/<bar_id>/inventories/<id>/v2-reconciliation` (session web)
- `GET /api/v1/bars/<bar_id>/inventories/<id>/reconciliation` (JWT)
- L'interface de comptage V2 présente et actualise ces informations sans
  recharger toute la page.

## Tests

```bash
python -m pytest tests/test_inventory_v2_reconciliation.py tests/test_inventory_v2_counting.py tests/test_inventory_v2_foundation.py tests/test_inventories.py tests/test_schema.py -q
python -m pytest -q
python -m flask --app wsgi:application db check
```

Régénérer `docs/DATA_MODEL.md` avec
`python scripts/schema_document.py` pour le nouveau schéma Alembic.
