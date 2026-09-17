# PythonAnywhere / MySQL

Configuration préparée, aucun déploiement exécuté et aucun secret fourni. Les fichiers
`deploy/pythonanywhere_wsgi.py` et `deploy/production.env.example` sont des modèles à adapter.

## Installation

1. Dans une console Bash PythonAnywhere, créer un environnement Python 3.12 puis installer
   `python -m pip install -r requirements.txt`. Configurer ce virtualenv dans l'onglet Web.
2. Créer une application Web en configuration manuelle. Copier le contenu de
   `deploy/pythonanywhere_wsgi.py` dans son fichier WSGI et remplacer les deux chemins.
3. Créer le fichier privé `/home/YOUR_USERNAME/.config/bar_pro/production.env` hors dépôt
   depuis le modèle, permissions `chmod 600`. Définir une SECRET_KEY aléatoire propre
   au déploiement, au moins 32 caractères. Ne jamais utiliser les valeurs des tests.
4. Générer une paire ES256 hors dépôt :

```bash
openssl ecparam -name prime256v1 -genkey -noout -out jwt-private.pem
openssl ec -in jwt-private.pem -pubout -out jwt-public.pem
chmod 600 jwt-private.pem
```

Définir les chemins absolus `JWT_PRIVATE_KEY_FILE` et `JWT_PUBLIC_KEY_FILE` dans le fichier
privé. Le chargeur valide une paire P-256 correspondante en production. Ne jamais copier
les clés dans le dépôt. Les commandes n'affichent pas la clé privée.

## Base et limiteur

Utiliser les valeurs de l'onglet Databases PythonAnywhere, et une URI
`mysql+pymysql://USER:PASSWORD_ENCODE@HOST/USER$DATABASE?charset=utf8mb4`.
Le mot de passe doit être encodé pour une URL. Le nom complet de base comporte le préfixe
utilisateur. Ne pas fournir cette URI au navigateur. Configurer InnoDB, utf8mb4 et le mode
SQL strict ; vérifier `SELECT VERSION(), @@sql_mode` sur l'instance réellement proposée.
La cible du projet est MySQL 8.4, avec CHECK actifs. Ne pas supposer qu'une autre version
PythonAnywhere satisfait les mêmes garanties.

Le pool utilise `pool_pre_ping=True` et `pool_recycle=280` dans
`SQLALCHEMY_ENGINE_OPTIONS`. Voir la documentation officielle sur
[les connexions](https://help.pythonanywhere.com/pages/ManagingDatabaseConnections/) et
[Flask sur PythonAnywhere](https://help.pythonanywhere.com/pages/Flask/).

Configurer `RATELIMIT_STORAGE_URI` vers un Redis partagé et durable accessible depuis
l'hébergement, avec TLS si distant (`rediss://...`). La dépendance redis est incluse.
Le stockage mémoire est refusé en production. La disponibilité réseau de ce service
et le niveau d'offre PythonAnywhere doivent être vérifiés sur le compte cible.

## Migrations et lancement

Dans le virtualenv, depuis le dépôt, utiliser exactement le même environnement privé que WSGI :

```bash
python -m flask --env-file /home/YOUR_USERNAME/.config/bar_pro/production.env --app wsgi:application check-config
python -m flask --env-file /home/YOUR_USERNAME/.config/bar_pro/production.env --app wsgi:application db upgrade
python -m flask --env-file /home/YOUR_USERNAME/.config/bar_pro/production.env --app wsgi:application db check
python -m flask --env-file /home/YOUR_USERNAME/.config/bar_pro/production.env --app wsgi:application routes
```

Pour une base existante, sauvegarder et essayer les migrations sur une copie avant production.
Les nouvelles contraintes peuvent refuser des données historiques incohérentes : ne pas les
corriger arbitrairement. Les anciens états POSTED/CLOSED sont convertis en CONFIRMED/SERVED
par la migration de cycle ; les paiements historiques sans ventilation reprennent `amount`.
Les downgrades destructifs sont volontairement refusés.

Créer le premier administrateur avec `flask --app wsgi:application create-superadmin` dans
le même environnement privé (invite masquée pour le mot de passe). Aucun compte par défaut
n'est installé. Ne pas lancer `seed-demo` en production.

Configurer `/static/` vers le répertoire `app/static`, puis recharger l'application dans
l'onglet Web. HTTPS est nécessaire aux cookies de session de production. Ne pas appeler
`app.run()` dans WSGI. Les migrations restent des commandes séparées du démarrage.
Tester `/health`, `/api/v1/health`, la connexion web et une connexion API réelle ; les
endpoints de santé ne vérifient pas la base ni Redis.

## Validation restant sur l'hébergement

Exécuter l'upgrade sur une base MySQL vide dédiée, `db check`, puis les tests d'intégrité et
de concurrence avec deux connexions indépendantes : caisse ouverte unique, stock concurrent,
retours/paiements simultanés, rotation refresh et suspension pendant une mutation.
Les tests SQLite et la compilation du SQL MySQL ne remplacent pas ces essais. Aucun test
ne doit viser une base de production contenant des données réelles.
