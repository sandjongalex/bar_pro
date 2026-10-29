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
