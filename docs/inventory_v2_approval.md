# BAR_PRO — Inventaires V2 : étape 4 — Circuit d'approbation

**Branche :** `feature/inventory-v2-approval`.

## Parcours

1. La caissière affectée à une session active enregistre toutes les quantités.
2. Chaque écart doit comporter une justification enregistrée sur le produit.
3. Elle transmet l'inventaire via **Soumettre au responsable**.
4. Les quantités sont figées. L'administrateur peut demander un recomptage,
   ou approuver uniquement dans les trois plafonds du propriétaire.
5. Le propriétaire peut approuver les dépassements et doit approuver
   obligatoirement le premier inventaire validé du bar.
6. L'approbation relit le journal en verrouillant le bar, puis crée les
   mouvements `INVENTORY_ADJUSTMENT` dans la même transaction. Le stock
   n'est jamais modifié à la soumission ou à la demande de recomptage.

Les différences sont évaluées en quantité absolue et en **coût de valorisation
du stock** (prix de revient, pas prix de vente). La valeur globale correspond
à la somme des valeurs absolues par produit, sans compensation entre surplus
et manquants. Les seuils sont recontrôlés à chaque approbation afin que
les ventes et changements de coût depuis la soumission soient pris en compte.

Les plafonds non configurés valent zéro ; l'administrateur ne peut approuver
que les inventaires sans écart (hors premier inventaire qui revient au propriétaire).
Des limites de sécurité serveur bornent les seuils paramétrables ; il ne s'agit
pas de montants de vente autorisés.

## Sécurité

- Permission distincte `inventory.submit` pour caissière, admin et propriétaire.
- Permission `inventory.approve` pour administrateur de bar et propriétaire.
- Permission `inventory.policy` réservée au propriétaire.
- Jamais d'auto-approbation par la caissière.
- Révisions d'inventaire et contrôles d'accès isolés par bar et session.
- Les inventaires V2 anciens sans curseurs sont refusés plutôt qu'approuvés
  automatiquement.
- Un journal de stock incohérent, un produit non compté, un mouvement d'inventaire
  concurrent ou une projection négative bloque l'approbation et exige un recomptage.
- Les raisons d'approbation/recomptage sont obligatoires et auditées.
- Une double approbation est rejetée, les ajustements sont effectués une fois.
- Les encaissements, dettes, paiements, remboursements et clôtures de caisse
  restent totalement indépendants ; l'ancien résumé de caisse V1 n'est pas
  présenté comme un rapprochement financier V2.
- Les migrations sont additives et l'historique V1 reste accessible.

## Interfaces

- `GET/POST /bars/<bar_id>/inventories/v2-policy` (propriétaire).
- `POST /bars/<bar_id>/inventories/<id>/v2-submit`.
- `POST /bars/<bar_id>/inventories/<id>/v2-decision`.
- `GET /api/v1/bars/<bar_id>/inventories/<id>/approval`.
- `POST /api/v1/bars/<bar_id>/inventories/<id>/submit`.
- `POST /api/v1/bars/<bar_id>/inventories/<id>/decision`.
- `GET/PUT /api/v1/bars/<bar_id>/inventories/approval-policy`.

Les formulaires web sont protégés par CSRF et la PWA n'est pas modifiée.
Les règles d'autorisation sont vérifiées dans les services, pas seulement dans
les boutons visibles.

## Vérifications

```bash
python -m pytest tests/test_inventory_v2_approval.py tests/test_inventory_v2_reconciliation.py tests/test_inventory_v2_counting.py tests/test_inventory_v2_foundation.py tests/test_inventories.py tests/test_schema.py -q
python -m pytest -q
python -m flask --app wsgi:application db check
```

Effectuer en plus des tests d'interleaving réel avec **deux connexions MySQL**
avant d'utiliser les approbations en production, puis une sauvegarde de la base
avant la migration `f4a5b6c7d8e9`.
