# BAR_PRO SaaS V1 — architecture cible (conception, aucun code métier livré)

## Principe
**Faire évoluer le monolithe Flask existant**, conserver les URLs, noms de champs, API, transactions et affichages : pas de microservices, pas de réécriture React, pas de changement de caisse en phase d'audit.

```text
Navigateur/PWA Jinja + JS -----> Flask (cookie/CSRF)
Client API autorisé ------------> Flask /api/v1 (JWT ES256)
                                      |
                        Autorisation tenant + rôle
                                      |
                       Autorisation SaaS + délégations
                                      |
             Services métier existants (POS, stock, dettes)
                                      |
                          SQLAlchemy / MySQL
                           + audit immuable
```

## Domaines et responsabilités futurs
1. **Compte et vérification** : OWNER vérifié par email et téléphone ; états explicitement persistés, preuves horodatées, anti-abus ; jamais de numéro validé par simple saisie.
2. **Organisation** : `Bar.owner_id` et `StaffAssignment` réutilisés. OWNER peut avoir plusieurs bars ; employé ne peut connaître ni voir les autres bars du même OWNER.
3. **Autorisation** : `permissions.evaluate/require` source de vérité serveur. Conserver les cinq rôles effectifs, les gardes de service, l'état de bar ; ajouter un calcul d'accès SaaS indépendant : `TRIAL`, `ACTIVE`, `READ_ONLY`, `SUSPENDED`. Statuts conceptuels, **pas colonnes créées dans cette phase**.
4. **Essai et abonnement** : essai initial de 14 jours à partir de l'activation vérifiée selon la future règle formalisée ; calcul UTC serveur et fuseau pour l'affichage. Après son expiration et sans droit payé valide : lecture seule immédiate, zéro grâce. Reprise des écritures seulement après confirmation fiable du paiement/renouvellement. État financier dans une transaction, non piloté par un badge UI.
5. **Permissions financières déléguées** : plafonds OWNER par administrateur et par opération, jamais au-dessus des limites de sécurité globales. Décision atomique et auditable, contre dépassement et requêtes concurrentes. Par défaut refus pour les capacités non expressément déléguées.
6. **Récupération de compte** : justificatifs, période de sécurité, notifications à tous les canaux déjà vérifiés, preuve d'approbation, révocation des sessions/tokens, audit et vérification humaine pour cas sensibles. Aucune pièce brute dans les journaux.
7. **Plateforme** : SUPER_ADMIN administre plans, assistance, suspensions et audits avec identité plateforme ; accès opérationnel à un bar sous procédure exceptionnelle tracée, sans casser l'accès actuel avant décision et migration.

## Invariants de compatibilité
- `bar_id` obligatoire dans toute lecture et mutation métier tenant. Scoper la ressource racine **et** les références secondaires.
- Refuser la mutation en `READ_ONLY` côté serveur avant la première écriture, y compris routes REST, formulaires et jobs ; ne pas bloquer le GET, la consultation historique, le téléchargement de reçus ou la déconnexion.
- Interdire paiement, création de dette, livraison, stock, annulation, suppression et toute autre modification opérationnelle pendant lecture seule ; journal d'audit et résolution de paiement plateforme sont des exceptions contrôlées.
- Un formulaire reste CSRF web, l'API conserve JWT ; changement de mode JSON/AJAX ne doit pas changer l'authentification.
- Une facture existante et sous-commande restent stables ; fusion seulement au moment expressément demandé dans les parcours actuels ; stock non décompté deux fois.
- Dettes clients manuelles indépendantes des commandes, stocks et caisse ; distinction persistante `ADJUSTMENT` vs `PAYMENT` de vente ; pas de compensation implicite.
- Opérations financières atomiques/idempotentes et traçables par acteur, bar, plafond, demande et justification.

## Points d'intégration ultérieurs
- `app/permissions.py` + service SaaS futur pour la décision unique et testable ; `app/__init__.py` pour garde web de sécurité additionnel **non suffisant seul**.
- `app/auth.py`, `app/bar_services.py`, `app/bars.py` pour inscription, preuves vérifiées, récupération.
- `app/models.py`, nouveaux modèles **isolés** éventuels + migration Alembic additive ; `app/subscription_services.py`, `app/subscriptions.py` pour essais, plans, renouvellements et transitions d'état.
- `app/cash_services.py`, `app/payment_services.py`, `app/customer_services.py`, `app/expense_services.py`, `app/purchase_services.py` : garde transactionnel pour plafonds et lecture seule.
- `app/templates/`, `app/static/` : adaptation progressive des écrans sans suppression de fonctions.

## Hors périmètre de ce commit
Pas d'inscription publique, OTP réel, API opérateur SMS, facturation automatique, paiement prestataire, lecture seule effective, changement RBAC, migration appliquée ou traitement réel de justificatifs. Ces ajouts requièrent le cahier des charges signé, les tests et une phase dédiée.
