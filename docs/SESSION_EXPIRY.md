# Sessions web et formulaires expirés

- Pages HTML protégées : une requête GET sans authentification renvoie une redirection HTTP 302 vers `/login`.
- Requêtes POST non authentifiées : HTTP 303 vers `/login`, **sans rejouer** le POST.
- AJAX web par cookie : HTTP 401 `SESSION_EXPIRED`. Le client commun navigue vers `/login?session=expired` lorsque ce code est reçu.
- Si une requête AJAX est redirigée vers `/login`, le client commun navigue vers le formulaire (sans rejouer la mutation).
- Un CSRF invalide sur `/login` ou sans authentification redirige vers un formulaire neuf. Une requête CSRF invalide **avec** authentification reste HTTP 400 `CSRF_REJECTED` : le CSRF et la session sont différents.
- Les droits refusés HTTP 403 ne déconnectent pas l'utilisateur.
- Une opération de stock, caisse, paiement ou livraison n'est jamais répétée automatiquement après une erreur ou une reconnexion.
- Les routes JWT Bearer `/api/v1/` conservent leur propre authentification.
- La durée du CSRF reste de 3600 s ; ceci n'est pas une durée maximale de session web. Les contrôles s'appliquent lors des requêtes, pas via une fermeture automatique du navigateur.
