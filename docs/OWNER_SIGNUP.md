# BAR_PRO — inscription propriétaire et essai automatique de 15 jours

## Principe

À la création d'un compte, **le premier bar est opérationnel immédiatement**.
Il n'attend pas d'approbation par le SUPER_ADMIN. La création du compte, du bar,
du catalogue initial et de la souscription d'essai est une transaction atomique.

- Compte propriétaire : `OWNER`, actif dès l'inscription.
- Premier bar : `ACTIVE`, gouvernance `APPROVED`.
- Essai : plan `BARPRO_TRIAL_15D`, gratuit, statut `ACTIVE`, **15 × 24 heures** à partir de l'inscription.
- Connexion : session du propriétaire ouverte automatiquement puis redirection vers le tableau de bord de son premier bar.
- Aucun prélèvement automatique ni demande de paiement lors de l'inscription.
- L'essai gratuit n'est accordé qu'au **premier bar** créé avec cette inscription. La création d'autres bars reste un parcours distinct administré par la plateforme.

## Fin de l'essai / paiement

Au-delà des 15 jours, sans abonnement payant actif et non expiré :
- Toutes les écritures commerciales et de configuration du bar sont refusées côté permissions ainsi que par les gardes web/API.
- Le propriétaire et les employés peuvent toujours **consulter** les données auxquelles leur rôle permet déjà d'accéder ; rien n'est effacé.
- La page `/bars/<bar_id>/subscription` affiche les dates et explique comment contacter l'administration pour l'abonnement.
- Le Super Admin vérifie **manuellement** le paiement et active l'abonnement via la gestion existante des souscriptions. Quand un abonnement payant valide est actif, l'écriture est rétablie.
- Un paiement non confirmé **ne** débloque **pas** l'établissement.

## Routes

- `GET/POST /register-owner` : inscription publique et activation immédiate, formulaire CSRF et limitation à 3 POST/heure/IP.
- `GET /platform/owner-registrations` : suivi des nouvelles inscriptions, réservé au SUPER_ADMIN ; **pas** d'approbation préalable.
- `GET /bars/<bar_id>/subscription` : échéance de l'essai / situation d'abonnement, limitée au propriétaire.
- `GET /platform/owner-requests` : ancienne URL redirigée vers les inscriptions.

## Sécurité et limites avant mise en production publique

- `User.set_password` hache le mot de passe ; les identifiants ne sont pas communiqués au SUPER_ADMIN.
- Le Super Admin peut suspendre les comptes ou les bars avec le mécanisme de gouvernance existant.
- La vérification des e-mails et numéros de téléphone **n'est pas encore implémentée** : cette version accorde l'essai immédiat aux inscriptions non vérifiées. Pour respecter l'objectif initial de vérification obligatoire, ajouter OTP e-mail/SMS dans le parcours d'inscription, **sans approbation humaine**, avant ouverture en production publique.
- Ajouter contrôle anti-fraude multi-comptes, rate limiter partagé et surveillance des abus.
- Tester intégration MySQL/PythonAnywhere, vrais accès par rôle et autorisations au moment précis de l'expiration.
- Publier les CGU / politique de confidentialité et la procédure de validation des paiements manuels.


## Tests d'intégration MySQL (sans toucher à la production)

Les tests fonctionnels historiques de `tests/test_owner_registration.py` utilisent
volontairement SQLite pour leur rapidité. Pour valider **réellement MySQL** :

1. La branche comporte `tests/test_owner_registration_mysql.py` avec les
   mêmes scénarios métier sur MySQL (inscription, première activation, fin de 15 jours,
   consultation seule et réactivation après paiement).
2. Le workflow `.github/workflows/owner-signup-mysql.yml` prépare automatiquement
   une base MySQL 8 jetable dans GitHub Actions. Aucun secret MySQL PythonAnywhere
   n'est nécessaire.
3. Sur PythonAnywhere, ne tester manuellement MySQL **que** dans une base
   *distincte*, vide, nommée `barpro_test` ou `sandjong$barpro_test`.
   La base MySQL de production (KAPE) n'est jamais un environnement de test.

Pour exécuter sur une base MySQL séparée, dans un environnement virtuel avec
les dépendances installées et depuis le worktree `bar_pro_owner_test` :

```bash
# Saisir l'URL MySQL de test sans l'afficher ni la conserver dans l'historique Bash :
read -r -s -p "URL mysql+pymysql de la base TEST uniquement : " OWNER_SIGNUP_MYSQL_TEST_URI
echo
export OWNER_SIGNUP_MYSQL_TEST_URI
export OWNER_SIGNUP_MYSQL_TEST_CONFIRM=ISOLATED_MYSQL_TEST
python -m pytest -v tests/test_owner_registration_mysql.py
unset OWNER_SIGNUP_MYSQL_TEST_URI OWNER_SIGNUP_MYSQL_TEST_CONFIRM
```

Le test est **désactivé** lorsque `OWNER_SIGNUP_MYSQL_TEST_URI` n'est pas défini,
refuse un nom de base différent de `barpro_test` (ou suffixe autorisé),
et refuse toute base avec des tables préexistantes. Il laisse les données
du test dans cette base dédiée, pour inspection ; il ne supprime ni n'efface
aucune table. **Ne jamais utiliser l'URL de la base de production**.

Un démarrage GitHub Actions peut être bloqué par les contrôles du compte GitHub ;
un statut `startup_failure` ne constitue pas un résultat de test Python.
