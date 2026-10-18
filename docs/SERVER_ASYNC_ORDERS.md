# Commandes serveuse asynchrones — notes d'architecture

## Contexte / point de départ

Branche créée depuis `main` commit `ffe32ee`, puis fondation AJAX de la PR #47
réintégrée sans dépendre d'une fusion de cette PR. Architecture conservée :
Flask/Jinja2 + Bootstrap + JavaScript Vanilla et sessions cookie côté web.
Le JWT `/api/v1` n'est jamais utilisé par le POS de la serveuse.

## Cycle de création

1. Le formulaire web Jinja conserve ses champs et son jeton Flask-WTF CSRF.
   Il expose aussi une clé d'intention aléatoire en champ caché pour la serveuse.
2. `server_async_orders.js` capture la soumission seulement sur le POS serveuse.
   Le panier, la facture et la clé ne sont jamais effacés avant confirmation.
3. Le POST `/bars/<bar_id>/orders/new` appelle le service métier **existant**
   `order_service.create`, avec le même rôle, affectation et contrôle de service.
4. `IdempotencyRecord` (table et unicité déjà présentes) enregistre la clé par
   bar + acteur + opération ; le hash canonique protège contre la réutilisation
   de cette clé avec un panier différent. Insertion du reçu, création de commande
   et enregistrement du résultat sont commités **dans la même transaction**.
5. Un rejeu exact rend l'ID / référence de la première commande sans créer
   une deuxième ligne. Une collision de contenu retourne HTTP 409.
6. Après validation réelle du serveur, le client vide le panier, redonne une
   nouvelle clé pour une **nouvelle** commande, et déclenche le polling existant.

## Timeout, coupure réseau et reconnexion

Une création POST ne fait **jamais l'objet d'un retry automatique**. La clé
incertaine est conservée en `sessionStorage` par bar et ID utilisateur ;
au rafraîchissement elle est restaurée avant toute nouvelle soumission. Le
client interroge ensuite `GET /bars/<bar_id>/orders/async-intents/<clé>` pour
récupérer l'ID et la référence de la transaction si elle a été validée.

Une réponse « absent / en cours » ou un échec réseau ne prouve pas qu'aucune
commande n'a été créée. Le client garde donc la clé existante et permet à
l'utilisateur de renvoyer **la même intention**, jamais une nouvelle création
silencieuse. Aucun secret n'est stocké dans `sessionStorage` ; seulement la
clé d'intention propre à l'utilisateur.

L'idempotence repose sur la contrainte SQL unique. La couverture de concurrence
MySQL multi-connexions doit être effectuée sur une base de test MySQL avant
déploiement : aucune preuve de concurrence production n'est revendiquée.

## Synchronisation

Le flux web déjà existant `/bars/<bar_id>/live/orders` reste la source de
vérité pour la liste. On synchronise par `order.id` : insertion des nouvelles
cartes (y compris quand la liste SSR initiale est vide), mise à jour des
montants, notes et statuts, retrait des cartes sorties des résultats et
actualisation des indicateurs de filtres. Les éléments qui ont le focus ne
sont pas déplacés. Une date de snapshot protège contre les réponses anciennes.
Une seule requête de polling est active à la fois ; les rafraîchissements
demandés pendant son exécution sont sérialisés.

Les modales et formulaires HTML d'origine restent disponibles en mode non-JS.
Les nouveaux éléments sont initialisés via les helpers existants
`setupInlineOrderActions` et `setupServerEditors`, sans dupliquer les
listeners sur une carte déjà traitée.

## Règles inchangées et contrôles

- Aucune modification des services de caisse, paiements, stocks, crédits,
  livraison, ou abonnements.
- Tenant et rôle vérifiés sur le serveur avant toute mutation/relecture.
- Session de service obligatoire pour une serveuse ; un autre acteur ne peut
  consulter ou modifier le reçu idempotent d'un collègue.
- Les modifications de commande utilisent la vérification de révision
  `order_revision` déjà implémentée.
- Erreurs métier explicites JSON pour le chemin asynchrone, HTML/redirect
  maintenus pour les chemins historiques.
- Aucun déploiement PythonAnywhere.

## Validation

`node --test tests/*.test.cjs` et
`pytest -q tests/test_server_async_orders.py tests/test_async_ui_foundation.py tests/test_server_micro_animations.py tests/test_skeleton_loading.py`
sont exécutés par le workflow `server-async-orders.yml`.

La suite Python générale contenait des échecs sur `main` relevés via la PR
de fondation (#47), notamment des fixtures d'anciens tests qui créent un
utilisateur hors service (`OFF_DUTY`). Ce problème n'est **pas masqué** par
les contrôles ciblés. Avant fusion, traiter les échecs des tests existants et
valider concurrence transactionnelle sur une vraie instance MySQL de test.
