# Bar Manager Pro — Règles métier

> État au 9 septembre 2026 : ce document conserve des exigences et une architecture cible.
> Il ne constitue pas une liste de fonctionnalités disponibles. Les routes réellement
> enregistrées sont dans [ROUTES.md](ROUTES.md), le schéma installé dans
> [DATA_MODEL.md](DATA_MODEL.md), les tests exécutés et les écarts dans [PROGRESS.md](PROGRESS.md).


Version : 0.4 — 2026-09-09. Statut : autorisation et isolation SRC-002 conservées ; invariants SRC-003 et contrat API SRC-004 définis ; workflows métier globaux encore incomplets.

## 1. Sources et autorité

SRC-002 (PROMPT 02) impose les cinq rôles, l'isolation par bar, la suspension, l'audit et les conventions financières/temporelles. Il demande de définir une matrice sans détailler chaque droit : la matrice ci-dessous est la politique initiale décidée dans le cadre de cette demande, et non une citation d'un cahier des charges absent. Toute extension de privilège devra modifier explicitement cette référence.

Terminologie : [SPECIFICATIONS.md](SPECIFICATIONS.md). Application technique : [ARCHITECTURE.md](ARCHITECTURE.md). Questions restantes : [PROGRESS.md](PROGRESS.md). Les autorisations n'impliquent pas que les workflows soient déjà définis ou développés.

## 2. États métier — REQ-006

STATE-001 : `ACTIVE`, état structurel d'un bar permettant les écritures autorisées.

STATE-002 : `SUSPENDED`, état imposé par SRC-002 ; lectures et blocage selon TEN-005.

Transitions retenues : ACTIVE → SUSPENDED par `bars.suspend` ; SUSPENDED → ACTIVE par `bars.reactivate`, toutes deux réservées au super-admin et auditées. Répéter la même transition est sans effet métier supplémentaire, avec journal de tentative. État initial retenu : ACTIVE à la création. Aucun automatisme d'abonnement ne change ce statut avant définition de sa politique. Aucun état de suppression n'est introduit.

SRC-003 fixe désormais des cycles structurels initiaux pour commandes, achats, retours, inventaires, remises, caisse et abonnements dans DATA_MODEL.md §2 et §3. Les règles d'accès aux transitions et certains critères financiers restent OPEN_BUSINESS (AMB-003). Les paiements/remboursements sont des écritures validées immuables, pas des tentatives en attente. Les statuts documentaires ne sont pas des états métier.

## 3. Finances — REQ-007

FIN-001 : montants en DECIMAL et calculs exacts ; représentation DEC-010. XAF est la devise par défaut du bar (SRC-002). La devise figure dans les résultats financiers ; aucun calcul entre devises ni conversion implicite. La devise ne change pas après la première écriture financière tant qu'aucun workflow de conversion n'est défini.

Restent OPEN_BUSINESS : arrondis (stockage à quatre décimales ne signifie pas affichage ou encaissement à quatre décimales), taxes, remises, prix, paiements partiels, remboursements, crédit, rapprochement et clôture. Les montants dépassant la précision de stockage sont refusés plutôt qu'arrondis silencieusement. Les règles d'arrondi doivent être fixées avant un service financier opérationnel.

## 4. Stock — REQ-008

SRC-003 impose l'historique de stock immuable et une précision suffisante. Les choix initiaux MODEL-003 à MODEL-005 fixent unité canonique par produit, mouvement complet à validation, corrections additives et contrôle de version d'inventaire ; voir DATA_MODEL.md. Restent ouverts (AMB-005) : méthode de valorisation, autorisation du négatif, conversions éventuelles et règles de perte. Une permission inventory.adjust n'annule pas ces invariants. Les opérations composées conservent tenant et transaction.

## 5. Multi-tenant — REQ-009

| ID | Règle normative |
| --- | --- |
| TEN-001 | Un tenant est exactement un bar. Toute donnée commerciale appartient à un seul bar ; la racine Bar porte son propre identifiant, ses descendants portent bar_id. Comptes globaux, sessions et registre de politique relèvent de l'identité. Plan est le catalogue d'offres de la plateforme, explicitement global ; Subscription et SubscriptionPayment restent propres au bar. AuditLog autorise PLATFORM uniquement pour événements globaux, BAR avec bar_id obligatoire pour toute intervention de bar. Aucun partage implicite de produits, clients ou fournisseurs. |
| TEN-002 | Un propriétaire accède exclusivement aux bars dont il est propriétaire. Choix structurel : un propriétaire peut posséder plusieurs bars ; chaque bar possède un unique propriétaire. Aucun partage entre propriétaires implicite. |
| TEN-003 | Un employé (administrateur, caissier, serveur/serveuse) accède exclusivement à son bar d'affiliation active. Une identité employé a au plus une affiliation active. Une affiliation inactive ou absente ne donne aucun accès métier. |
| TEN-004 | Tout accès à un identifiant vérifie aussi le bar, y compris les parents et références fournis dans un payload. Listes, recherches, agrégats, exports et traitements internes appliquent la même portée. Aucun déplacement d'objet entre bars via mise à jour de bar_id. |
| TEN-005 | SUSPENDED interdit toute nouvelle écriture métier à tous les rôles, super-admin compris. Le propriétaire conserve les lectures de la matrice ; choix conservateur : les employés n'ont plus accès aux données métier. Le super-admin conserve la lecture auditée. Seuls bars.suspend, bars.reactivate et les lectures de contrôle restent possibles dans la portée du bar ; aucune mutation d'abonnement, de paramètres ou de personnel ne contourne le gel. Sessions personnelles et journal de sécurité restent possibles hors écritures métier. |
| TEN-006 | Le super-admin intervient explicitement sur un bar, sans se faire passer pour son propriétaire. Chaque intervention, lecture comprise, est auditée selon AUD-001. Sa portée globale n'est pas un contournement du gel ou des invariants métier. |
| TEN-007 | Changement d'URL, identifiant deviné, cookie ou bar_id soumis ne modifient jamais les droits. Un objet hors portée et un objet inexistant donnent la même réponse de non-disponibilité ; aucun nom, total ou détail de l'autre bar ne fuit. |

## 6. Contraintes de réalisation

REQ-013 : cette étape est documentaire ; aucun module métier n'est développé. REQ-014 : aucun secret dans le dépôt ; les valeurs externes restent EXTERNAL_PREREQUISITE. REQ-015 : terminologie et identifiants permanents, politique unique dans ce fichier.

## 7. Rôles et permissions

### Rôles canoniques

| ID | Code stable | Nom | Portée |
| --- | --- | --- | --- |
| ROLE-001 | SUPER_ADMIN | Super-administrateur global | Plateforme ; bar explicitement ciblé et audit |
| ROLE-002 | OWNER | Propriétaire | Un ou plusieurs bars possédés |
| ROLE-003 | BAR_ADMIN | Administrateur | Un bar d'affiliation active |
| ROLE-004 | CASHIER | Caissier | Un bar d'affiliation active |
| ROLE-005 | SERVER | Serveur/serveuse | Un bar d'affiliation active ; même rôle quel que soit le genre |

AUTH-001 : refus par défaut. Chaque opération correspond à une action connue de la matrice. Pas de wildcard super-admin, de permissions personnalisables ou de rôle « supérieur » implicite. Tous les droits restent soumis à TEN et aux invariants métier.

AUTH-002 : les catégories globales SUPER_ADMIN, OWNER et EMPLOYEE sont exclusives dans cette architecture initiale ; EMPLOYEE porte exactement un rôle local BAR_ADMIN, CASHIER ou SERVER dans son affiliation active. Un compte ne cumule pas automatiquement propriétaire et employé. Un propriétaire sans bar n'accède à aucune donnée métier. Un employé sans affiliation active non plus. La création initiale d'un super-admin relève d'une procédure d'exploitation séparée ; aucun endpoint public de promotion globale.

### Matrice permission × rôle

Légende : **G** = plateforme, **B** = bar accessible selon TEN, **S** = compte propre, **—** = interdit. Pour SUPER_ADMIN, B implique un ciblage explicite et AUD-001. Classes : **R** lecture, **W** écriture métier, **C** contrôle plateforme, **I** identité personnelle. Une cellule B ne suffit jamais à autoriser un bar étranger, une écriture sur bar suspendu ou un invariant métier invalide.

| ID | Action stable | Classe | SUPER_ADMIN | OWNER | BAR_ADMIN | CASHIER | SERVER |
| --- | --- | --- | --- | --- | --- | --- | --- |
| PERM-001 | auth.self.read | I | S | S | S | S | S |
| PERM-002 | auth.self.update_credentials | I | S | S | S | S | S |
| PERM-003 | bars.platform.list | C | G | — | — | — | — |
| PERM-004 | bars.create | C | G | — | — | — | — |
| PERM-005 | bars.read | R | B | B | B | B | B |
| PERM-006 | bars.update_settings | W | B | B | — | — | — |
| PERM-007 | bars.suspend | C | B | — | — | — | — |
| PERM-008 | bars.reactivate | C | B | — | — | — | — |
| PERM-009 | staff.read | R | B | B | B | — | — |
| PERM-010 | staff.manage | W | B | B | — | — | — |
| PERM-011 | catalog.read | R | B | B | B | B | B |
| PERM-012 | catalog.manage | W | B | B | B | — | — |
| PERM-013 | inventory.read | R | B | B | B | — | — |
| PERM-014 | inventory.adjust | W | B | B | B | — | — |
| PERM-015 | suppliers.read | R | B | B | B | — | — |
| PERM-016 | suppliers.manage | W | B | B | B | — | — |
| PERM-017 | purchases.read | R | B | B | B | — | — |
| PERM-018 | purchases.manage | W | B | B | B | — | — |
| PERM-019 | orders.read | R | B | B | B | B | B |
| PERM-020 | orders.create | W | B | B | B | B | B |
| PERM-021 | orders.edit | W | B | B | B | B | B |
| PERM-022 | payments.read | R | B | B | B | B | — |
| PERM-023 | payments.record | W | B | B | B | B | — |
| PERM-024 | cash.read | R | B | B | B | B | — |
| PERM-025 | cash.operate | W | B | B | B | B | — |
| PERM-026 | expenses.read | R | B | B | B | — | — |
| PERM-027 | expenses.record | W | B | B | B | — | — |
| PERM-028 | reports.read | R | B | B | B | — | — |
| PERM-029 | reports.export | R | B | B | B | — | — |
| PERM-030 | subscriptions.read | R | B | B | — | — | — |
| PERM-031 | subscriptions.manage | W | B | — | — | — | — |
| PERM-032 | audit.read | R | B | B | — | — | — |

AUTH-003 : `manage` n'est pas un joker. catalog.manage et suppliers.manage couvrent création, modification descriptive et désactivation ; purchases.manage couvre création/modification avant effets définitifs ; staff.manage couvre création/désactivation d'affiliation et choix parmi les trois rôles employés. Aucun de ces droits n'autorise une suppression physique, un changement de propriétaire ou une promotion globale. bars.create exige un propriétaire existant de catégorie OWNER et une timezone IANA fournie. Le provisionnement du compte propriétaire relève de l'exploitation tant que son workflow n'est pas spécifié. bars.platform.list retourne seulement les métadonnées de supervision (identifiant, propriétaire, statut), pas des données commerciales agrégées.

AUTH-004 : orders.edit ne permet ni annulation financière ni modification d'une opération finalisée ; DRAFT est l'état éditable retenu par SRC-003. Choix initial : SERVER peut lire et modifier les commandes éditables de son bar, sans restriction implicite « uniquement les siennes ». cash.operate couvre ouverture, mouvements et clôture selon les invariants de caisse documentés ; aucune réécriture d'historique. subscriptions.manage couvre les données d'abonnement, jamais une réactivation cachée. Toute opération non comprise explicitement est interdite jusqu'à ajout d'une permission et de ses règles. La création des structures Return/Refund/ApiToken ne crée pas de droits applicatifs supplémentaires.

AUTH-005 : l'API n'a aucun privilège propre. Toutes les interfaces, scripts et services appliquent la même décision centralisée. Authentification initiale et déconnexion sont des opérations du protocole d'identité ; elles n'accordent aucun accès métier.

FIN-010 : une commande CONFIRMED non encaissée peut être CANCELLED avec motif ; sa restauration de stock est additive et unique. Une commande encaissée ne peut pas être annulée directement : créer et valider d'abord le Refund, dans la même transaction que la décision d'annulation si les règles de remboursement l'autorisent ; ensuite seulement restaurer le stock et passer CANCELLED. Après SERVED, un retour RESTOCK crée une entrée stock ; un retour LOSS ne crée aucun second retrait car la vente a déjà sorti les unités, mais un audit explicite associe la perte au retour. Toute opération est atomique et auditée.

AUD-001 : interventions super-admin sur bar auditées (lectures, écritures et refus connus), avec acteur réel, bar, action, cible, date UTC, résultat et motif. La liste globale de supervision fait l'objet d'un événement plateforme ; toute ouverture d'un bar produit un événement ciblé. Les journaux sont immuables : aucune permission de modification ou suppression. Audit de mutation atomique avec celle-ci ; audit de lecture avant restitution. Si l'audit requis échoue, l'intervention échoue. Un journal d'audit peut être écrit sur un bar suspendu en tant que contrôle de sécurité.

### Revue systématique des cinq rôles

| Rôle | Autorisé dans sa portée sur bar actif | Refus significatifs vérifiés par raisonnement |
| --- | --- | --- |
| SUPER_ADMIN | Supervision, création, ciblage des bars et opérations B auditées | Pas de wildcard, pas d'écriture métier sur suspendu, pas de lecture non auditée |
| OWNER | Gestion de ses bars, personnel, exploitation et lecture abonnement/audit | Autre propriétaire, auto-réactivation, gestion abonnement plateforme, promotion globale |
| BAR_ADMIN | Catalogue, stock, fournisseurs, achats, commandes, paiements, caisse, dépenses, rapports | Personnel, paramètres du bar, abonnement, audit et autre bar |
| CASHIER | Commandes, encaissements et caisse ; catalogue de vente | Dépenses, stock, personnel, rapports et autre bar |
| SERVER | Catalogue et commandes du bar | Encaissement, caisse, finances, personnel et autre bar |

### Scénarios d'acceptation d'isolation et d'autorisation

Jeu de données documentaire : propriétaires P1 et P2, bars B1 appartenant à P1 et B2 à P2, tous deux ACTIVE ; A1/C1/S1 employés de B1 et A2/C2/S2 de B2 ; objet O1 de B1 et O2 de B2 ; super-admin SA. Ces symboles ne sont ni comptes réels ni secrets. Revue mentale effectuée ; tests exécutables non réalisés.

| ID | Action | Résultat observable attendu |
| --- | --- | --- |
| AC-AUTH-001 | P1 lit O1 puis O2 ; P2 lit O2 puis O1 | Objets propres visibles ; objets étrangers 404 sans détail ni effet |
| AC-AUTH-002 | P1 remplace B1 par B2 dans URL/payload ; P2 fait l'inverse | 404 ; aucune donnée du bar étranger |
| AC-AUTH-003 | P1 liste/recherche/exporte ; P2 fait de même | Lignes et totaux de leurs bars seulement, avant pagination |
| AC-AUTH-004 | A1, C1, S1 accèdent à O2 ; A2, C2, S2 à O1 | 404 pour les six accès ; mêmes réponses HTML/API |
| AC-AUTH-005 | Création dans B1 avec référence parent/article/fournisseur de B2 | 404 générique avant mutation ; contrainte composite refuse aussi une association invalide en base |
| AC-AUTH-006 | S1 forge payments.record malgré bouton masqué | 403 ; aucune ligne ni effet financier/stock ; C1 autorisé par politique pour B1 sous invariants |
| AC-AUTH-007 | A1 tente staff.manage ou change son rôle en SUPER_ADMIN | 403 ; identité et affiliation inchangées |
| AC-AUTH-008 | B1 devient SUSPENDED ; P1 lit puis tente chaque action W | Lectures permises ; chaque écriture 403 BAR_SUSPENDED, aucun effet ; B2 inchangé |
| AC-AUTH-009 | A1/C1/S1 accèdent à B1 suspendu ; SA y écrit | Employés refusés 403 ; SA lecture auditée possible, écriture W refusée |
| AC-AUTH-010 | SA intervient sur B1 puis B2 ; panne du stockage audit | Événement ciblé pour chaque succès ; panne : aucune restitution de lecture ni commit métier |
| AC-AUTH-011 | SA réactive B1 ; P1 essaie la même opération | SA réussit avec audit ; P1 reçoit 403 |
| AC-AUTH-012 | Affiliation de C1 révoquée entre deux requêtes ou avant commit | Nouvelle opération refusée ; vérification après verrou avant effet ; session ne fige pas les droits |
| AC-AUTH-013 | Commande et suspension concurrentes sur B1 | Ordre sérialisé : commande commit avant suspension, ou refus après ; aucun commit métier après suspension effective |
| AC-AUTH-014 | Action inconnue, bar absent du contexte, rôle inconnu, client MySQL direct | Refus applicatif sans effet ; accès réseau MySQL client impossible dans le déploiement cible |
| AC-AUTH-015 | Anonyme appelle une lecture protégée ; P1 demande un objet inexistant | API 401 pour anonyme ; objet absent 404 identique à un objet hors portée |
| AC-AUTH-016 | Fixture alternative : P1 possède B1 et B2 ; employé toujours affilié à B1 | P1 accède aux deux avec contexte séparé ; employé limité à B1 ; pas de mutation multi-bar implicite |

Les réponses HTTP sont précisées dans API.md. Chaque action refusée doit être vérifiée sans mutation persistée ; l'audit de sécurité attendu constitue la seule exception de contrôle.

## 8. Invariants relationnels issus de SRC-003

Source : PROMPT 03, définition des entités, snapshots, historiques, séparation paiements/remboursements et caisse ouverte unique. Les règles ci-dessous distinguent obligations explicites et décisions initiales nécessaires à un schéma cohérent. Dictionnaire propriétaire des détails de colonnes : [DATA_MODEL.md](DATA_MODEL.md).

| ID | Règle | Origine |
| --- | --- | --- |
| STATE-003 | Purchase, Inventory, OrderReturn, CashHandover : DRAFT → POSTED ou CANCELLED, puis terminal. Annuler conserve les lignes. | Décision de conception SRC-003 |
| STATE-004 | Order : DRAFT → CONFIRMED/CANCELLED ; CONFIRMED → SERVED. Seul DRAFT permet l'édition libre ; toute modification confirmée passe par une correction différentielle de stock. Le statut de paiement distinct est UNPAID, PARTIAL ou PAID. | PROMPT 14, remplace la décision SRC-003 |
| STATE-005 | CashSession : OPEN → CLOSED uniquement. Pas de réouverture ni mouvement après fermeture. | Protection de caisse demandée et décision conservatrice |
| STATE-006 | Subscription : PENDING → ACTIVE/CANCELLED ; ACTIVE → EXPIRED/CANCELLED. Aucun changement automatique du statut Bar sans politique ultérieure. | Décision de conception SRC-003 |
| FIN-002 | Les montants validés et écritures financières ne sont jamais supprimés physiquement. Corrections additives reliées à l'origine ; une contre-écriture exacte au plus par original, sans chaîne de contre-écritures. | Exigence SRC-003 et mécanisme retenu |
| FIN-003 | OrderLine conserve les snapshots vente/coût/unité/nom et les allocations de remise/taxe. Les changements de catalogue ne modifient pas les marges historiques. Totaux parents validés = somme des snapshots de lignes. | Exigence SRC-003 |
| FIN-004 | Payment et Refund sont distincts. Remboursements cumulés <= paiement d'origine ; retours cumulés <= quantité et valeur vendues ; remboursement lié à un retour <= valeur de ce retour. Contrôles transactionnels sous verrou, jamais un CHECK inter-table fictif. | Décision initiale MODEL-006 |
| FIN-005 | Les soldes client/fournisseur/garde sont calculés, pas maintenus dans un champ générique divergent. Net vente et net encaissement distinguent retour commercial et restitution réelle d'argent. | Décision MODEL-007, MODEL-008 |
| FIN-006 | Les espèces détenues par un employé sont distinctes du tiroir. Paiement en garde → StaffCashLedger ; remise confirmée → débit de garde et crédit de tiroir atomiques, sans nouveau Payment. La garde n'est ni ajoutée au fond de caisse ni effacée à la clôture. | Structures StaffCashLedger/CashHandover de SRC-003, MODEL-008/012 |
| FIN-007 | Un bar ne possède jamais deux CashSession OPEN. UNIQUE de la colonne virtuelle open_bar_id et verrou transactionnel, y compris en cas de deux requêtes concurrentes. | Exigence SRC-003, DEC-017 |
| FIN-008 | La clôture capture fond initial + mouvements du tiroir, montant compté et écart. Les corrections ultérieures sont dans une session ouverte, sans modifier la clôture passée. | Décision de conservation MODEL-010/012 |
| FIN-009 | Les règlements d'abonnement concernent la plateforme et n'alimentent pas automatiquement la caisse du bar. Plan est global ; conditions Subscription conservées en snapshots. | Modèle demandé et décision MODEL-013 |
| STK-001 | StockMovement est append-only ; une correction est un nouveau mouvement lié. StockBalance est une projection reconstruisible, égale au cumul des mouvements et mise à jour dans la même transaction. | Historique immuable imposé par SRC-003 |
| STK-002 | DECIMAL(20,6) pour quantités ; une unité canonique par produit, figée dès premier mouvement. Aucun arrondi ou changement d'unité implicite. | DEC-016 et décision initiale |
| STK-003 | Validation d'achat/vente en une fois : mouvement unique par ligne, aucune réservation en DRAFT. Retour partiel via OrderReturnLine ; seule restock_quantity revient en stock. | Décision initiale MODEL-003, à réviser explicitement si réception fractionnée exigée |
| STK-004 | Inventaire partiel explicite : quantité attendue et version capturées ; version changée avant validation → conflit et recomptage, aucun écrasement silencieux. | Décision MODEL-005 |
| TEN-008 | Références renforcées : un retour et sa ligne vendue partagent la commande ; source stock et produit concordent ; paiement/remboursement/retour partagent la commande. Les FK composites et services appliquent ces invariants. | Décision MODEL-001 |
| AUTH-006 | StaffAssignment est le nom canonique ; ended_at NULL signifie active ; colonne active_user_id UNIQUE globale empêche le double rattachement. Changer de rôle termine l'affiliation et en crée une nouvelle, historique conservé. | Remplace la projection provisoire ActiveStaffAssignment |
| AUTH-007 | ApiToken représente un refresh token opaque lié à une identité et un seul bar ; empreinte uniquement, expiration obligatoire, family_id et rotated_from_id pour la rotation. TokenRevocation interdit réutilisation ; une réutilisation de refresh révoque la famille. Les droits actuels sont réévalués à chaque access API. Aucune émission ni nouvelle permission activée par le schéma. Sessions et révocations sont des métadonnées de sécurité, possibles sur bar suspendu ; aucun effet commercial. | Tables SRC-003, stratégie SRC-004 |
| AUTH-008 | IdempotencyRecord est unique par bar/acteur/opération/clé et atomique avec effets. Même clé/hash différent : conflit ; même hash : résultat antérieur après contrôle des droits actuels. Pas de réponse sensible contenant des jetons. | DEC-020 et MODEL-014 |

La restriction du rôle SERVER sur payments.record reste inchangée : une affiliation citée comme détentrice des espèces ne donne pas le droit d'enregistrer le paiement. Les permissions de retour, remboursement, paiement fournisseur, modification d'offre et émission de jeton restent à ajouter explicitement avant activation des opérations concernées.

Les états ci-dessus donnent des structures initiales utilisables ; ils ne résolvent pas les règles d'arrondi, fiscalité, coût de valorisation, stock négatif, crédit/surpaiement ni toutes les conditions de clôture. Les exemples de paiements/contre-écritures du dictionnaire ne sont pas des données réelles.


## Politique actuellement implémentée (stabilisation du 9 septembre 2026)

- Pas de stock négatif ; quantité exacte à six décimales et montant exact à quatre.
  Les résultats de multiplication non représentables sont refusés, sans arrondi inventé.
- Commandes : DRAFT → CONFIRMED → SERVED ; DRAFT/CONFIRMED peuvent être annulées avec motif,
  mais CONFIRMED encaissée est refusée. Les lignes sont éditables uniquement en DRAFT.
  La correction différentielle d'une commande confirmée reste à faire ; ne pas l'annoncer.
- Retour après service : permission orders.edit, lignes persistées, RESTOCK réintègre et
  LOSS ne retire pas une seconde fois ; cumul <= quantité vendue. Aucun remboursement implicite.
- Paiement : permission payments.record, commande confirmée/servie, positif, partiel possible,
  plafond vente nette des retours. Paiement hors espèces enregistré manuellement, sans appel
  à un prestataire ni preuve de confirmation externe. Espèces uniquement dans le tiroir ouvert.
- Clôture : permission cash.operate, somme du fond et des mouvements, comptage non négatif,
  motif persistant dans l'audit en cas d'écart. Garde/remise et remboursements encore indisponibles.
- Les règles d'idempotence, audit exhaustif et triggers décrites plus haut restent des objectifs.


### Compl�ment financier op�rationnel

Les services de finance activent `payments.read`, `cash.read` et `refunds.record`
pour SUPER_ADMIN/OWNER/BAR_ADMIN/CASHIER ; SERVER reste exclu. Les �critures
financi�res verrouillent le bar via la permission avant de modifier les documents.
Paiements nets = paiements affect�s - remboursements. Vente nette = vente - retours
POSTED. Le reste d� et le trop-per�u sont expos�s s�par�ment, sans montant n�gatif.
Les corrections de paiement sans retour peuvent rouvrir une dette. Les remboursements
avec retour sont limit�s par le paiement, le cr�dit du retour et le trop-per�u courant.
Une annulation CONFIRMED rembourse d�abord tout encaissement net puis restaure le stock,
dans la m�me transaction. SERVED exige le parcours de retour.
D�p�t/retrait manuel : montant positif, motif requis, mouvement sign�. Une correction
est une contre-�criture unique sur caisse ouverte ; aucune r��criture d�historique.
Aucune sortie ne peut rendre n�gatif le tiroir ou la garde. Les remises ne r�servent
pas la garde au brouillon : le solde est v�rifi� de nouveau � la confirmation.
L�affiliation termin�e peut rendre ses esp�ces restantes mais ne peut plus encaisser.
