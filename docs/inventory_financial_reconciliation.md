# BAR_PRO — Rapprochement financier pendant l'inventaire

## Périmètre

L'écran physique V1 et V2 contient un panneau financier en dessous du
comptage des bouteilles. Les deux univers restent **indépendants** :

- Le comptage des produits modifie le stock seulement au moment de l'approbation.
- La recette physique est un relevé des billets et pièces du **tiroir de caisse
  actif**, fonds de caisse compris. Aucun encaissement fictif n'est créé.
- La **caisse attendue** vient des mouvements réels de la session (fonds initial
  + encaissements en espèces + dépôts − dépenses en espèces − retraits −
  remboursements). Le solde des mouvements Mobile Money ne doit jamais être
  ajouté au tiroir d'espèces.
- L'écart réel est `recette physique − tiroir attendu`. Une valeur négative
  indique un manquant; positive, un surplus.
- La recette physique exige un commentaire si l'écart est non nul. Chaque
  sauvegarde et montant attendu est consigné au journal d'audit.
- Aucun dépôt ou retrait de caisse n'est implicitement créé lors du comptage.

## Régulariser une dépense oubliée

Le bouton dans l'inventaire appelle le **service existant** d'enregistrement
des dépenses. Une dépense payée en espèces génère une unique sortie du
tiroir; une dépense Mobile Money/carte/virement ne génère pas de mouvement
d'espèces. La référence d'origine par formulaire est unique pour empêcher
les doubles clics d'enregistrer deux dépenses identiques. La date reste
l'heure de **saisie réelle** (aucune antidatation arbitraire de caissière).

Une session ouverte est obligatoire pour une dépense en espèces. Le montant
doit être positif, la catégorie active et le motif documenté.

## Demandes de dette

Une caissière peut signaler la dette d'un client présent dans le registre.
Le propriétaire doit approuver ou rejeter en justifiant la décision.
Deux parcours distincts :

1. **Facture reliée** : `customer_service.credit_order` convertit le
   montant **total encore impayé** en vente à crédit, et n'altère ni le stock
   ni le tiroir. Le montant déclaré doit correspondre exactement au solde
   réellement dû au moment de l'approbation. L'option doit être activée dans
   les paramètres du bar.
2. **Dette manuelle hors facture** : `record_manual_debt` constitue une
   créance **hors caisse et hors stock**; elle ne fait jamais disparaître un
   manquant de caisse. Elle ne modifie pas non plus le chiffre d'affaires
   théorique de l'inventaire.

Les demandes en attente empêchent l'approbation définitive de l'inventaire;
une décision est obligatoire. L'approbation stock V1/V2 n'est pas autorisée
à réécrire ou effacer les dettes.

## Préserver la chronologie

Le relevé d'espèces contient un **instantané** du tiroir au moment du
comptage. Si de nouveaux encaissements ou dépenses surviennent avant la
fin de service, un avertissement invite à recomptabiliser les espèces.
L'inventaire n'entraîne **pas** la clôture automatique de la session : elle
reste soumise au processus de caisse habituel. Après approbation du stock,
le relevé financier est en lecture seule. La correction nécessite les
écritures classiques (contrepassation et audit), sans altérer le rapport
historique. Un inventaire sans relevé affiche « Recette non saisie » et
n'affiche **jamais** une différence de zéro inventée.

## Technique / déploiement

- Schéma MySQL : révision Alembic `b5c6d7e8f901`.
- Tables : `inventory_cash_reconciliations` et
  `inventory_debt_requests`, clés composites par bar.
- Vues : comptes V1, V2 et rapport V2. Routes POST avec authentification,
  CSRF, rôle et attribution au service.
- Tests : comptage, caisse, dépenses, dettes soumises, approbation
  propriétaire, tenant isolation, refus des opérations sur inventaire validé,
  non-régression des ventes.
- **Migration obligatoire** : sauvegarder la base de production MySQL avant
  `flask db upgrade`. Ne pas activer les écrans financiers tant que la
  migration ne s'est pas terminée. Installer la branche sur PythonAnywhere
  uniquement après CI et fusion sur `main`.
