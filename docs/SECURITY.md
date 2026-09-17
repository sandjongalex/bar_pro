# Sécurité applicative

Document établi par inspection du code. Il décrit les protections réellement
présentes et les limites observées ; il ne constitue pas une attestation de
conformité, un test d’intrusion ou une preuve d’exploitation.

Procédures liées : [TESTING.md](TESTING.md) pour les tests disponibles et
[DEPLOYMENT.md](DEPLOYMENT.md) pour la préparation PythonAnywhere, MySQL, Redis
et les secrets hors dépôt.

## Périmètre inspecté

Fichiers vérifiés : `app/auth.py`, `app/permissions.py`, `app/config.py`,
`app/extensions.py`, `app/__init__.py`, `app/models.py`, `app/errors.py`,
`app/audit.py`, `app/bar_services.py`, `app/catalog_services.py`, routes
HTML/API dans `app/*.py`, `app/templates/`, `.env.example`, `.gitignore`,
`deploy/`, `docs/DEPLOYMENT.md`, tests d’authentification, configuration,
workflows, finance et inventaires.

## Protections implémentées

| Sujet | État | Preuve | Observation |
| --- | --- | --- | --- |
| Mots de passe | Implémenté | `User.set_password`, `User.check_password` dans `app/models.py` | Hash et vérification via Werkzeug. Aucun mot de passe brut stocké par les modèles. |
| Sessions web | Implémenté | Flask-Login dans `app/auth.py`, `LoginManager` dans `app/extensions.py` | Session Flask signée par `SECRET_KEY`; `credentials_version` stocké en session et vérifié au chargement utilisateur. |
| Cookies | Configuré | `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"` dans `app/config.py`; `SESSION_COOKIE_SECURE=True` en production | La sécurité dépend de la configuration de production et de HTTPS côté hébergeur. |
| API JWT | Implémenté | `api_required`, `issue` dans `app/auth.py` | Bearer uniquement, ES256, issuer/audience, `exp`, `iat`, `nbf`, `sub`, `bar_id`, `jti`, `token_use`, `credentials_version`, `sid`. |
| Refresh tokens | Implémenté | `ApiToken`, `TokenRevocation`, `digest`, `refresh`, `api_logout` | Refresh opaque généré avec `secrets.token_urlsafe`, hash SHA-256 stocké, rotation et révocation familiale. |
| Révocation par compte | Implémenté | `credentials_version` dans `User`, session web et claims JWT | Un changement de version invalide sessions web et access tokens chargés ensuite. |
| CSRF web | Implémenté | `CSRFProtect`, tokens dans templates, tests CSRF | Formulaires web protégés ; routes API exemptées et protégées par Bearer. |
| Séparation web/API | Implémenté | `api_required`, exemptions CSRF API, tests `test_every_business_api_route_requires_bearer` | Les cookies web ne suffisent pas pour les routes API métier. |
| Rate limiting | Configuré | `Flask-Limiter`, `LOGIN_RATE_LIMIT`, décorateurs login/token | Actif hors test ; stockage mémoire refusé en production par `validate_config`. |
| Permissions serveur | Implémenté | `PermissionService.evaluate/require` | Rôles globaux et locaux, bar suspendu, actions d’écriture et verrou logique par `require`. |
| Isolation tenant | Implémenté partiellement par routes/services/SQL | `bar_id` JWT, contrôle `bar_id` chemin, FK composites, tests tenant | Nombreux services filtrent par `bar_id` et contrôlent les références secondaires. |
| Validation numérique | Implémenté dans services concernés | `app/validation.py` | Refus des valeurs non finies et précision explicite, utilisé par stock, inventaires, finances, catalogue. |
| Erreurs contrôlées | Implémenté | `app/errors.py` | Rollback sur erreurs courantes, réponse générique sans trace SQL. |
| Secrets externes | Configuré | `.env.example`, `.gitignore`, `deploy/production.env.example`, `config.load_environment` | Secrets attendus par variables ou fichiers hors dépôt ; `.env`, clés et SQLite ignorés. |
| Upload catalogue | Absence volontaire d’usage | `catalog_services.image_key` | Upload reçu mais stockage refusé (`IMAGE_STORAGE_UNAVAILABLE`). |

## Configuré mais non vérifié en exploitation

| Sujet | Preuve | Limite |
| --- | --- | --- |
| HTTPS de production | `SESSION_COOKIE_SECURE=True`, `PREFERRED_URL_SCHEME="https"` | Le code exige une configuration favorable, mais ne prouve pas TLS côté hébergeur. |
| Redis durable pour limiter | `validate_config` refuse `memory://` en production | Les tests ne valident pas une instance Redis réelle. |
| MySQL/PyMySQL | production exige `mysql+pymysql://` | Les tests SQLite et la compilation SQL MySQL ne prouvent pas le fonctionnement sur serveur MySQL. |
| Rotation opérationnelle des clés JWT | clés chargées par env/fichiers | Pas de procédure applicative de rotation de clés ni de `kid` observé. |
| Sauvegardes et restauration | `DEPLOYMENT.md` recommande sauvegarde avant migration | Pas de mécanisme applicatif de sauvegarde. |
| En-têtes HTTP de sécurité | dépendants du serveur/hébergeur | Pas de configuration applicative CSP, HSTS, X-Frame-Options ou Referrer-Policy observée. |
| Concurrence réelle | `with_for_update`, contraintes SQL, tests SQLite | À valider sur MySQL avec connexions concurrentes comme indiqué dans [DEPLOYMENT.md](DEPLOYMENT.md). |

## Absences observées

| Sujet | Preuve | Impact possible |
| --- | --- | --- |
| Workflow `UserSession` | table dans `app/models.py`, aucune route/service associé | La table ne prouve pas une gestion serveur des sessions web, révocation fine ou suivi des appareils. |
| Idempotence HTTP | table `IdempotencyRecord`, aucune logique de rejeu observée | Les références uniques peuvent éviter certains doublons, mais pas rejouer une réponse HTTP. |
| Upload sécurisé opérationnel | `image_key` lève immédiatement `IMAGE_STORAGE_UNAVAILABLE` | Pas de stockage, scan, signature d’URL ou politique de conservation à auditer. |
| Journal d’audit complet | `audit.record` appelé par plusieurs services, pas partout | Actions sensibles non instrumentées peuvent manquer de trace métier. |
| Immutabilité SQL de l’audit | table `AuditLog`, pas de trigger/permission SQL observé | Le code écrit des lignes d’audit, mais la base ne les rend pas immuables par construction. |
| Tests navigateur | suite FlaskClient uniquement | Aucun contrôle rendu navigateur, CSP ou comportement JS réel. |
| Dépendances verrouillées | `requirements.txt` avec plages de versions | Pas de lockfile ni hash de dépendances observé. |

## Risques et recommandations

| Risque | Fichier / symbole | Observation | Impact possible | Conditions nécessaires | Preuve | Recommandation |
| --- | --- | --- | --- | --- | --- | --- |
| HTML concaténé sans échappement | `app/bars.py::web_list` | Retourne `"\n".join(f"{b.id}: {b.name}" ...)` sans template Jinja. | Si un nom de bar contient du HTML, le navigateur peut l’interpréter. | Nom de bar contrôlé par un acteur autorisé et réponse servie en HTML. | Statique, pas d’exploitation démontrée. | Rendre via template Jinja ou échapper explicitement les champs. |
| Audit partiel | `app/audit.py::record`, appels dispersés | Audit présent pour commandes, paiements, achats, caisse, mais pas uniformément sur tous les flux, permissions refusées ou auth. | Traçabilité incomplète lors d’incident ou litige. | Action sensible non couverte par un appel `record`. | Recherche d’appels `record(`. | Définir une matrice d’audit minimale par action sensible et la tester. |
| Différence `evaluate` / `require` | `bar_services.require` | Utilise `permissions.evaluate` et non `permissions.require`; ne bénéficie pas du verrou `with_for_update` appliqué aux écritures par `require`. | Moins de protection contre une modification concurrente du statut du bar pendant certaines écritures. | Changement concurrent de bar/statut autour de création/modification de bar ou staff. | Statique ; aucun test concurrent MySQL. | Utiliser `permissions.require` pour les écritures ou justifier l’exception. |
| Validation variable selon modules | routes et services | Certains endpoints délèguent aux services, d’autres accèdent à `request.json[...]` ou capturent en `400`; validation homogène absente. | Codes et messages variables, risque d’erreur non anticipée si le service ne valide pas un champ. | Corps mal formé ou champ de type inattendu. | Code routes + tests `invalid_api_body_never_returns_500` limités. | Centraliser les schémas d’entrée critiques ou documenter les différences. |
| UserSession non exploité | `app/models.py::UserSession` | Table avec digest session/CSRF, sans workflow applicatif observé. | Attentes de révocation serveur ou suivi de session non satisfaites. | Opérateur ou développeur supposant la table active. | Absence de route/service repérée. | Ne pas présenter cette table comme fonctionnalité livrée ; concevoir le workflow avant usage. |
| Idempotence HTTP absente | `IdempotencyRecord` | Table présente, aucune lecture d’en-tête ou rejeu de réponse observé. | Retentatives client peuvent créer des doublons selon les références et transactions. | Client réseau retry après timeout. | Absence de logique route/service. | Implémenter une politique `Idempotency-Key` avant annonce publique. |
| Uploads refusés | `catalog_services.image_key` | Code mort après `raise ValueError("IMAGE_STORAGE_UNAVAILABLE")`. | Pas de risque de stockage non maîtrisé actuellement, mais fonctionnalité indisponible. | Demande produit d’upload catalogue/logo. | Statique. | Garder le refus tant que stockage, validation, taille, scan et droits ne sont pas conçus. |
| Dépendances non verrouillées | `requirements.txt` | Plages `>=,<` sans lock ni hashes. | Dérive de versions entre environnements, régression ou correctif manqué. | Installation fraîche à une date différente. | Inspection fichier. | Ajouter une stratégie de verrouillage si le projet entre en exploitation. |
| Bootstrap via CDN | `app/templates/layout.html` | CSS/JS chargés depuis jsDelivr sans SRI. | Dépendance disponibilité/intégrité d’un tiers pour l’interface. | Accès web utilisateur, CDN indisponible ou compromis. | Statique. | Évaluer hébergement local ou SRI/CSP avant production sensible. |
| Redis/MySQL non validés réellement par tests | `tests/test_schema.py`, `tests/test_config.py` | Tests valident configuration et compilation SQL, pas services externes réels. | Faux sentiment de validation production. | Déploiement sur services non testés. | [TESTING.md](TESTING.md), tests inspectés. | Exécuter les validations d’hébergement de [DEPLOYMENT.md](DEPLOYMENT.md). |
| En-têtes de sécurité non observés | `app/__init__.py`, templates | Pas de CSP/HSTS/X-Frame-Options applicatifs. | Protection navigateur dépend de l’hébergeur et reste à confirmer. | Exposition HTTP publique. | Inspection statique. | Définir les en-têtes au niveau serveur ou middleware selon le déploiement. |
| Rate limit dépend du stockage | `app/config.py`, `app/extensions.py` | Limiteur configuré ; production refuse mémoire, mais Redis réel non testé. | Contournement ou inefficacité si stockage indisponible/mal configuré. | Déploiement avec Redis inaccessible ou non partagé. | Tests config seulement. | Test de charge minimal et vérification Redis avant ouverture. |

## Tests de sécurité disponibles

La suite vérifie notamment :

- login API, refresh, logout, désactivation de compte et CSRF web
  (`tests/test_auth.py`) ;
- bearer obligatoire, isolation tenant, bar suspendu, refresh reuse,
  claims JWT invalides, mauvais algorithme, utilisateur désactivé, affiliation
  terminée (`tests/test_workflows.py`) ;
- refus de configuration production dangereuse : secret court, SQLite en
  production, limiteur mémoire, clés JWT invalides (`tests/test_config.py`) ;
- permissions finance et rollback sur opérations composées
  (`tests/test_finance.py`) ;
- permissions et portée d’inventaire (`tests/test_inventories.py`).

Voir [TESTING.md](TESTING.md) pour les commandes. Ces tests restent des tests
Flask/SQLite et ne remplacent pas un test navigateur, un audit de dépendances,
un test Redis/MySQL réel ou un essai de concurrence multi-connexion.

## Points à vérifier avant exploitation

- HTTPS effectif, cookies `Secure` en production et redirections côté hébergeur.
- Redis durable réellement accessible par le limiteur.
- MySQL réel : migrations, contraintes, verrous et concurrence.
- En-têtes de sécurité HTTP adaptés au déploiement.
- Rotation opérationnelle des clés JWT et procédure d’urgence.
- Politique de sauvegarde/restauration, journalisation et rétention d’audit.
- Stratégie de verrouillage et mise à jour des dépendances.

## Exécution pendant cette mission

Date : 2026-09-17, fuseau `Africa/Lagos`.

Tests exécutés : non exécuté.

Raison : création documentaire par inspection statique du code, des templates,
des exemples de configuration/déploiement et des tests existants, sans changement
applicatif.
