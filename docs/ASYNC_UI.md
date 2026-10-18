# Fondations AJAX progressives — BAR_PRO

Ce socle est **opt-in** et ne modifie aucun workflow métier existant.
Les pages restent rendues avec Flask/Jinja2, Bootstrap et Vanilla JS.
Le script global `barpro_async.js` ne transforme aucun formulaire par défaut.

## Audit de la branche main (18 octobre 2026)

- `app/templates/layout.html` charge PWA, thème, skeletons et scripts web à chargement différé.
- `server_pos_ui.js`, `cashier_live_ui.js` et `unpaid_orders_ui.js` disposent déjà de `fetch` et de polling. Ne pas créer un second poller sur les mêmes composants.
- `order_edit_ui.js` gère son propre formulaire AJAX, son CSRF et les erreurs ; le migrer séparément, avec régressions.
- `app/orders.py` expose toujours le formulaire web de création avec redirection HTML, et une API `/api/v1` JWT ; ne jamais appeler cette API avec le cookie de session.
- Des formulaires Jinja existants déclenchent des POST puis redirections ; l'infrastructure doit rester sans effet sur eux.
- `skeleton_loading.js` est réutilisé sans modification et `theme.js` / `dark_theme.css` sont conservés.

## API côté navigateur

- `BarProAsync.request(url, {method, form, body, key, timeout, format})` : mêmes origine et session, CSRF pour toute mutation, délai, erreur réseau et rejet JSON.
- `BarProAsync.submit(form, submitter)` : **uniquement** pour une route web Flask explicitement adaptée et renvoyant JSON, sans reset/reload. Évite les doubles clics tant que la requête est en cours. L'entrée et le panier sont conservés en cas d'échec.
- `BarProAsync.refresh(element, url, {selector, key})` : GET HTML same-origin et comparaison des enfants `data-bp-key` (identifiants métiers stables).
- `BarProAsync.watch(element, url, {interval})` : rafraîchissement périodique avec visibilité, connexion et nettoyage.
- `BarProAsync.notify(message, kind)` : notification accessible par lecteur d'écran.
- événements `barpro:network`, `barpro:async-success`, `barpro:async-error`.

## Contrat côté Flask

`from app.async_ui import wants_async_web_response, web_json_response`.

Conserver d'abord les validations de rôle, bar, session et état du métier.
Dans la branche dédiée à un écran, après succès d'un service transactionnel,
retourner `web_json_response(data={...}, message=...)` si `wants_async_web_response()`.
Sinon exécuter la redirection HTML **inchangée**.
Renvoyer des erreurs métier JSON avec statuts 4xx pour les formulaires adaptés.
**Ne pas** ajouter `csrf.exempt` aux routes web; ne pas exposer `/api/v1` aux cookies.

## Idempotence et actions financières

Le double-clic client est bloqué mais il ne garantit pas l'idempotence serveur.
Avant de convertir les créations de commandes ou paiements, ajouter une clé d'intention
persistante et atomique côté base, avec résultat rejouable, tests de collision,
timeout et concurrence MySQL. **Aucune mutation financière n'est convertie ici.**
Ne jamais rejouer automatiquement un POST après un timeout.

## Composants et focus

La synchronisation `data-bp-key` ne modifie que les nœuds différents.
Un élément contenant le focus est préservé pour éviter l'interruption d'une saisie ;
la position de défilement est conservée. Les fragments HTML doivent provenir de
routes same-origin déjà protégées. Le nettoyage DOM refuse scripts et attributs
inline d'événement pour éviter d'exécuter du contenu dynamique non désiré.
