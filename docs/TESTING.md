# Guide de tests

Ce document décrit la suite réellement présente dans le dépôt. Il ne définit
pas de CI, de couverture, de lint, de tests navigateur ou d’infrastructure E2E
qui ne figurent pas dans le code.

Sources vérifiées : [`pytest.ini`](../pytest.ini), [`requirements.txt`](../requirements.txt),
[`tests/conftest.py`](../tests/conftest.py), [`tests/factories.py`](../tests/factories.py),
`tests/test_*.py`, [`app/config.py`](../app/config.py) et
[`scripts/schema_document.py`](../scripts/schema_document.py).

## Outils et configuration

La suite utilise `pytest` avec Flask, Flask-SQLAlchemy, Flask-Migrate/Alembic,
Flask-Login, Flask-WTF, Flask-Limiter, PyJWT avec support crypto, PyMySQL et Redis
comme dépendances déclarées dans [`requirements.txt`](../requirements.txt).

[`pytest.ini`](../pytest.ini) configure :

| Réglage | Valeur |
| --- | --- |
| `testpaths` | `tests` |
| `python_files` | `test_*.py` |
| `addopts` | `-ra` |
| `pythonpath` | `.` |

[`tests/conftest.py`](../tests/conftest.py) fixe des valeurs de test par défaut :
`SECRET_KEY=test-only-environment-value` et
`TEST_DATABASE_URI=sqlite+pysqlite:///:memory:` si l’environnement ne les fournit
pas. La fixture `app` instancie `create_app("testing")`; la fixture `client`
renvoie `app.test_client()`.

[`app/config.py`](../app/config.py) définit `TestingConfig` avec `TESTING=True`,
`WTF_CSRF_ENABLED=False`, limiteur désactivé et base SQLite de test. Les tests qui
vérifient explicitement le CSRF réactivent `WTF_CSRF_ENABLED=True` dans leur
configuration locale.

## Structure actuelle

| Fichier | Couverture principale |
| --- | --- |
| [`tests/test_app.py`](../tests/test_app.py) | factory Flask, healthchecks, enveloppe JSON 404 API |
| [`tests/test_auth.py`](../tests/test_auth.py) | login API, refresh, logout, désactivation, login web avec CSRF |
| [`tests/test_config.py`](../tests/test_config.py) | validation de configuration production, clés ES256, chargement par fichiers |
| [`tests/test_finance.py`](../tests/test_finance.py) | remboursements, annulation payée, caisse, gardes/remises, rollback finance |
| [`tests/test_inventories.py`](../tests/test_inventories.py) | inventaires API/web, snapshots, comptage, validation, rollback |
| [`tests/test_schema.py`](../tests/test_schema.py) | migrations Alembic, compatibilité métadonnées, doc générée, compilation SQL MySQL |
| [`tests/test_workflows.py`](../tests/test_workflows.py) | parcours stock-commandes-achats-paiements, permissions, JWT, HTTP Flask |

[`tests/factories.py`](../tests/factories.py) contient de petites factories
fictives pour `User` propriétaire et `Bar`. Elles n’utilisent aucune donnée de
production.

## Bases de test et migrations

Les tests ne doivent jamais utiliser une base de production. Les tests qui ont
besoin du schéma créent des bases SQLite temporaires, souvent dans `tmp_path`,
puis appellent Alembic via `flask_migrate.upgrade()`.

Exemples présents :

- [`tests/test_schema.py`](../tests/test_schema.py) crée des fichiers SQLite
  temporaires comme `schema.sqlite3`, `empty.sqlite` ou `legacy.sqlite`.
- [`tests/test_auth.py`](../tests/test_auth.py) crée `auth.sqlite`.
- [`tests/test_workflows.py`](../tests/test_workflows.py) crée `flow.sqlite`.
- La fixture globale simple peut utiliser SQLite mémoire via `TEST_DATABASE_URI`.

Les tests de schéma comparent les tables migrées à `db.metadata`, vérifient la
nullabilité, les CHECK constraints et `alembic.autogenerate.compare_metadata`.
Ils testent aussi que le downgrade destructif initial est refusé.

La compilation SQL MySQL dans [`tests/test_schema.py`](../tests/test_schema.py)
sert à vérifier que le SQL Alembic se génère sans clé `BLOB` et contient des
contraintes attendues. Elle ne prouve pas qu’un serveur MySQL réel fonctionne.

## Clés étrangères SQLite

SQLite n’active pas toujours les clés étrangères par défaut. La fixture `env` de
[`tests/test_workflows.py`](../tests/test_workflows.py) exécute explicitement :

```sql
PRAGMA foreign_keys=ON
```

Cette fixture sert aux parcours métiers qui vérifient aussi des rejets de
références secondaires par service et par base. Les autres tests de migration
s’appuient sur les migrations et les comparaisons de métadonnées, sans présenter
SQLite comme équivalent complet à MySQL.

## Utilisateurs, données fictives et JWT ES256

Les tests construisent des utilisateurs fictifs avec des emails `.example.invalid`.
Les scénarios typiques créent :

- un propriétaire `OWNER` actif ;
- un autre propriétaire et un autre bar pour tester l’isolation tenant ;
- un employé `EMPLOYEE` avec affiliation locale `SERVER` ;
- un catalogue minimal, du stock initial et un bar en devise `XAF`.

Les tests d’authentification et de workflows génèrent des clés P-256 en mémoire
avec `cryptography.hazmat.primitives.asymmetric.ec.generate_private_key`.
La clé privée PEM est injectée dans `JWT_PRIVATE_KEY` de la configuration de test.
Les tests de configuration production vérifient aussi une paire privée/publique
ES256 et le chargement via fichiers temporaires.

Les tokens API sont signés en ES256 dans les tests qui passent par `app.auth.issue`
ou par `POST /api/v1/auth/tokens`. Les tests couvrent notamment expiration, audience
incorrecte, `token_use` incorrect, claim manquant, `credentials_version`, utilisateur
désactivé, affiliation terminée, mauvais algorithme et altération de payload.

## CSRF et HTTP Flask

Les tests HTTP utilisent `FlaskClient` (`app.test_client()`), pas un navigateur.
Ils vérifient des routes Flask, des statuts HTTP, des enveloppes JSON, des
redirections et du HTML rendu, mais pas le comportement d’un navigateur réel.

Par défaut, `TestingConfig` désactive le CSRF. Les scénarios qui doivent vérifier
le flux web l’activent explicitement :

- [`tests/test_auth.py`](../tests/test_auth.py) vérifie que le login web exige un
  jeton CSRF et crée une session.
- [`tests/test_workflows.py`](../tests/test_workflows.py) vérifie login web,
  protection tenant HTML et formulaire de commande avec CSRF.
- [`tests/test_inventories.py`](../tests/test_inventories.py) vérifie création,
  comptage, validation et impression d’inventaire via formulaires CSRF.
- [`tests/test_finance.py`](../tests/test_finance.py) vérifie l’écran finance web
  et un POST avec CSRF.

Les routes API métier restent authentifiées par Bearer JWT et ne s’appuient pas
sur les cookies web.

## Types de tests présents

### Logique métier

Les services sont appelés directement pour vérifier les règles internes :
stock non négatif, quantités décimales, transitions de commandes, inventaires,
achats, paiements, remboursements, remises de caisse et annulations.

Les tests vérifient aussi des erreurs contrôlées par `pytest.raises`, puis
effectuent souvent `db.session.rollback()` pour confirmer l’absence d’état
partiel.

### Intégration base

Les tests avec bases temporaires vérifient les migrations Alembic, les contraintes
SQL, l’unicité, les CHECK constraints, les FK composites quand SQLite les applique,
les backfills de migrations historiques et la correspondance avec les modèles.

### Parcours HTTP

Les tests API et web utilisent `FlaskClient`. Ils vérifient notamment :

- healthchecks ;
- login, refresh, logout ;
- exigence Bearer des routes métier ;
- isolation par bar signée dans le JWT ;
- permissions `403`, masquage tenant `404`, erreurs `400/401/409/422` selon les flux ;
- parcours commande → confirmation → caisse → paiement → clôture ;
- fournisseurs, achats, remboursements, inventaires et finance web.

## Dictionnaire de données généré

[`docs/DATA_MODEL.md`](DATA_MODEL.md) est généré par
[`scripts/schema_document.py`](../scripts/schema_document.py). La commande :

```bash
python scripts/schema_document.py
```

écrit le fichier `docs/DATA_MODEL.md`. Ce n’est pas une simple commande de
contrôle. Après l’avoir exécutée, il faut vérifier le résultat avec le test exact :

```bash
python -m pytest tests/test_schema.py -q
```

[`tests/test_schema.py`](../tests/test_schema.py) compare exactement le contenu de
`DATA_MODEL.md` à `scripts.schema_document.render()`.

## Commandes existantes

Exécuter depuis la racine du projet, avec une configuration de test isolée et sans
base de production.

| Usage | Commande |
| --- | --- |
| Suite complète | `python -m pytest -q` |
| Authentification | `python -m pytest tests/test_auth.py -q` |
| Workflows transverses | `python -m pytest tests/test_workflows.py -q` |
| Finance et caisse | `python -m pytest tests/test_finance.py -q` |
| Inventaires | `python -m pytest tests/test_inventories.py -q` |
| Schéma et configuration | `python -m pytest tests/test_schema.py tests/test_config.py -q` |

## Choix des tests selon le changement

| Changement | Tests cibles minimaux |
| --- | --- |
| Auth, JWT, permissions API | `python -m pytest tests/test_auth.py tests/test_workflows.py -q` |
| Configuration ou secrets | `python -m pytest tests/test_config.py -q` |
| Modèle ou migration | `python -m pytest tests/test_schema.py tests/test_config.py -q` |
| Commandes, stock, achats, tenant | `python -m pytest tests/test_workflows.py -q` |
| Paiements, remboursements, caisse, remises | `python -m pytest tests/test_finance.py tests/test_workflows.py -q` |
| Inventaires | `python -m pytest tests/test_inventories.py tests/test_workflows.py -q` |
| Documentation simple | contrôle de cohérence documentaire ; pas de test artificiel requis |
| `DATA_MODEL.md` ou générateur de schéma | `python scripts/schema_document.py`, puis `python -m pytest tests/test_schema.py -q` |

Élargir à `python -m pytest -q` quand le changement touche un contrat partagé,
une migration, l’authentification, les permissions ou plusieurs domaines métier.

## Critères de fin de tâche

Une tâche est prête à être rendue quand les points applicables sont couverts :

- tests ciblés exécutés pour le module ou contrat modifié ;
- régression pertinente élargie si le changement touche un service partagé ;
- base de test isolée, jamais production ;
- permissions et isolation par bar vérifiées si la tâche touche un accès ou une mutation ;
- rollback vérifié pour toute mutation composée qui peut échouer après un premier effet ;
- migrations appliquées sur base temporaire pour tout changement de schéma ;
- `DATA_MODEL.md` régénéré et comparé si le modèle ou le générateur change ;
- documentation affectée mise à jour ;
- limites restantes et tests non exécutés rapportés explicitement.

Une modification documentaire simple ne justifie pas un nouveau test artificiel.
Le dictionnaire généré fait exception : il doit rester reproductible par son script
et par le test exact.

## Limites connues

- Les tests HTTP Flask ne sont pas des tests de navigateur.
- La compilation SQL MySQL ne valide pas une base MySQL réelle.
- Aucune couverture de performance, charge, concurrence multi-processus ou E2E
  navigateur n’est déclarée dans le dépôt.
- Aucun outil de couverture, lint ou CI n’est documenté comme présent.
- Les tests utilisent des données fictives et des clés générées pour la session
  de test ; ils ne doivent pas accéder à des secrets ou bases utilisateur.

## Exécution pendant cette mission

Date : 2026-09-17, fuseau `Africa/Lagos`.

Commande pytest exécutée : non exécuté.

Raison : la mission a créé une documentation opérationnelle à partir de la suite
existante, sans modifier le code applicatif, les tests, les migrations ni le
dictionnaire généré.
