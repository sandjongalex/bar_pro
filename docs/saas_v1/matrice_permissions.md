# BAR_PRO SaaS V1 — matrice de permissions

## Légende
`L` lire, `C` créer, `M` modifier, `V` valider/exécuter, `S` supprimer/annuler ; `—` interdit ; `*` délégation expresse, plafond OWNER **et** plafond sécurité plateforme, audit, contexte. Les annulations financières doivent être **compensées par écritures de contrepartie** plutôt que suppression physique. `SUPER_ADMIN` opérationnel en V1 envisagé en mode assistance contrôlée ; la plateforme ne doit pas pouvoir contourner silencieusement l'audit.

Les cellules sont la **politique CIBLE À VALIDER**, non l'état actuel. Toutes les actions dépendent de l'authentification, du tenant, d'un service actif CASHIER/SERVER, de la période d'accès SaaS et de la vérification utilisateur.

| Domaine/opération | SUPER_ADMIN | OWNER | BAR_ADMIN | CASHIER | SERVER |
| --- | --- | --- | --- | --- | --- |
| Tableau de bord bar | L | L | L | L limitée | L limitée |
| Bars du propriétaire | L | L | L propre bar | — | — |
| Créer établissement | C | C (après vérifications) | — | — | — |
| Paramètres établissement | LCMV | LCMV | L/M non sensibles* | — | — |
| Activer/suspendre un bar | LV | L | — | — | — |
| Gérer personnel/affectations | LCMVS* | LCMVS | L/C/M* (sans élever les droits) | — | — |
| Délégations et plafonds financiers | L | LCMV | L de ses droits | — | — |
| Produits/prix/catalogue | LCMVS* | LCMVS | LCMV | L | L |
| Fournisseurs et achats | LCMVS* | LCMVS | LCMV* | — | — |
| Stock et inventaire | LCMVS* | LCMVS | LCMV* | L propre contexte | L propre contexte |
| Commandes principales | LCMVS* | LCMVS | LCMVS* | LCMV (service) | LCM (propres commandes) |
| Sous-commandes | LCMV* | LCMV | LCMV* | LCMV opérationnel | LCM/V selon parcours |
| Livraison | LV* | LV | LV* | LV | L |
| Factures/reçus | L* | L | L* | L/V impression | L de ses ventes |
| Paiements et caisse | LCVS* | LCVS | LCV* | LCV opérationnel | — |
| Remboursements/annulations financières | LV* | LV | V* | V sous politique caisse* | — |
| Dépenses et remise de caisse | LCV* | LCV | LCV* | C/V dans sa session* | — |
| Clients et crédits liés aux commandes | LCM* | LCM | LCM | LCM opérationnel | — |
| **Dette client hors caisse/stock** | LCV* | LCV | V* | — | — |
| Rapports financiers | L* | L | L* | L limité caisse | — |
| Abonnements/plans | LCMV* | L propre bar / C renouveler | L statut limité | — | — |
| Audit et incidents | L | L bar | L bar* | — | — |
| Récupération de comptes | V assistée* | C de sa demande | C de sa demande | C de sa demande | C de sa demande |

`S` pour commandes et achats = annulation documentée réversible selon état, **jamais DELETE SQL direct des écritures de caisse**.

## Vérification : droits actuellement encodés dans `app/permissions.py`
- `bars.create` : SUPER_ADMIN seul ; `bars.update_settings` : SUPER_ADMIN/OWNER.
- `staff.manage` : SUPER_ADMIN/OWNER ; `staff.read` inclut BAR_ADMIN.
- `customer_debt.manage` : SUPER_ADMIN/OWNER, tandis que `customer_credit.manage` inclut BAR_ADMIN/CASHIER.
- `payments.record`, `refunds.record`, `cash.operate` : SUPER_ADMIN, OWNER, BAR_ADMIN, CASHIER.
- `orders.create` et `orders.edit` : cinq rôles ; `orders.deliver` exclut SERVER.
- `subscriptions.manage` : SUPER_ADMIN seul ; `subscriptions.read` : SUPER_ADMIN/OWNER.
- Une action inconnue est refusée ; le contrôle de service actif est listé dans `SHIFT_GATED_ACTIONS`.

## Cas à formaliser avant implémentation
Séparer chaque droit `create`/`modify`/`approve`/`reverse`; ne pas réutiliser un `*.manage` global pour donner un remboursement illimité. Définir les montants, monnaie, cumul journalier, unité de plafond par admin/bar, seuil d'approbation OWNER, séparation des tâches, délégation expirante, interdiction d'auto-approbation et journal des refus. Toute suppression physique de dette, paiement ou caisse reste interdite. Lors de `READ_ONLY`, retirer **C/M/V/S** à tous les rôles métier, y compris OWNER, à l'exception d'opérations plateforme de régularisation formellement auditées.
