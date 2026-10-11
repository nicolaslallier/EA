---
titre: Session partagée entre cas d'usage — une transaction, un savepoint par lot de lignes, un import à la fois
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/db/unit_of_work.py, backend/src/ea/repositories/{architecture,document,diagram,package,saved_query,baseline}_store.py, backend/src/ea/repositories/conflicts.py, backend/src/ea/domain/ports.py
---

# 50. Session partagée entre cas d'usage — une transaction, un savepoint par lot de lignes, un import à la fois

Date : 2026-10-11
Statut : Proposition

## Contexte

Chaque méthode d'écriture d'un dépôt ouvre **sa** transaction, prend ses verrous de ligne (`lock_elements`, dans
l'ordre des ids, ADR 0046), reçoit `audit=` (l'événement est écrit dans la même transaction, ADR 0044), applique
`guard_element_write` et enveloppe ses erreurs 40001/40P01 en `WriteConflictError` (`repositories/conflicts.py`).
Ce durcissement est fait pour **un** cas d'usage = **une** transaction.

L'import d'un fichier d'échange (lot 2 du sous-projet 3, ADR 0048) veut l'inverse : exécuter *plusieurs* cas
d'usage de `ArchitectureService` (`create_element`, `connect`…) pour que chaque règle ArchiMate, IPAM, d'audit et
d'approbation s'applique sans être recopiée ; défaire **une** ligne refusée sans perdre les autres ; et pouvoir
tout annuler (`dry_run`) en laissant le modèle **et** le journal d'audit intacts. Le sous-projet « historique et
versions » (ADR 0044) avait laissé cette interface à ce sous-projet : l'événement d'audit suit déjà la session que
le dépôt utilise, il suffit donc que cette session puisse être celle de l'import.

## Décision

**Une session liée au contexte, un seul point d'entrée pour les dépôts.** `db/unit_of_work.py` :

| Signature | Rôle |
|---|---|
| `work(sessions, *, alone=False)` | session d'une **écriture** de dépôt. Hors import : `sessions.begin()`, exactement comme avant. Dans un import : la session partagée, dans un `SAVEPOINT`, sauf si un lot est ouvert (le savepoint du lot le couvre) |
| `reading(sessions, *, alone=False)` | session d'une **lecture**. Hors import : `sessions()`, comme avant. Dans un import : la session partagée (elle voit les écritures de l'import) |
| `shared_transaction(sessions, *, commit, exclusive=False)` | ouvre la transaction de l'import et la lie à une `ContextVar` ; rend un `Transaction` (`commit` modifiable). Refuse l'imbrication |
| `apply_rows(rows, apply, *, batch_size=BATCH_SIZE, max_refused=MAX_REFUSED_ROWS)` | applique des lignes par lots d'un `SAVEPOINT` ; rend la liste des `RowRefusal(index, error)` |
| `savepoint()` | le `SAVEPOINT` d'un lot (primitive de `apply_rows`) ; relève l'exception après l'avoir annulé **et relâché** |
| `ambient_session()`, `in_unit()`, `condemn_unit()`, `is_serialization_failure(error)` (40001, 40P01, 55P03) | état de l'import en cours |

Les six dépôts du modèle (`architecture`, `document`, `diagram`, `package`, `saved_query`, `baseline`) n'ouvrent
plus jamais `self._sessions.begin()` ni `self._sessions()` : ils passent par `work` / `reading`.
`tests/unit/test_one_way_to_open_a_unit.py` lit le source (AST) et échoue si l'un d'eux le refait ; trois dépôts
sont exemptés avec leur raison écrite (`audit_store` : lit le journal ; `exchange_store` : instantané de l'export ;
`file_metadata_store` : pas le modèle). `test_audit_coverage.py` reconnaît `work(...)` comme une ouverture
d'écriture, de sorte que l'exigence « `audit` obligatoire » s'applique toujours.

**Une `ContextVar`, comme `current_caller`.** Deux imports concurrents, ou une requête ordinaire pendant un
import, ne partagent jamais de session : la requête ordinaire voit `None` et ouvre sa propre transaction. La
variable est remise à zéro à la sortie, exception comprise. *Piège connu* : une tâche `asyncio` créée **pendant**
l'import en hérite (copie du contexte) ; un test qui veut un acteur indépendant copie le contexte avant
(`contextvars.copy_context()` puis `create_task(..., context=...)`).

**Un `SAVEPOINT` par lot de 1 000 lignes, pas par ligne.** Hors lot, chaque appel de dépôt est atomique à lui
seul (un échec défait ses propres écritures, événement d'audit compris). Dans `apply_rows`, un seul `SAVEPOINT`
couvre un lot de `BATCH_SIZE = 1 000` lignes et les appels de dépôt n'en ouvrent aucun. Quand la ligne k d'un lot
est refusée (règle du domaine : `ArchimateRuleError`, `DuplicateElementError`, `ElementLockedError`… ; ou
contrainte : `IntegrityError`, `DataError`) : `ROLLBACK TO SAVEPOINT` du lot, **rejeu des lignes 0..k-1 du lot sans
savepoint**, la ligne k est écartée (une `RowRefusal` pour le rapport), un nouveau lot repart à k+1. Aucun
événement n'est orphelin : tout ce que le lot avait écrit, audit compris, a été annulé, et le rejeu réécrit
celui des lignes conservées. Un rejeu qui échoue à une ligne j<k (ce qui s'est passé n'est plus reproductible)
**condamne** l'import (409 `write_conflict`, rien d'écrit). Ce qui n'est pas la faute de la ligne
(`WriteConflictError`, rôle manquant, import déjà en cours) n'est jamais un refus de ligne. Plus de
`MAX_REFUSED_ROWS = 100` refus, l'import est rejeté en entier (`TooManyRefusedRowsError`, 422
`too_many_refused_rows`) : un fichier où autant de lignes sont refusées est presque toujours le mauvais fichier, et
chaque refus coûte un rejeu.

Le rejeu **ne rejoue pas à l'identique** : `ArchitectureService` donne un nouvel identifiant à un élément créé, et le
premier passage a disparu avec le `ROLLBACK TO`. Le rappel `apply` d'une ligne doit donc ne tirer son état que
de ce qu'il fait *maintenant* (la table « identifiant du fichier -> id créé » se réécrit à chaque passage) et ne rien
supposer d'un passage précédent, y compris pour la ligne refusée, dont les entrées partielles sont à ignorer.

**40001, 40P01 et 55P03 ne se rattrapent pas ligne par ligne.** PostgreSQL a abandonné la transaction (ou, pour 55P03,
un verrou n'est pas venu à temps) : la suite ne serait pas plus fiable que ce qui précède. `work` lève
`WriteConflictError` (409 `write_conflict`, rejouable) et **condamne** l'import : tout appel, lecture et `savepoint()`
ultérieur relève la même erreur, et la sortie du bloc annule tout, même si l'appelant a avalé l'exception (sinon un
`COMMIT` sur une transaction abandonnée serait un `ROLLBACK` silencieux présenté comme un succès). Rien n'est
écrit pour tout l'import. Même règle si le `COMMIT` lui-même est refusé. `replayable` ne ré-exécute rien (il ne l'a
jamais fait) ; il condamne l'import au lieu de laisser croire qu'un nouvel essai local a un sens.

**`dry_run` = exécution complète, puis `ROLLBACK`.** `shared_transaction(commit=False)` exécute les mêmes
règles, les mêmes contraintes d'unicité, la même détection de collisions, puis annule : ni élément, ni relation,
ni `audit_events`, ni verrou conservé. Le rapport d'un `dry_run` est donc, par construction, celui que l'écriture
réelle donnerait. Les numéros de séquence consommés ne sont pas rendus (PostgreSQL ne les rend jamais) ; aucune
table du modèle n'en utilise pour une clé métier (identifiants UUID).

**Un seul import à la fois.** `exclusive=True` prend `pg_try_advisory_xact_lock(IMPORT_LOCK)` comme première
instruction : un second import reçoit **immédiatement** `ImportInProgressError` (409 `import_in_progress`), sans
attendre. Le verrou est de transaction : il part avec le `COMMIT` ou le `ROLLBACK`, y compris sur exception. Les
écritures ordinaires ne le prennent pas et ne sont jamais bloquées par lui.

**L'audit reste attribué et fail-closed.** L'acteur et la porte (`http`/`mcp`) viennent du `Caller` et du canal
posés sur la requête par l'adaptateur, déjà des `ContextVar` que l'import ne touche pas : il n'existe pas de
canal « import » à forger, et les événements des lignes importées portent le canal de la requête. L'événement est
toujours écrit dans la transaction de l'écriture (partagée ici) : annulé avec elle.

**Les gardes d'approbation s'appliquent ligne par ligne.** Rien ne change : chaque écriture passe par
`guard_element_write` sous le verrou de la ligne. Conséquences pour l'import (règles posées ici, écrites à la
Task suivante) : un élément existant n'est jamais modifié (collision (type, nom) ⇒ réutilisé, sans écriture) ;
relier un élément verrouillé est refusé par la garde `LINK` — c'est un **refus de ligne** du rapport (code
stable), jamais une exception qui tue l'import ; relier un élément approuvé le repasse « en revue » (nouvelle
révision, événement), ce que le rapport signale (`approved_element_reopened`) et que le `dry_run` annule.

**Ce qui ne peut pas rejoindre un import.** `baseline_store.create`, `apply_restore` et `_at_one_instant` fixent
un niveau d'isolation avant leur première instruction, impossible au milieu d'une transaction : ils passent
`alone=True` et lèvent `RuntimeError` dans un import au lieu de tourner silencieusement au mauvais niveau.

**Les lectures de l'import ne sont pas périmées.** Chaque appel `expire_all()` la session avant de commencer :
une ligne chargée plus tôt n'est jamais relue depuis le cache d'identité après qu'une autre transaction a validé
un changement (`READ COMMITTED`). Tout ce que l'import a écrit est déjà vidé (`flush`) à la fin de l'appel précédent.

## Verrous : combien de temps, et jusqu'où

Les verrous de ligne (`FOR UPDATE`, `FOR NO KEY UPDATE`) et le verrou consultatif de l'arbre des paquets
**s'accumulent** jusqu'à la fin de la transaction : un import les garde pendant toute sa durée, là où une écriture
normale les rend au bout de quelques millisecondes.

- **`lock_timeout` de 10 s sur les connexions de l'API** (`EA_DB_LOCK_TIMEOUT_MS`, défaut 10 000, 0 = désactivé,
  validé `>= 0` par `Settings` ; envoyé comme paramètre de serveur par `create_api_engine`, jamais par
  `create_engine` : ni les migrations Alembic ni `ea.reindex`, `ea.baseline_capture`, `ea.files_reconcile` ne
  l'ont, ils peuvent attendre aussi longtemps qu'il le faut). Sans lui, avec un pool de 5+5 connexions, dix
  écritures ordinaires bloquées derrière un import occuperaient tout le pool pendant des minutes : lectures,
  `/health` et `/me` gelés, puis un 504 de NGINX alors que l'écriture finit par passer et que le client la rejoue
  (doublon ou 409). Un verrou qui n'arrive pas en 10 s lève SQLSTATE 55P03, traduit en 409 `write_conflict`
  rejouable (`conflicts.py`) et qui condamne l'import s'il survient dans un import. Le `proxy_read_timeout` de
  NGINX (60 s) reste supérieur au `lock_timeout` (10 s) exprès : l'API doit répondre elle-même, par un 409 typé,
  avant que le proxy ne coupe la connexion.
- **L'import hérite du même `lock_timeout`** (ses connexions sont celles de l'API) : une ligne qui attend plus de
  10 s une écriture ordinaire condamne l'import entier (409 `write_conflict`, rien d'écrit). Une attente brève
  est sans effet (test : `test_an_import_survives_a_short_wait_and_is_condemned_by_a_long_one`).
- Une écriture ordinaire sur un élément verrouillé par l'import **attend** la fin de l'import, au plus 10 s, puis
  passe (test : `test_a_normal_write_waits_for_the_import_to_commit_then_goes_through`) ou reçoit un 409 rejouable
  (`test_a_normal_write_waits_for_the_timeout_then_gets_a_replayable_conflict`) ; elle ne perd ni n'écrase
  jamais la modification de l'import.
- **Limite connue : l'interblocage import/écriture.** Les ids sont verrouillés dans l'ordre à l'intérieur d'**un**
  appel, pas d'un appel à l'autre : un import qui a verrouillé `Z` puis demande `A` peut croiser une écriture
  ordinaire qui tient `A` et demande `Z`. PostgreSQL détecte le cycle (`deadlock_timeout`, 1 s) et abandonne **l'une**
  des deux : l'écriture ordinaire reçoit un 409 rejouable, ou l'import entier est condamné (rien d'écrit, quelques
  minutes perdues, 409 rejouable). Aucune donnée perdue, jamais d'attente infinie (test :
  `test_a_deadlock_with_a_normal_write_is_a_replayable_conflict_never_a_hang`). **Atténuation non implémentée** :
  verrouiller par avance, dans l'ordre des ids, les éléments *existants* que l'import sait toucher (ceux de ses
  collisions (type, nom) pour lesquels il va créer des liens). Elle suppose de connaître cet ensemble avant la
  première écriture, donc de l'analyseur et du plan d'import (tâches suivantes), et d'une méthode de dépôt qui prend
  des verrous sans écrire ; ce n'est pas simple ici, et elle est laissée à l'import (`lock_elements` est déjà la
  fonction à appeler).

## Coût et bornes

**Ce qui coûte vraiment : les sous-transactions suivies.** PostgreSQL suit au plus 64 sous-transactions par
transaction dans le cache de chaque session ; au-delà (`subxact_overflowed`) les instantanés de **toutes** les sessions
de la base partagée passent par `pg_subtrans`, plus lent. Mesuré sur PostgreSQL 17 avec
`pg_stat_get_backend_subxact` : un `SAVEPOINT` qui n'écrit pas ne coûte rien, mais **un `SAVEPOINT` qui écrit reste
compté même relâché** (`RELEASE` ne libère rien) : 100 savepoints écrivants donnent 64 suivis, `overflowed = true`.
Deux niveaux écrivants par ligne (celui de la ligne et celui de l'appel de dépôt) font déborder dès ~32 lignes.

Deux autres sources de sous-transactions ont été trouvées et neutralisées :

- `ROLLBACK TO SAVEPOINT` annule les écritures mais **laisse le savepoint vivant**, comme un niveau vide de plus ; la
  prochaine écriture donne un identifiant à tous les niveaux au-dessus d'elle. Chaque refus ajoutait donc un
  niveau suivi (mesuré : 1, 10, 30, 60 refus -> 2, 11, 31, 61 suivis). `_nested` relâche (`RELEASE SAVEPOINT`) le
  savepoint juste après l'avoir annulé : 100 refus consécutifs -> 1 suivi.
- Les appels de dépôt à l'intérieur d'un lot n'ouvrent pas leur propre savepoint (compteur `batched`).

Résultat, `BATCH_SIZE = 1 000` : au plus `lignes / 1 000` lots complets, soit **50 pour 50 000 lignes (< 64)**.
Mesures (`subxact_count`, `overflowed`) : 2 000 lignes -> 2, non ; 6 000 lignes dont 20 refusées -> 1, non ;
100 refus consécutifs -> 1, non ; la même chose avec un savepoint par ligne -> 64, oui. Chaque refus coûte le rejeu
des lignes qui précèdent la ligne refusée dans son lot (au plus 999) : d'où `MAX_REFUSED_ROWS = 100`, soit au pire
100 000 écritures rejouées, de l'ordre de la minute.

Durées mesurées (PostgreSQL jetable, Mac) : 2 000 lignes de 1 écriture -> 1,2 s par lots, 3,8 s avec le savepoint
d'un appel par ligne ; 6 000 lignes dont 20 refusées -> 7,5 s ; 100 refus consécutifs -> 0,2 s. La mémoire est sans
surprise : le cache d'identité de SQLAlchemy ne retient que ce qui est référencé, et chaque appel le vide.

La **borne de taille** est `MAX_EXCHANGE_ITEMS = 50 000` éléments + relations par fichier (Task 10), soit de l'ordre
de 2 à 3 minutes d'import dans une seule transaction, pendant lesquelles les verrous s'accumulent (d'où le
`lock_timeout` ci-dessus pour les autres).

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Passer une `AsyncSession` en paramètre de chaque méthode de dépôt | explicite | change tous les ports, leurs doubles et leurs appelants ; un oubli écrit hors de la transaction sans que rien ne le dise | écarté |
| Un service d'import qui écrit en SQL | une seule session | contourne `ArchitectureService` : un graphe que l'API refuserait, des règles recopiées | écarté |
| Une transaction par ligne | aucun changement | pas d'annulation globale, un `dry_run` qui écrit, pas d'atomicité | écarté |
| Session dans `app.state` ou globale | simple | partagée entre requêtes | écarté |
| Un `SAVEPOINT` par ligne (première version de ce document) | simple | 2 niveaux écrivants par ligne : le cache de 64 sous-transactions déborde dès ~32 lignes et ralentit toute la base | écarté, remplacé par les lots |
| Rattraper 40001/40P01 ligne par ligne | import plus tolérant | la transaction est abandonnée : on continuerait sur un état qu'on ne peut plus raisonner, ou on validerait un `ROLLBACK` | écarté |
| Un canal d'audit « import » | trace plus fine | forgeable, et le canal dit la *porte* : l'import entre par HTTP ou MCP | écarté |

## Conséquences

- Hors import, rien ne change : mêmes transactions, mêmes verrous, mêmes erreurs, mêmes tests.
- Une écriture de dépôt dans un lot n'ajoute aucun aller-retour ; le coût d'un import est celui de ses écritures plus un `SAVEPOINT` par lot.
- Tout nouveau dépôt qui écrit le modèle doit passer par `work` / `reading` (le test AST l'exige) ; tout appel
  qui fixe un niveau d'isolation passe `alone=True`.
- **Non garanti** : une lecture faite depuis une autre session *pendant* un import long voit l'état d'avant
  (`READ COMMITTED`) puis, au `COMMIT`, tout d'un coup ; elle n'est pas bloquée par l'import, seule une
  *écriture* sur une ligne qu'il a verrouillée attend. Un import n'est pas isolé des écritures ordinaires qui
  touchent d'autres lignes : il peut donc en voir les effets entre deux lignes. Les nombres de lignes d'un
  rapport décrivent l'import, pas un instantané du catalogue. Un import et une restauration de base (`apply_restore`,
  `SERIALIZABLE`) ne s'excluent pas par ce verrou.
- Le verrou d'import est tenu par la transaction : un import dont le processus meurt le perd avec sa connexion
  (PostgreSQL abandonne la transaction).

## Références

- ADR liés : [0015](0015-socle-postgresql-sqlalchemy-alembic.md), [0033](0033-postgresql-seul-pour-le-graphe.md),
  [0044](0044-journal-d-audit-append-only.md), [0046](0046-approbation-et-verrouillage-des-elements.md),
  [0048](0048-echanges-formats-collision-et-rejet-par-ligne.md)
- Code concerné : `backend/src/ea/db/unit_of_work.py`, `backend/src/ea/repositories/conflicts.py`
- Tests : `backend/tests/unit/test_unit_of_work.py`, `backend/tests/unit/test_one_way_to_open_a_unit.py`,
  `backend/tests/integration/test_unit_of_work.py`
