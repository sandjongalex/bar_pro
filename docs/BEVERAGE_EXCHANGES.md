# Échanges de boissons sans facture

Accès : `/bars/<bar_id>/exchanges`, depuis le poste de caisse, les retours,
la navigation de la serveuse et la barre latérale.

La serveuse demande un échange, ou la caissière le saisit pour une serveuse active.
Aucune facture ni commande n'est créée, sélectionnée ou modifiée. La demande
reste en attente sans mouvement de stock et sans encaissement.

La demande conserve les prix de vente du catalogue à cet instant, les produits,
les quantités entières, la serveuse, le motif et l'auteur. Sans facture, les prix
historiques et les remises de la vente initiale ne peuvent pas être retrouvés.

Supplément = quantité de remplacement × prix de remplacement − quantité
rapportée × prix de la boisson rapportée. Même prix : zéro supplément.
Remplacement moins cher : bloqué tant que la règle de remboursement n'est pas
confirmée. Les bouteilles ouvertes ou non revendables ne sont pas acceptées dans
ce parcours ; utiliser les procédures de perte/retour existantes.

Seule la caisse (ou un responsable autorisé) valide, après contrôle des bouteilles
fermées et revendables. Pour un supplément positif, une caisse ouverte et la
saisie du montant exact reçu en espèces sont obligatoires. Mobile Money et le
paiement différé ne sont pas proposés dans ce parcours. Le montant reçu désigne
le supplément net après éventuelle monnaie rendue.

La validation ajoute les bouteilles rapportées, retire le remplacement et crédite
la session de caisse dans une seule transaction. Une erreur annule tous les effets.
La référence empêche les doubles demandes lors d'une répétition du formulaire ;
une validation répétée ne génère aucun nouveau mouvement. Annulation uniquement
avant validation. Aucune suppression d'historique ni annulation d'échange validé.

Les suppléments sont inclus dans le solde théorique, le récapitulatif du service,
les encaissements et le chiffre d'affaires du rapport financier, ainsi que sa
courbe journalière. La marge estimée tient compte des coûts des mouvements de
remplacement et de retour. Les ventilations par facture/serveuse/produit et les
historiques de paiements sur facture conservent leur périmètre initial ; le détail
des échanges est consultable sur la page dédiée (30 entrées par page).
Le coût de la bouteille retournée est la valorisation courante, faute de facture.
Chaque serveuse ne voit que ses échanges ; les droits et références sont contrôlés
sur le serveur et limités au bar actif.

## Déploiement

Sauvegarder la base, déployer la branche fusionnée, puis exécuter avec
l'environnement Python habituel de l'application :

```sh
python -m flask --app wsgi:application db upgrade
```

Recharger ensuite l'application PythonAnywhere. La migration `c9d0e1f2a3b4`
ajoute uniquement la table des échanges et ses contraintes, sans modifier les
données existantes. Le downgrade refuse la suppression de la piste d'audit.

## Vérification

```sh
python -m pytest tests/test_beverage_exchanges.py -q
```

Les tests couvrent supplément, égalité de prix, caisse fermée, CSRF, permissions,
isolation, annulation, doubles soumissions, quantités, remboursement non défini,
stock insuffisant avec rollback, rapports et migration. La compilation SQL de
la nouvelle migration pour MySQL n'est pas un test sur un serveur MySQL réel.
