# BAR_PRO — Inventaires V2 : extension des tests InnoDB opérationnels

Les tests ajoutés au workflow `Inventory V2 real MySQL concurrency`
exécutent les vrais services de BAR_PRO sur un **MySQL 8 éphémère**.
Ils préservent volontairement toutes les règles de fonctionnement métier
existantes et ne modifient pas les fonctionnalités du bar.

## Scénarios couverts

1. **Sous-commande de serveuse** : création/validation chez la serveuse,
   puis livraison par la caissière. Le stock sort uniquement à la livraison.
   Le test intercale la livraison avec l'approbation d'un inventaire dans les
   deux ordres possibles. Une relivraison doit être refusée.
2. **Sous-commande servie par la caisse** : le stock sort dès la création
   de cette sous-commande. L'acquittement ultérieur par la serveuse ne
   décompte pas une seconde fois. Deux ordres possibles des commits.
3. **Retour de bière** : retour réel enregistré avec la disposition
   `RESTOCK`, qui rajoute une seule bouteille ; puis variante
   `LOSS`, qui n'en réinjecte aucune. Test avec approbation concurrente.
4. **Réception fournisseur** : enregistrement et réception réelle d'un achat
   de trois unités, dans les deux ordres possibles avec l'approbation.

Chaque scénario utilise des transactions SQLAlchemy indépendantes sur InnoDB,
synchronisées par des événements. Il vérifie un unique mouvement
`INVENTORY_ADJUSTMENT`, les types et nombres de mouvements opérationnels,
le stock final calculé et la concordance exacte
`stock_balances.quantity = SUM(stock_movements.quantity_delta)`.
Le compteur `stock_balances.version` doit aussi correspondre au nombre de
mouvements journalisés.

Ces contrôles ne sont **pas** exécutés contre PythonAnywhere. La base
`barpro_inventory_concurrency_ci` doit être locale et initialement vide.
L'étape est une extension de tests **sans migration ni modification de code
métier**.

La suite ne couvre pas encore tous les cas de pénurie sous concurrence,
les échanges indépendants des factures s'ils reposent sur un service distinct,
ni les opérations sur deux produits dans des ordres de verrouillage inversés.
