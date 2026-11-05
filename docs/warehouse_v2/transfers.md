# BAR_PRO V2 — PROMPT 4 : Bons d'approvisionnement, transit, réception

## Périmètre

Le mouvement autorisé est **MAIN → TRANSIT → CASHIER uniquement**. Il n'existe
aucun endpoint, service ou statut de retour de CASHIER vers MAIN. Les marchandises
sorties du magasin principal ne peuvent pas le réintégrer par le circuit de
transfert. Les achats fournisseurs restent reçus exclusivement dans MAIN.

Les transferts sont **non commerciaux** : aucune commande, vente, remise,
encaissement, paiement, dette ou facture n'est créé. Un mouvement MAIN→TRANSIT
ou TRANSIT→CASHIER conserve la quantité et le coût unitaire historique de la
ligne du bon. Une perte approuvée sort explicitement le volume du transit ;
la valeur correspondante est traçable dans le journal, elle n'est ni imputée
automatiquement à la caissière ni facturée au client.

## Prérequis impératif : répartition initiale LIVE

La création d'un bon (REQUESTED) et sa préparation (PREPARING) sont possibles
en attendant le cutover. **Toute expédition, réception ou résolution physique
est interdite** tant que le statut de `warehouse_allocations` n'est pas
`LIVE`. La fonctionnalité d'activation LIVE n'est *pas* introduite dans ce
PROMPT ; elle exige la réconciliation initiale et une procédure de passation
séparées, sans supposer que les stocks historiques sont disponibles dans MAIN.

Le stock des ventes historiques reste indépendant ; il n'est pas modifié par
les transferts V2. Ne jamais additionner deux fois les soldes hérités et V2.

## États et historique

1. **REQUESTED** : une caissière, un propriétaire, un gérant autorisé ou le
   magasinier de sa propre initiative crée un bon avec référence unique
   **par bar**, destinataire et quantités demandées.
2. **PREPARING** : le magasinier saisit les quantités préparées et peut les
   corriger avant expédition. Le système stocke le journal de chaque
   modification. Préparé ≤ demandé.
3. **SHIPPED** : un verrou transactionnel sur le bar et les lignes vérifie le
   stock de MAIN pour chaque produit. Le système débite MAIN, crédite TRANSIT
   et mémorise le coût unitaire courant, sans changer la valorisation du
   produit. Impossible de réexpédier ou de modifier les lignes après cela.
4. **PARTIALLY_RECEIVED** : seuls les produits réellement acceptés sont
   transférés de TRANSIT à CASHIER, avec une clé d'idempotence.
5. **DISPUTED** : un écart déclaré reste en TRANSIT, ne peut ni être accepté
   comme stock non litigieux ni disparaître sans résolution autorisée.
6. **RECEIVED** : tout le stock expédié est réceptionné et aucun litige
   n'est en cours.
7. **CLOSED** : clôture justifiée par le propriétaire ou gérant explicitement
   autorisé, seulement une fois les quantités expédiées totalement soldées
   (acceptées + pertes reconnues = expédiées).

L'historique `warehouse_transfer_events` est en ajout seul, avec auteur,
ligne, quantité, coût au moment du mouvement, motif et horodatage.
`warehouse_transfer_receipts` garde les clés d'idempotence. Le cumul
dispatched - received - disputed - lost ne peut jamais devenir négatif.

## Conditionnement

Les quantités peuvent être renseignées comme `BOTTLE` (bouteilles) ou
`CASE` (casiers). La conversion est faite immédiatement en unités de base
(quantité × `Product.units_per_case`) et le nombre de bouteilles par casier
est figé sur la ligne du bon. Une modification ultérieure de la fiche produit
ne modifie pas une ancienne demande.

## Permissions

| Action | Autorités |
| --- | --- |
| Demander | OWNER, CASHIER en service, BAR_ADMIN délégué, STOREKEEPER par droit d'expédition propre |
| Préparer/expédier | OWNER, STOREKEEPER, BAR_ADMIN délégué |
| Réceptionner/déclarer un litige | destinataire CASHIER en service, OWNER, BAR_ADMIN délégué destinataire |
| Résoudre une livraison ultérieure ou une perte | OWNER, BAR_ADMIN expressément délégué |
| Fermer un bon soldé | OWNER, BAR_ADMIN expressément délégué |
| Annuler/supprimer un bon expédié | Jamais (pas d'endpoint DELETE) |
| Retour CASHIER→MAIN | Jamais |

Les délégations sont gérées par le propriétaire via la permission
`warehouse.discrepancies.resolve`. Aucun droit financier, de vente, de retour
vers MAIN ou d'approbation de litige n'est accordé au magasinier.

## Interface et API interne

Page de gestion responsive :
`/bars/<bar_id>/warehouse/transfers/manage`, depuis l'Espace magasin.

API JSON interne authentifiée par **session** et protégée par la CSRF Flask
(Web, pas une API publique anonyme) :

| Méthode et route | Usage |
| --- | --- |
| GET `/bars/<bar_id>/warehouse/transfers` | Liste |
| GET `/bars/<bar_id>/warehouse/transfers/<id>` | Détail + historique des écarts |
| POST `.../transfers` | Créer demande |
| POST `.../transfers/<id>/prepare` | Préparer |
| POST `.../transfers/<id>/ship` | Expédier |
| POST `.../transfers/<id>/receive` | Réceptionner |
| POST `.../transfers/<id>/dispute` | Déclarer |
| POST `.../transfers/<id>/resolve` | Livrer plus tard ou perte justifiée |
| POST `.../transfers/<id>/close` | Clore |

Exemple de création, **POST JSON** (requiert cookie de session et token
CSRF valide) :

```json
{
  "reference": "TR-2026-0001",
  "recipient_user_id": 12,
  "quantities": {
    "3": {"unit": "CASE", "quantity": "2"},
    "8": {"unit": "BOTTLE", "quantity": "5"}
  }
}
```

Réception : `{"quantities":{"3":"10"},"idempotency_key":"unique-client-001"}`.
Litige : `{"quantities":{"3":"2"},"note":"Deux bouteilles manquantes"}`.
Résolution : `{"product_id":3,"quantity":"2","outcome":"DELIVERED","note":"Livraison complémentaire vérifiée"}`.
Une perte justifiée utilise `"outcome":"LOSS_APPROVED"`.

Toutes les opérations sont isolées par `bar_id` ; les mutations exigent
le verrou `Bar FOR UPDATE` puis `WarehouseTransfer FOR UPDATE` et les
lignes, avant la modification de balances. Tout appel échoué doit être
rollback ; seule la couche Web/API s'occupe du commit.

## Déploiement

Nouvelle migration additive :
`f2c9a4d1b8e7` révision après `e8f4b6c0d3a2`. Elle étend les bons
et les lignes existants, protège les états et ajoute l'audit des litiges.
Les anciennes lignes IN_TRANSIT/PARTIAL, lorsqu'elles existent, sont
converties en SHIPPED/PARTIALLY_RECEIVED avec leurs acteurs historiques
conservés sans inventer de nouveaux destinataires.

**Ne pas migrer la production sans sauvegarde MySQL restaurable et tests au vert.**
L'activation réelle de la répartition ne fait pas partie de cette livraison.
