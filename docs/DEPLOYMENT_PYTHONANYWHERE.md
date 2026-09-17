# Déploiement PythonAnywhere

## État

Préparation terminée ; **déploiement réel bloqué par `EXTERNAL_PREREQUISITE`**.
Le dépôt ne contient ni compte PythonAnywhere, ni username, ni forfait, ni domaine,
ni base MySQL, ni secrets de production. Aucune URL publique n’est donc revendiquée.

Cette procédure suit la documentation officielle PythonAnywhere : application Flask
configurée depuis l’onglet Web avec un fichier WSGI, virtualenv utilisant la même
version Python que l’application, et configuration statique séparée
([Flask](https://help.pythonanywhere.com/pages/Flask/),
[virtualenv](https://help.pythonanywhere.com/pages/VirtualEnvForWebsites/)).

## Compatibilité vérifiée

- Le code utilise Flask 3.1+, SQLAlchemy, Alembic/Flask-Migrate et PyMySQL.
- PythonAnywhere documente actuellement Python 3.13 sur l’image système `innit` ;
  choisir dans l’onglet Web la même version que le virtualenv
  ([versions Python](https://help.pythonanywhere.com/pages/PythonVersions/)).
- Créer l’environnement dans une console Bash :

  ```bash
  mkvirtualenv --python=python3.13 bar-manager-pro
  pip install -r requirements.txt
  ```

- Flask doit être chargé par `application = create_app("production")` dans WSGI ;
  ne pas lancer `flask run` ni `app.run()`.
- `pool_recycle=280` est conservé, sous la limite d’inactivité MySQL documentée de
  300 secondes ([SQLAlchemy/MySQL](https://help.pythonanywhere.com/pages/UsingSQLAlchemywithMySQL/)).

## Prérequis à fournir

`EXTERNAL_PREREQUISITE` :

- compte et région PythonAnywhere (`www` ou `eu`) ;
- username et forfait compatible ; les comptes gratuits récents peuvent ne pas
  disposer de MySQL, et le compte gratuit est limité à 1 web app/worker et 512 MiB
  ([fonctionnalités gratuites](https://help.pythonanywhere.com/pages/FreeAccountsFeatures/),
  [quota disque](https://help.pythonanywhere.com/pages/DiskQuota/)) ;
- domaine ou URL `username.pythonanywhere.com` ;
- hôte, nom, utilisateur et mot de passe MySQL PythonAnywhere ;
- `SECRET_KEY` aléatoire d’au moins 32 caractères ;
- paire de clés ES256 JWT ;
- Redis durable accessible pour `RATELIMIT_STORAGE_URI` ;
- décision de stockage des uploads et chemin persistant.

Les valeurs ne doivent jamais être ajoutées à Git ou à ce document.

## Configuration privée

Créer hors dépôt, par exemple `/home/USERNAME/.config/bar_pro/production.env`, avec
permissions restrictives :

```dotenv
FLASK_CONFIG=production
SECRET_KEY=<valeur aléatoire>
SQLALCHEMY_DATABASE_URI=mysql+pymysql://<user>:<url-encoded-password>@<host>/<user>$<database>?charset=utf8mb4
RATELIMIT_STORAGE_URI=redis://<durable-redis>/0
JWT_PRIVATE_KEY_FILE=/home/USERNAME/.config/bar_pro/jwt-private.pem
JWT_PUBLIC_KEY_FILE=/home/USERNAME/.config/bar_pro/jwt-public.pem
DEBUG=False
```

La configuration production refuse toute base non `mysql+pymysql`, les clés JWT
absentes/non correspondantes et le rate limiting mémoire. `SESSION_COOKIE_SECURE`
est activé par `ProductionConfig`, avec cookie HTTP-only et SameSite `Lax`.

## WSGI et fichiers statiques

Copier `deploy/pythonanywhere_wsgi.py` dans le fichier WSGI de l’onglet Web puis
remplacer les chemins `YOUR_USERNAME`. Le fichier charge l’environnement privé avant
`create_app("production")`. Configurer dans l’onglet Web le mapping :

```text
URL : /static/
Path : /home/USERNAME/bar_pro/app/static
```

PythonAnywhere documente les mappings statiques dans
[Static files mappings](https://help.pythonanywhere.com/pages/StaticFiles/).
Les uploads doivent rester hors `app/static`, avec noms générés, validation de type,
limite de taille et aucun droit d’exécution. Le projet actuel refuse encore le
stockage d’images/logos non configuré : ne pas activer d’upload avant décision et
stockage dédiés.

## Base, sauvegarde et migrations

Utiliser une base MySQL distincte pour test et production. Avant toute migration sur
une base contenant des données : effectuer un dump et vérifier sa restauration sur une
copie. Ne jamais versionner le dump.

```bash
workon bar-manager-pro
cd /home/USERNAME/bar_pro
python -m flask --app wsgi:application check-config
python -m flask --app wsgi:application db upgrade
python -m flask --app wsgi:application db check
python -m flask --app wsgi:application routes
```

Ne jamais utiliser `drop_all`, `create_all` ou une fixture de démonstration en
production. Créer le super-admin uniquement via la commande CLI sécurisée existante,
avec un mot de passe saisi interactif.

## Logs, HTTPS et mise à jour

Les logs PythonAnywhere sont accessibles depuis l’onglet Web ; ne journaliser ni
secrets, tokens, mots de passe, credentials MySQL ni données bancaires. Activer HTTPS
dans l’onglet Web ; pour un domaine personnalisé, PythonAnywhere recommande un CNAME
et un certificat SSL ([custom domains](https://help.pythonanywhere.com/pages/CustomDomains/)).

Procédure : backup vérifié, récupération du code, activation du virtualenv,
installation des requirements, contrôle de configuration, migration, reload Web,
smoke tests (`/health`, connexion web/API, statiques), puis inspection des logs.

Rollback : revenir à la version de code précédente ; si une migration est en cause,
appliquer une migration corrective testée ou restaurer la sauvegarde. Aucun downgrade
automatique destructif n’est fourni.

## Vérifications non exécutées

Sans compte et accès externes, les éléments suivants restent non vérifiés : version
MySQL réelle, InnoDB/utf8mb4 sur l’instance cible, migrations sur serveur distant,
Redis, domaine/HTTPS, reload WSGI, smoke tests publics, backup/restauration et quotas
du compte réel.
