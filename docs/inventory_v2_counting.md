# Inventaires V2 — Étape 2 : comptage physique et sauvegarde progressive

Cette branche part de `feature/inventory-v2-foundation`; elle ne doit pas être
fusionnée directement dans `main` ni déployée avant validation.

## Fonctions incluses

- La caissière saisit sur une page dédiée adaptée aux téléphones/tablettes.
- Un champ vide est **non compté** (`NULL`), jamais un zéro implicite.
- Une valeur `0` expressément saisie représente un comptage physique de zéro.
- Le formulaire prend en charge les casiers complets et les unités hors casier,
  convertis en quantité d'unité de base avec précision maximale de 6 décimales.
- Chaque produit enregistré conserve un horodatage, l'identifiant de l'auteur
  et un numéro de révision dans `inventory_line_count_states`.
- JavaScript sauvegarde progressivement les lignes modifiées via un POST web
  protégé par CSRF. Une réponse HTTP 409 bloque la ligne en conflit jusqu'à
  actualisation; les erreurs réseau restent visibles.
- Sans JavaScript, un POST HTML traditionnel sauvegarde les valeurs
  explicites et les champs laissés vides comme `NULL`.
- Un administrateur peut créer un inventaire V2 en choisissant la session
  active d'une caissière; les anciens inventaires V1 restent consultables.
- Les ventes et l'encaissement continuent; **aucun ajustement de stock n'est
  effectué pendant le comptage**.
- La validation finale V2 reste désactivée jusqu'au circuit d'approbation.

## Hors périmètre

Le rapprochement horaire des mouvements pendant le comptage (étape 3), les
calculs d'écarts, les plafonds et les approbations (étapes suivantes) ne sont
pas actifs. Les anciennes pages et services V1 restent accessibles au
propriétaire/administrateur durant la transition, mais ne constituent pas un
remplacement du nouveau flux V2.

Attention : les unités par casier reposent actuellement sur le catalogue
courant. La gestion d'un changement de conditionnement après ouverture
d'inventaire devra être figée avant la validation des inventaires V2.

## Tests

`python -m pytest tests/test_inventory_v2_foundation.py tests/test_inventory_v2_counting.py tests/test_inventories.py tests/test_schema.py -q`

`python -m pytest -q`

Le schéma généré doit être mis à jour par
`python scripts/schema_document.py` puis commité sur la branche de travail.

Aucune garantie de concurrence réelle MySQL n'est revendiquée sans tests
sur un serveur MySQL de test.
