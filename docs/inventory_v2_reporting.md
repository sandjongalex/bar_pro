# BAR_PRO — Inventaires V2, étape 5 : rapports et historique

## Objet

Les Inventaires V2 et V1 disposent désormais de présentations distinctes :

- Pour **V1**, les ventes théoriques et le rapprochement de caisse historique sont
  conservés comme avant.
- Pour **V2**, l'historique présente l'état de la demande de validation (brouillon,
  contrôle, recomptage, approuvé), le nom de la caissière affectée et un accès au
  rapport correspondant. Les colonnes comptables V1 restent vides.
- Les inventaires V2 validés sont consultables sur un rapport de stock en lecture
  seule, et imprimables sur une page adaptée au format A4 portrait.
- Les événements de soumission, recomptage et approbation proviennent du journal
  `audit_logs`, sous l'identité de leur auteur et avec leur motif.

## Calculs historiques fiables

La quantité de correction et son coût proviennent **des véritables mouvements
`INVENTORY_ADJUSTMENT` associés à l'inventaire**, et de
`StockMovement.unit_cost_snapshot` au moment de leur insertion.

Les corrections ne sont pas reconstruites depuis le stock machine actuel, ni
le prix de vente actuel du produit. Ainsi une vente ou un changement du coût
catalogue après approbation ne change pas la correction publiée.

- Les quantités physiques affichées sont celles saisies par la caissière lors du comptage.
- La référence machine à l'ouverture est celle de l'inventaire.
- Un produit sans mouvement correctif après approbation a une différence
  approuvée de zéro.
- Tant que l'inventaire n'est pas approuvé, les différences approuvées et les
  valorisations sont **absentes** (non présentées comme zéro).
- Les noms des produits affichés proviennent du catalogue actuel, car aucun
  nom de produit n'a été figé dans les inventaires V2 des étapes 1 à 4.

**Attention :** ce rapport est un document de suivi des stocks. Sa valeur
absolue d'ajustement n'est **pas** un écart de caisse. Il ne change pas les
commandes, paiements, remboursements, crédits ou dettes. Aucun rapprochement
financier automatique n'est fourni par cette étape.

## Sécurité et URL

Les routes web sont soumises à la connexion et aux contrôles d'accès des
inventaires, y compris la restriction aux inventaires affectés à la caissière :

- `GET /bars/<bar_id>/inventories/<id>/v2-report`
- `GET /bars/<bar_id>/inventories/<id>/v2-report?print=1`
- `GET /api/v1/bars/<bar_id>/inventories/<id>/report`

L'ancienne URL d'impression redirige vers ce rapport pour les inventaires V2.
Les anciennes impressions V1 restent identiques.

## Déploiement

Aucune nouvelle table ou migration de base n'est nécessaire pour l'étape 5.
Sauvegarder la configuration, `git pull --ff-only origin main`, installer les
éventuelles dépendances inchangées et recharger l'application PythonAnywhere
seulement après réussite des tests et fusion.

Vérifications :

```bash
python -m pytest tests/test_inventory_v2_reporting.py tests/test_inventory_v2_approval.py tests/test_inventory_v2_reconciliation.py tests/test_inventory_v2_counting.py tests/test_inventory_v2_foundation.py tests/test_inventories.py -q
python -m pytest -q
```
