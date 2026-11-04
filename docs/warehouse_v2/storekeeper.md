# BAR_PRO — PROMPT 2 : Rôle STOREKEEPER et permissions magasin

Branche : `feature/warehouse-v2-storekeeper`. Dépend de la PR « fondations
multi-magasins » (PROMPT 1), encore non fusionnée. Ne jamais déployer les
permissions sans appliquer d'abord la révision `c6f2d4e8a1b0`.

## Règles de sécurité

Le magasinier est un `User(category="EMPLOYEE")` affecté à un seul bar via
`StaffAssignment(role="STOREKEEPER")`. Ni la session navigateur ni les
paramètres transmis ne déterminent son rôle : chaque action est autorisée par
`permissions.require(actor, action, bar_id)`.

- Rôle conservé : `SUPER_ADMIN` (lectures de supervision uniquement sur les
  actions commerciales, aucune écriture sur les magasins).
- Rôle conservé : `OWNER` (accès aux magasins de ses propres bars, décision
  finale sur les litiges et gestion exclusive des délégations).
- Rôle conservé : `BAR_ADMIN` (aucune autorisation de magasin automatique :
  le propriétaire accorde séparément chaque droit dans **Magasin >
  Délégations du gérant**).
- Rôle conservé : `CASHIER` (demande de stock et réception autorisées seulement
  pendant son service; aucune expédition depuis MAIN).
- Rôle conservé : `SERVER` (aucune écriture dans le système magasin).
- **STOREKEEPER / magasinier** : lecture de MAIN, besoins de CASHIER, demandes,
  notifications, transferts et écarts ; préparation/expédition MAIN → TRANSIT,
  comptage physique du principal sans validation de ses propres écarts.

## Droits du magasinier

Les actions sont isolées dans le namespace `warehouse.*`, sans créer
d'alias vers `purchases.manage`, `orders.*`, `cash.*`, `payments.*`
ou `inventory.approve`. Les futures pages métier doivent vérifier exactement
l'action correspondante **côté serveur**.

Le droit spécial `warehouse.purchases.manage` peut être accordé explicitement
au magasinier par son propriétaire. Dans cette phase, il ne débloque pas les
routes d'achats fournisseurs existantes : une future intégration dédiée,
auditée et tenant-scopée sera nécessaire.

Les gérants reçoivent leurs droits via `warehouse_staff_grants`. Une ligne
par `bar_id, staff_assignment_id, action`; désactiver une autorisation conserve
sa trace, et un changement de rôle ou une fin d'affectation révoque les
habilitations pour empêcher leur résurrection ultérieure. Les modifications
sont inscrites dans `audit_logs`.

Il n'existe **aucune** autorisation de retour `CASHIER → MAIN`, ni de droit
de validation des litiges pour le magasinier ou le gérant.

## Écrans et notifications

- `/bars/<bar_id>/warehouse` : consultation cloisonnée par permission ;
  masque les colonnes non autorisées. Les stocks STAGED sont affichés comme
  préparatoires : aucune confusion avec les quantités historiques.
- `/bars/<bar_id>/warehouse/permissions` : liste et formulaire d'autorisation
  **réservés au propriétaire** avec protection CSRF.
- `notify_warehouse_request(...)` : helper serveur pour émettre les futures
  notifications de demandes aux employés habilités dans le même bar et au
  propriétaire. Aucun formulaire de demande ni workflow de préparation n'est
  encore activé dans cette phase.

Le magasinier n'est pas soumis au pointage CASHIER/SERVER actuel ; son
autorisation dépend de son affectation active. Les éventuelles contraintes de
prise de service du magasinier seront décidées avant les opérations physiques.

## Base de données et mise en production MySQL

Révision `d7e3a5b9c2f1` : elle élargit uniquement la contrainte CHECK de
`staff_assignments.role` et crée la table de délégations.
Aucun compte existant n'est converti, supprimé ou changé de mot de passe.

- Sauvegarder MySQL avec vérification de restauration en base séparée.
- Vérifier les migrations sur une copie anonymisée de la base, y compris
  `staff_assignments` avec ses lignes actives et ses colonnes calculées.
- Exécuter `flask --app wsgi:application db current`, puis
  `flask --app wsgi:application db upgrade` pendant une fenêtre maîtrisée.
  Ne pas exécuter les migrations sur production sans validation complète CI.
- Vérifier les anciens rôles, la connexion, l'isolation inter-bars et les
  nouvelles affectations STOREKEEPER.
- Le downgrade refuse de supprimer un magasinier ou des délégations actives
  ou historiques. Sur données en production, utiliser une restauration
  vérifiée et un protocole de retour arrière, pas un downgrade destructif.

## Portée et prochaines phases

Ce prompt pose les **permissions**, l'identité et les espaces de lecture,
pas encore les workflows opérationnels des demandes, préparation, transferts
ou inventaires physiques MAIN. Les anciennes interfaces de caisse, ventes,
achats et inventaires restent protégées par leurs permissions historiques.
La future logique de transfert utilise `warehouse.transfers.dispatch` et
`warehouse.transfers.receive` (et non plus achats ou livraison de commandes).

Tests : `tests/test_warehouse_v2_storekeeper.py` et
`tests/test_warehouse_v2_storekeeper_mysql.py` ; GitHub Actions
`warehouse-v2-storekeeper.yml` valide SQLite, MySQL 8 et les régressions.
