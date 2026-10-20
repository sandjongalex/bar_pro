# Inventaires V2 — fondations de l'affectation aux caissières

Branche de développement : `feature/inventory-v2-foundation`. **Aucun déploiement automatique.**

## Compatibilité provisoire

Les inventaires historiques (V1) ne portent aucun enregistrement d'affectation.
Les parcours V1 propriétaires/administrateurs restent disponibles pour préserver
les tests et écrans actuels pendant la migration progressive.

Un inventaire V2 est créé avec un enregistrement obligatoire dans
`inventory_cashier_assignments`, relié à une session `EmployeeShift` ouverte,
à un compte caissière et au bar. La saisie de la caissière est limitée à ses
propres inventaires V2, pendant une session active.

Les nouveaux inventaires V2 ne peuvent pas être validés définitivement dans
cette étape : le service refuse la validation avant la mise en place des plafonds
et de l'approbation. Les inventaires V1 conservent leur ancien parcours.

L'interface responsive et le comptage asynchrone de V2, le traitement des ventes
pendant comptage, les plafonds et les rapprochements financiers sont explicitement
reportés aux étapes suivantes. Ne pas considérer la branche comme prête pour la
production avant ces étapes.

## Invariants

- Aucun rapprochement financier ni mouvement de stock supplémentaire ici.
- Aucune affectation automatique des inventaires historiques.
- Aucune caissière ne peut valider définitivement ou ajuster directement un stock.
- Les identifiants du bar, de l'inventaire et de la session doivent être cohérents.
- Le lien à la session et l'identité enregistrés ne sont pas réaffectables.
- L'accès en lecture à une caissière est limité à ses inventaires.
- Aucun changement de `main` ni de déploiement PythonAnywhere sur cette branche.

## Vérifications attendues

`python -m pytest tests/test_inventory_v2_foundation.py tests/test_inventories.py tests/test_schema.py -q`

`python -m pytest -q`

`python -m flask --app wsgi:application db check`

`python scripts/schema_document.py`

Valider également les migrations et la concurrence sur un serveur MySQL de test
avant une mise en production. SQLite ne suffit pas à garantir les verrous MySQL.
