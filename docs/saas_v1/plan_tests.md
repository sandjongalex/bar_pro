# BAR_PRO SaaS V1 — plan des tests de référence

## Sources et commandes
- Configuration `pytest.ini` ; `tests/conftest.py` ; fixtures et Alembic depuis `tests/test_workflows.py`. SQLite temporaire ; certains tests activent explicitement CSRF.
- Commandes locales sûres : `python -m pip install -r requirements.txt`, `python -m pytest -q`, `node --test tests/*.test.cjs`, `python -m compileall -q app tests`, `python scripts/schema_document.py` **uniquement si son résultat n'écrase pas des fichiers suivis sans revue**.
- CI de référence ajouté : `.github/workflows/saas-v1-audit-baseline.yml` ; seule cible GitHub Actions isolée, sans secret de production ni migration de production.
- Nouveaux tests de référence dans `tests/test_saas_v1_baseline.py` : structure tenant, rôles observés, refus du propriétaire étranger et dette manuelle sans vente/stock/caisse.

## Résultats constatés (main avant branche SaaS)
| Source/Date | Exécution | Résultat |
| --- | --- | --- |
| Actions run 39318537195 — commit `5c3d8be9` — 19/10/2026 | Job `all-python` : `python -m pytest -q` | **335 réussis, 1 échec**, suite rouge |
| Actions run 39318537195 | Job `focused` : sous-ensemble facturation/caisse/finances | **Succès** |
| Actions run 39318537022 | Tests achats, fournisseurs, SQL migration et schéma | **Succès** |
| Actions run 39318450395 — commit précédent `3bb0bd18` | JS `node --test tests/*.test.cjs` + tests Python ciblés | **Succès ciblé** ; suite Python complète échouée |

Échec exact du run principal : `tests/test_cashier_suborder_edit.py::test_cashier_can_edit_round_before_and_after_delivery_and_server_sees_changes`. Le test appelle `_csrf(page)` après GET workspace, mais reçoit un `302 FOUND` vers `/bars/1/cashier-session` : `AssertionError: csrf token not found`. **Ne pas le masquer ni déclarer la suite verte**. Compte rendu source : https://github.com/sandjongalex/bar_pro/actions/runs/39318537195

## Grille de test de la phase suivante
1. **Authentification** : sessions web, CSRF, JWT bar claim, refresh/revocation, session invalidée à changement de mot de passe ; double vérification email+téléphone.
2. **Rôles** : chacune des cinq identités ; consultation/création/modification/validation/annulation ; cas explicites de refus ; compte désactivé et service non ouvert.
3. **Tenant/IDOR** : propriétaire A vers bar B, employé A vers bar B du même propriétaire, ID secondaire objet étranger, POST/GET/API/export/print/AJAX, 404 sans fuite.
4. **Essai** : J0, jour 13, 14 jours exactement, absence de paiement, lecture seule immédiate, statut bar suspendu, paiement valide, changement de fuseau, concurrence.
5. **Délégation** : BAR_ADMIN sans droit, avec plafond, plafond global plus faible, opérations fractionnées, dépassement par race, audit et notifications.
6. **Métier** : commandes et sous-commandes avant/après livraison, annulation et rollback stock, ajustements d'inventaire, caisse, remboursements, dettes indépendantes et impressions.
7. **SaaS paiements et récupération** : idempotence, renouvellement, justificatifs, périodes de sécurité, révocation, protection des documents.
8. **Technique** : SQLite en CI + **MySQL de test séparé**, SQL strict et contraintes, migration/backfill, JS tests, PWA sans cache privé, responsive/tablette et performance.

## Traçabilité et statut à mettre à jour après CI
La nouvelle branche déclenche `saas-v1-audit-baseline.yml`. Reporter le lien du run, le SHA, le nombre de tests passés/échoués et le statut réel dans la PR après exécution. Cette phase n'autorise ni correction de comportement, ni migration prod. **Si le run n'est pas terminé, laisser le statut « EN COURS » ; ne jamais inférer un succès.**
