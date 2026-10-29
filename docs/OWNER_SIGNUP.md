# Inscription propriétaire BAR_PRO — première version

## Routes

- `GET/POST /register-owner` : demande publique avec CSRF et limitation à 3 POST/heure/IP.
- `GET /register-owner/sent` : accusé de réception, aucune session créée.
- `GET /platform/owner-requests` : file d'attente réservée au SUPER_ADMIN.
- `POST /platform/owner-requests/<bar_id>/approve` : activation après confirmation manuelle des deux coordonnées.
- `POST /platform/owner-requests/<bar_id>/reject` : rejet motivé.

La page `/login` propose « Créer un compte propriétaire ». L'espace admin est accessible par le menu « Inscriptions propriétaires ».

## Règles et sécurité

1. Le formulaire recueille nom, adresse e-mail, téléphone camerounais, mot de passe et premier bar. Le mot de passe est haché par `User.set_password`, jamais envoyé au Super Admin.
2. La création est transactionnelle. Le compte `OWNER` a `is_active=False` ; les deux états `OwnerGovernance` et `BarGovernance` valent `PENDING`. Aucun accès au bar n'est accordé avant approbation.
3. Le Super Admin vérifie **manuellement** l'e-mail et le téléphone par un canal indépendant avant de cocher les deux cases obligatoires. Il approuve ou rejette. L'opération est auditée. Cette première version **ne possède PAS d'envoi automatique de courriels, d'OTP SMS, ni de notifications au propriétaire**.
4. À l'approbation, le compte et le bar passent à `APPROVED`. Le premier établissement reçoit une souscription `BARPRO_TRIAL_14D` gratuite de 14 jours à partir de cette date. Un autre bar ne reçoit pas de deuxième essai.
5. Une fois l'essai expiré, le bar reste consultable mais les écritures sont refusées tant qu'aucun abonnement payant actif et non expiré ne le couvre. La validation d'un abonnement payant clôt l'essai actif pour respecter l'unicité SQL.
6. Les établissements historiques sans `BARPRO_TRIAL_14D` ne sont pas affectés par la limitation d'essai. Le Super Admin conserve la gouvernance et le traitement des abonnements.

## Limites avant commercialisation publique

- Ajouter un prestataire e-mail transactionnel et un service SMS (OTP à usage unique, expiration, anti-bruteforce, preuves de validation).
- Ajouter les notifications d'approbation/refus, le renvoi des codes et la récupération du mot de passe.
- Publier et versionner les conditions d'utilisation / politique de confidentialité ; effectuer les vérifications légales et anti-abus (captcha, surveillance, throttling partagé multi-worker).
- Exécuter les tests d'intégration MySQL et de navigation avec CSRF, puis valider le parcours réel PythonAnywhere.
- Prévoir une file de traitement des dossiers rejetés et de support des inscriptions.
