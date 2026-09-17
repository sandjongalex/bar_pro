# Bar Manager Pro

Application Flask multi-bar : API JWT ES256, vues Jinja2 de consultation et services métier
transactionnels. Le périmètre vérifié et les modules encore incomplets sont détaillés dans
[PROGRESS](docs/PROGRESS.md). Ne pas déduire une fonctionnalité de la seule présence d'une table.

## Démarrage local

Python 3.12+, environnement virtuel :

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Configurer une SECRET_KEY locale unique dans `.env`. Pour les routes d'authentification API,
générer une paire ES256 P-256 et définir `JWT_PRIVATE_KEY_FILE` / `JWT_PUBLIC_KEY_FILE`.
Aucun secret ni clé privée n'est livré.

```powershell
python -m flask --app wsgi:application db upgrade
python -m flask --app wsgi:application check-config
python -m flask --app wsgi:application routes
python -m flask --app wsgi:application create-superadmin
python -m flask --app wsgi:application run
```

SQLite convient au développement et aux tests locaux. Pour PythonAnywhere/MySQL, suivre
[DEPLOYMENT.md](docs/DEPLOYMENT.md), avec les fichiers de configuration dans `deploy/`.
Aucune migration ni connexion base n'est exécutée par la factory au démarrage.

## Vérification

```powershell
python -m pytest -q
python scripts/schema_document.py
```

Les tests créent leurs bases temporaires par Alembic, avec contraintes étrangères activées
pour les scénarios métier. Ils couvrent JWT, tenant, stock, inventaires, commandes, retours,
paiements et caisse. Le dictionnaire [DATA_MODEL](docs/DATA_MODEL.md) est comparé aux modèles.
Le SQL MySQL est compilé sans connexion serveur ; aucune validation MySQL réelle n'est revendiquée.
Les routes effectivement enregistrées sont dans [ROUTES](docs/ROUTES.md).

Les vues web proposent la consultation et un formulaire de création/confirmation de commande avec CSRF.
Les opérations métier documentées sont aussi disponibles via l'API. Les remboursements, dépenses,
garde/remises, abonnements, rapports et idempotence HTTP restent à réaliser.

Le module inventaires propose aussi un parcours web avec CSRF : création, comptage, validation, annulation du brouillon et impression. Tests dédiés : `python -m pytest tests/test_inventories.py -q`.
