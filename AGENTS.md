# Instructions Codex pour Bar Manager Pro

Ce fichier fixe les consignes de travail dans ce projet. Applique-le avant
toute modification de code, de migration, de test ou de documentation.

## Reperes constates dans le code

- Traite l'application comme un monolithe Flask actuel, initialise par
  [`app/__init__.py`](app/__init__.py), avec extensions partagees dans
  [`app/extensions.py`](app/extensions.py).
- Respecte les Blueprints plats existants dans `app/*.py`, les services
  `app/*_services.py` et [`app/stock_service.py`](app/stock_service.py), et les modeles regroupes dans
  [`app/models.py`](app/models.py).
- Verifie les faits dans le code et les tests avant de t'appuyer sur la
  documentation : [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) decrit une
  structure cible differente du code courant.
- N'assimile pas ces reperes a une garantie generale : confirme toujours le
  comportement exact dans les fichiers touches.

## Lecture ciblee avant modification

- Lis d'abord la zone a modifier, ses tests, ses services et ses appels.
- Recherche les usages avec `rg` avant de renommer, supprimer ou changer un
  contrat public.
- Ne charge pas tous les documents a chaque tache ; ouvre seulement ceux qui
  eclairent le changement demande.
- Avant toute operation Git, execute `git rev-parse --show-toplevel`.
- Si la racine Git pointe hors `bar_pro`, comme `C:/Users/pc`, ne fais aucun
  staging global et ne modifie pas le depot parent.

| Sujet | Lis en priorite |
| --- | --- |
| Schema | [`app/models.py`](app/models.py), [`migrations/versions/`](migrations/versions/), [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md), [`tests/test_schema.py`](tests/test_schema.py) |
| API | routes concernees dans `app/*.py`, [`docs/API.md`](docs/API.md), tests HTTP associes |
| Securite | [`app/auth.py`](app/auth.py), [`app/permissions.py`](app/permissions.py), [`app/config.py`](app/config.py), tests d'authentification |
| Interface | [`app/templates/`](app/templates/), routes web concernees, formulaires et tests CSRF |
| Migrations | revision Alembic precedente, modele cible, donnees existantes et tests SQLite |
| Metier | service concerne, [`docs/BUSINESS_RULES.md`](docs/BUSINESS_RULES.md), workflows de tests |

## Regles de changement

- Limite toute creation, modification ou suppression au perimetre necessaire.
- Conserve les conventions locales de nommage, d'organisation et de style.
- N'applique aucun reformatage, deplacement ou refonte sans lien direct avec la
  tache.
- Decoupe les changements importants en etapes verifiables.
- Ne transforme pas implicitement l'architecture, les dependances ou les flux
  metier sous couvert d'une correction locale.
- Reutilise les dependances de [`requirements.txt`](requirements.txt) quand
  elles suffisent.
- Justifie tout ajout de dependance par un besoin concret, son impact runtime
  et son impact tests.
- Mets a jour la documentation seulement pour les sujets effectivement changes.

## Configuration et secrets

- Garde la configuration par environnement dans [`app/config.py`](app/config.py)
  et les variables d'environnement.
- Ne code jamais de secret, cle privee, jeton, mot de passe ou URL sensible.
- Ne journalise pas les secrets, les credentials ni les contenus SQL detailles.
- Preserve les exigences de production deja verifiees par la configuration :
  secret explicite, stockage de limiteur non memoire et base cible MySQL.

## Authentification, permissions et isolation

- Distingue l'API JWT Bearer des flux web par cookie Flask-Login.
- Maintiens le CSRF sur les formulaires web et les tests qui l'activent.
- Ne remplace pas une verification serveur par un controle d'interface.
- Passe par [`app/permissions.py`](app/permissions.py) pour les decisions
  d'autorisation et respecte les actions existantes.
- Verifie l'isolation par bar sur les chemins, objets charges et mutations.
- Controle les references secondaires, comme commande, achat, inventaire,
  retour ou mouvement inverse, avant toute mutation liee.
- Ne suppose pas qu'un garde web global suffit pour les droits metier d'une
  action precise.

## Donnees numeriques et stock

- Utilise `Decimal` pour les montants, quantites et prix.
- Declare la precision attendue et respecte les echelles locales.
- Refuse les valeurs non finies, les flottants implicites et les arrondis
  silencieux.
- Fais passer les mouvements de stock par
  [`app/stock_service.py`](app/stock_service.py).
- Garde les transactions composees atomiques : aucun etat partiel ne doit
  rester apres une erreur controlee.
- Ne place pas de `commit` profond dans un service si l'appelant compose
  plusieurs operations metier.
- Preserve les verifications de bar, produit, sens, quantite et references
  secondaires autour des mouvements.

## Erreurs et transactions

- Retourne des erreurs controlees avec le statut HTTP existant du flux touche.
- Effectue un rollback quand une mutation echoue.
- Ne laisse pas fuiter les details SQL, contraintes internes ou traces serveur
  dans les reponses.
- Respecte les enveloppes JSON, redirections, messages flash et statuts deja
  couverts par les tests.
- Ajoute un test de rollback si une nouvelle mutation composee peut echouer
  apres un premier effet de bord.

## Migrations et compatibilite base

- Ecris des migrations Alembic explicites pour tout changement de schema.
- Preserve les donnees existantes par backfill, valeur par defaut ou etape de
  transition documentee.
- Ne remplace pas une migration par `db.create_all`.
- Garde la compatibilite avec SQLite de test et MySQL cible.
- Ne declare pas MySQL valide sur la seule base des tests SQLite.
- Verifie les types, index, contraintes et valeurs historiques avant de
  modifier une revision.
- Ne reecris pas l'historique de migrations existant sans consigne explicite.

## Contrats HTTP, Jinja2 et performances

- Maintiens les contrats HTTP existants : routes, methodes, statuts,
  enveloppes, noms de champs et formats serialises.
- Preserve les formulaires Jinja2 : `name`, `action`, `method`, jeton CSRF,
  messages affiches et endpoints references.
- Avant de changer une liste, un agregat ou un tableau de bord, inspecte les
  volumes attendus, les boucles de requetes et les appels repetes.
- Evite d'ajouter des requetes par ligne quand une jointure, un chargement
  cible ou un agregat borne suffit dans le style local.
- Garde les bornes de pagination ou de limite quand elles existent.

## Tests et documentation

- Lance d'abord les tests cibles du module modifie.
- Elargis ensuite a la suite pertinente quand le changement touche un contrat,
  un service partage, une migration ou l'authentification.
- Rapporte les commandes lancees, leurs resultats et les limites restantes.
- Ne pretends jamais avoir execute une verification non lancee.
- [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) est genere par
  [`scripts/schema_document.py`](scripts/schema_document.py).
- [`tests/test_schema.py`](tests/test_schema.py) compare ce document exactement
  au rendu du script ; ne l'edite pas librement.
- Si le modele change, modifie le code source du schema, regenere le document
  par le script et garde le test exact vert.
- Ne transforme pas ce fichier en catalogue complet des routes, schema detaille
  ou roadmap.
