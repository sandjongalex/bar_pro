# Changelog

Ce fichier consigne uniquement les changements significatifs vérifiables pour
l’utilisateur, l’exploitation ou la compatibilité. Ne pas y recopier la roadmap,
chaque retouche interne ou une fonctionnalité déjà présente sans preuve de
changement.

Historique Git : vérification du 2026-09-17, `git rev-parse --show-toplevel`
retourne `C:/Users/pc`, hors `bar_pro`. Aucun commit du dépôt parent n’est utilisé
comme historique de Bar Manager Pro.

## Non publié

### Architecture

- Documentation opérationnelle consolidée : ajout ou actualisation des références
  projet `AGENTS.md`, `CONTEXT.md`, `docs/ARCHITECTURE.md`,
  `docs/SPECIFICATION.md`, `docs/ROADMAP.md`, `docs/API.md`,
  `docs/TESTING.md`, `docs/SECURITY.md` et du présent changelog.
  Impact : les contributeurs disposent de références séparées pour architecture,
  API, tests, sécurité et contexte. Action : aucune migration ni changement
  applicatif. Preuve : fichiers présents dans le dépôt de travail ; tests non
  relancés pour ces modifications documentaires.
- Contrat API courant déplacé vers `docs/API.md`; `docs/API_CONTRACT.md` devient
  un renvoi historique et `docs/ROUTES.md` pointe vers le contrat courant.
  Impact : évite deux contrats API contradictoires. Action : utiliser `docs/API.md`
  pour l’API existante. Preuve : inspection des routes Flask avec application de
  test isolée, 57 routes `/api/` relevées lors de la mission documentaire.

### Données et migrations

- `docs/DATA_MODEL.md` régénéré par `scripts/schema_document.py` avec révision
  Alembic terminale détectée depuis `migrations/versions/`, variantes MySQL/SQLite,
  relations FK/ORM et limites d’intégrité. Impact : dictionnaire physique plus
  reproductible. Action : ne pas éditer `DATA_MODEL.md` librement. Preuve :
  `python scripts/schema_document.py`; comparaison exacte `True`;
  `python -m pytest tests/test_schema.py -q` : 9 tests réussis.

## 2026-09-15 — faits rapportés par `docs/PROGRESS.md`

### Ajouts

- Parcours inventaires web avec CSRF : création, sélection de produits, comptage,
  validation, annulation, consultation paginée et impression. Impact : couverture
  HTML des inventaires en plus des services. Action : aucune migration signalée
  par cette entrée. Preuve attribuée : `docs/PROGRESS.md`, section
  « Complément inventaires — 15 septembre 2026 », et `tests/test_inventories.py`.
- API inventaires complétée pour consultation détaillée, pagination, annulation
  et statuts d’erreur documentés. Impact : clients API peuvent gérer le cycle
  d’inventaire testé localement. Action : vérifier les permissions et conflits
  selon `docs/API.md`. Preuve attribuée : `docs/PROGRESS.md` et tests dédiés.

### Corrections

- Validation inventaire renforcée : références, motifs, identifiants, doublons,
  produits hors bar, quantités finies non négatives et rollback des ajustements
  multiples. Impact : réduction des états partiels et des références croisées.
  Action : aucune base utilisateur migrée par cette entrée. Preuve attribuée :
  `docs/PROGRESS.md`, `tests/test_inventories.py`.

### Incompatibilités

- Limite explicitée : validation SQLite/HTTP uniquement ; concurrence MySQL et
  rendu navigateur non validés. Impact : ne pas annoncer une validation production.
  Action : suivre `docs/DEPLOYMENT.md` avant exploitation MySQL. Preuve attribuée :
  `docs/PROGRESS.md`.

## 2026-09-09 — faits rapportés par `docs/PROGRESS.md`

### Ajouts

- Parcours locaux rapportés comme terminés dans le périmètre testé : socle Flask,
  auth JWT/web, tenant, stock, inventaires, fournisseurs/achats, commandes,
  retours commerciaux, paiements manuels et tiroir de caisse. Impact : base
  fonctionnelle testée localement. Action : ne pas extrapoler à toutes les
  fonctions financières ni à la production. Preuve attribuée :
  `docs/PROGRESS.md`, section « Parcours terminés dans le périmètre testé
  localement ».
- Fournisseurs et achats complétés dans le périmètre API testé : CRUD fournisseur,
  achat brouillon, annulation, réception unique, dette, règlement fournisseur
  et contre-écriture. Impact : flux achat/stock/caisse cohérent localement.
  Action : vérifier permissions et dette fournisseur. Preuve attribuée :
  `docs/PROGRESS.md`, `tests/test_workflows.py`.
- Finance rapportée comme livrée localement : paiements en tiroir ou garde,
  remboursements, soldes nets, annulation `CONFIRMED` avec remboursements,
  mouvements manuels, contrepassation, remises et historique paginé API.
  Impact : flux caisse plus complet. Action : paiements externes restent saisis
  manuellement. Preuve attribuée : complément finance de `docs/PROGRESS.md`.

### Corrections

- Commandes : adoption du cycle `DRAFT → CONFIRMED → SERVED → CANCELLED`,
  retrait de stock unique à la confirmation et absence de second retrait au
  service. Impact : évite double déstockage. Action : migration de cycle requise
  pour anciennes valeurs. Preuve : migration `a1b2c3d4e5f6_order_lifecycle.py`,
  `docs/PROGRESS.md`.
- Paiements : ventilation des montants `amount_presented`, `amount_applied` et
  `change_given`, plafond de paiement et cohérence de monnaie rendue. Impact :
  distinction entre montant reçu, affecté et rendu. Action : migration de
  paiement requise pour données historiques. Preuve :
  `b2c3d4e5f6a7_payment_amounts.py`, `docs/PROGRESS.md`.
- Retours commerciaux : persistance des documents/lignes, plafonnement cumulé,
  dispositions `RESTOCK` ou `LOSS` et usage des snapshots historiques. Impact :
  empêche restitution illimitée d’une ligne. Action : aucune migration dédiée
  signalée dans cette entrée. Preuve attribuée : `docs/PROGRESS.md`,
  `tests/test_workflows.py`.
- Permissions et isolation : permissions manquantes ajoutées selon matrice,
  serveur autorisé à vendre sans gérer le stock, dette fournisseur interdite au
  rôle `SERVER`. Impact : séparation plus fine des droits. Action : maintenir
  les tests de tenant et permissions. Preuve attribuée : `docs/PROGRESS.md`,
  `tests/test_workflows.py`.
- Erreurs et validation : refus des nombres non finis, précision excessive,
  uploads non stockés et non-exposition des erreurs SQL aux clients. Impact :
  réponses plus sûres et entrées numériques bornées. Action : conserver les
  validations de service. Preuve attribuée : `docs/PROGRESS.md`.

### Données et migrations

- Révision Alembic atteinte rapportée : `c3d4e5f6a7b8`, 35 tables applicatives.
  Impact : schéma courant identifié. Action : appliquer Alembic, pas `create_all`.
  Preuve attribuée : `docs/PROGRESS.md`, `migrations/versions/`.
- `DATA_MODEL.md` décrit le schéma réel et est comparé aux modèles par test ;
  le design historique est séparé. Impact : dictionnaire généré contrôlable.
  Action : régénérer par `scripts/schema_document.py` après changement de modèle.
  Preuve attribuée : `docs/PROGRESS.md`, `tests/test_schema.py`.
- Intégrité renforcée : FK tenant pour journaux financiers, cohérence
  commande/produit sur lignes de retour, `BINARY/VARBINARY` pour clés binaires
  MySQL et `units_per_case` unsigned. Impact : contraintes physiques plus
  strictes et meilleure compatibilité MySQL compilée. Action : vérifier sur
  MySQL réel avant production. Preuve :
  `c3d4e5f6a7b8_integrity.py`, `docs/PROGRESS.md`.

### Architecture

- Déploiement PythonAnywhere préparé par documentation et modèles d’environnement :
  WSGI, MySQL/PyMySQL, pool, clés ES256 externes, cookies HTTPS et Redis partagé.
  Impact : guide d’exploitation disponible sans secret fourni. Action : configurer
  secrets hors dépôt, migrer une base dédiée et vérifier Redis/MySQL réels.
  Preuve attribuée : `docs/PROGRESS.md`, `docs/DEPLOYMENT.md`,
  `deploy/production.env.example`, `deploy/pythonanywhere_wsgi.py`.

### Incompatibilités

- Les downgrades destructifs sont refusés par les migrations consultées.
  Impact : retour arrière automatique non disponible pour ces évolutions de
  schéma. Action : sauvegarde et essai sur copie avant production. Preuve :
  migrations `6b1599cad0b4`, `7c2a1b8d9e10`, `8d3e2f4a5b6c`,
  `9e4f5a6b7c8d`, `a1b2c3d4e5f6`, `b2c3d4e5f6a7`, `c3d4e5f6a7b8`.
- Validation MySQL réelle non attestée : seule une compilation SQL MySQL hors
  connexion et des tests SQLite/HTTP sont rapportés. Impact : ne pas déclarer la
  production validée. Action : exécuter les validations de `docs/DEPLOYMENT.md`.
  Preuve attribuée : `docs/PROGRESS.md`.

## Migrations attestées sans date de release

### Données et migrations

- `6b1599cad0b4_initial_schema.py` — schéma initial. Impact : création des tables
  de base. Action : migration initiale requise sur base neuve. Preuve : fichier
  de migration.
- `7c2a1b8d9e10_bar_settings.py` — ajoute paramètres de bar (`address`, `phone`,
  `logo_key`, `stock_alert_threshold`, `credit_sales_enabled`). Impact :
  enrichit la configuration d’établissement. Action : migration Alembic. Preuve :
  fichier de migration.
- `8d3e2f4a5b6c_catalog_product_fields.py` — ajoute seuil stock produit,
  `units_per_case` et `image_key`. Impact : prépare catalogue enrichi et upload,
  même si le stockage image opérationnel est refusé actuellement. Action :
  migration Alembic. Preuve : fichier de migration et `catalog_services.image_key`.
- `9e4f5a6b7c8d_stock_movement_type.py` — ajoute `movement_type` aux mouvements
  de stock. Impact : typage explicite des mouvements historiques. Action :
  backfill par default serveur `ADJUSTMENT` dans la migration. Preuve : fichier
  de migration.
- `a1b2c3d4e5f6_order_lifecycle.py` — convertit les états historiques
  `POSTED/CLOSED`, ajoute `payment_status`, `notes` et note de ligne. Impact :
  nouveau cycle commande/paiement. Action : vérifier les anciennes données.
  Preuve : fichier de migration.
- `b2c3d4e5f6a7_payment_amounts.py` — ajoute la ventilation des paiements.
  Impact : distingue reçu, affecté et rendu. Action : migration Alembic. Preuve :
  fichier de migration.
- `c3d4e5f6a7b8_integrity.py` — renforce types MySQL, backfills paiements/statuts,
  CHECK et FK composites. Impact : contraintes physiques plus strictes. Action :
  sauvegarde et validation sur copie avant production. Preuve : fichier de
  migration.
