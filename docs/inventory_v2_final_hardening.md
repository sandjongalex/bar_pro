# BAR_PRO Inventaires V2 — contrôle final, concurrence et ergonomie

## Vérifications de transactions InnoDB

L'extension de la suite `tests/test_inventory_v2_mysql_concurrency.py` couvre :
- ventes de **deux produits** à la fois, y compris avec lignes dans l'ordre
  inverse (afin de repérer des problèmes de verrouillage) ;
- stock insuffisant pour le second produit : retour complet à l'état initial
  de la transaction, sans mouvement du premier produit ni commande enregistrée ;
- échange indépendant des factures avec stock de remplacement insuffisant :
  ni retour au stock du premier produit, ni supplément encaissé, ni audit
  d'échange enregistré partiellement ;
- interleaving dans les deux sens entre chacune de ces opérations
  et l'approbation de l'inventaire V2.

Les essais utilisent des transactions distinctes sur une base MySQL de CI
éphémère. Les produits, la caisse et les données de production ne sont jamais
utilisés. Le traitement des véritables opérations métier ne change pas.

## Correction de quantité

Les quantités restent enregistrées en décimal exact dans MySQL et les valeurs
des API sont préservées pour ne pas casser les clients existants.

À l'écran, les valeurs sont normalisées **uniquement pour la présentation** :
`22.000000 → 22`, `22.500000 → 22.5`, `0.000000 → 0` et
`22.000001 → 22.000001`. Un produit non compté reste distinct du zéro.

La règle est appliquée au comptage V2, au rapprochement, au rapport de stock,
à l'impression A4 et aux plafonds de validation.

## Responsive

- Les grilles et les cartes ne peuvent plus élargir la largeur de l'écran
  en raison de noms longs ou d'une valeur numérique volumineuse.
- Les nombres et états sont autorisés à passer à la ligne.
- Les grilles des casiers et unités passent en une seule colonne sur téléphone.
- Les boutons de validation et les formulaires restent accessibles sur mobile.
- Le tableau détaillé du rapport défile **à l'intérieur de son conteneur**.
- Le bouton d'actualisation des mouvements reste actif même lorsqu'une
  soumission verrouille le formulaire de comptage.
- L'impression V2 utilise une table à largeur fixe A4.

Les tests automatisés valident le rendu des nombres et les garde-fous
HTML/CSS/JS. Ces tests ne sont **pas** une preuve de capture réelle sur les
appareils physiques : effectuer un contrôle visuel final à 320, 360, 390,
768 et 1366 px et sur la tablette ancienne Samsung SM-T900.

## Statut et limites

La sécurité d'un inventaire V2 exige toujours :
1. une vraie quantité physique, un zéro explicite ou l'état non compté ;
2. la confirmation humaine des mouvements physiques non encore journalisés ;
3. l'approbation par un responsable autorisé, selon le plafond propriétaire ;
4. une base de référence valide (les anciens inventaires V2 sans curseur
   ne peuvent pas être validés automatiquement).

Aucune correction de stock n'est exécutée par l'affichage des rapports.
Les échanges, dettes et encaissements restent indépendants.

Une fois les tests CI réussis et la branche fusionnée, un `git pull` puis
le rechargement Web PythonAnywhere seront requis pour publier les améliorations
visuelles. **Aucune migration Alembic nouvelle**.
