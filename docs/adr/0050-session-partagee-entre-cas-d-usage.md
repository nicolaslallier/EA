---
titre: Session partagée entre cas d'usage — une transaction, un savepoint par ligne, un import à la fois
date: 2026-10-11
statut: Proposition
affects: backend/src/ea/db/unit_of_work.py, backend/src/ea/repositories/{architecture,document,diagram,package,saved_query,baseline}_store.py, backend/src/ea/repositories/conflicts.py, backend/src/ea/domain/ports.py
---

# 50. Session partagée entre cas d'usage — une transaction, un savepoint par ligne, un import à la fois

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
| `work(sessions, *, alone=False)` | session d'une **écriture** de dépôt. Hors import : `sessions.begin()`, exactement comme avant. Dans un import : la session partagée, dans un `SAVEPOINT` (`begin_nested`) |
| `reading(sessions, *, alone=False)` | session d'une **lecture**. Hors import : `sessions()`, comme avant. Dans un import : la session partagée (elle voit les écritures de l'import) |
| `shared_transaction(sessions, *, commit, exclusive=False)` | ouvre la transaction de l'import et la lie à une `ContextVar` ; rend un `Transaction` (`commit` modifiable). Refuse l'imbrication |
| `savepoint()` | un `SAVEPOINT` pour une ligne ; relève l'exception après `ROLLBACK TO SAVEPOINT`, c'est à l'appelant de la rattraper |
| `ambient_session()`, `in_unit()`, `condemn_unit()`, `is_serialization_failure(error)` | état de l'import en cours |

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

**Un `SAVEPOINT` par écriture, et un par ligne.** Chaque appel de dépôt est atomique à lui seul (un échec
défait ses propres écritures, événement d'audit compris) ; `savepoint()` enveloppe en plus une *ligne* de
l'import, qui peut enchaîner plusieurs appels (créer deux éléments puis les relier) : un refus du domaine
(`ArchimateRuleError`, `DuplicateElementError`, `ElementLockedError`…) ou une `IntegrityError` défait la ligne
entière — éléments, liens et événements — et la transaction reste utilisable. Aucun événement n'est orphelin.

**40001 et 40P01 ne se rattrapent pas ligne par ligne.** PostgreSQL a abandonné la transaction : la suite ne
serait pas plus fiable que ce qui précède. `work` lève `WriteConflictError` (409 `write_conflict`, rejouable) et
**condamne** l'import : tout appel, lecture et `savepoint()` ultérieur relève la même erreur, et la sortie du bloc
annule tout, même si l'appelant a avalé l'exception (sinon un `COMMIT` sur une transaction abandonnée serait un
`ROLLBACK` silencieux présenté comme un succès). Rien n'est écrit pour tout l'import. Même règle si le
`COMMIT` lui-même est refusé. `replayable` ne ré-exécute rien (il ne l'a jamais fait) ; dans un import il
condamne l'import au lieu de laisser croire qu'un nouvel essai local a un sens.

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

- Une écriture ordinaire sur un élément verrouillé par l'import **attend** la fin de l'import, puis passe
  (test : `test_a_normal_write_waits_for_the_import_to_commit_then_goes_through`) ; elle ne perd ni n'écrase la
  modification de l'import.
- Les ids sont verrouillés dans l'ordre à l'intérieur d'**un** appel, pas d'un appel à l'autre : un import qui
  verrouille `Z` puis `A` peut croiser une écriture qui verrouille `A` puis attend `Z`. PostgreSQL détecte ce
  cycle (`deadlock_timeout`, 1 s) et abandonne **l'une** des deux : l'écriture ordinaire reçoit alors un 409
  `write_conflict` rejouable, ou l'import entier (condamné, rien d'écrit). Jamais d'attente infinie, jamais une
  500 (test : `test_a_deadlock_with_a_normal_write_is_a_replayable_conflict_never_a_hang`).
- Aucun `lock_timeout` n'est posé : une écriture ordinaire peut attendre aussi longtemps que dure l'import. C'est
  la raison de la borne ci-dessous.

## Coût et bornes

Mesuré sur le PostgreSQL jetable (Mac, 1 000 lignes = 2 éléments + 1 lien, soit 3 écritures chacune) :

| Mode | Durée |
|---|---|
| 3 000 écritures, une transaction chacune (aujourd'hui) | 8,3 s |
| une transaction, un `SAVEPOINT` par écriture | 7,3 s |
| + un `SAVEPOINT` par ligne | 9,1 s |
| + un `SAVEPOINT` par lot de 10 lignes | 7,9 s |

Environ 2,5 ms par écriture, dominés par les allers-retours et l'événement d'audit ; le `SAVEPOINT` de ligne coûte
~1,8 ms par ligne (+25 %). La mémoire est sans surprise : le cache d'identité de SQLAlchemy ne retient que ce qui
est référencé, et chaque appel le vide.

La **borne de taille** est `MAX_EXCHANGE_ITEMS = 50 000` éléments + relations par fichier (Task 10), soit de
l'ordre de 2 à 3 minutes d'import dans une seule transaction, pendant lesquelles les verrous s'accumulent. C'est
volontairement un ordre de grandeur supportable plutôt qu'un traitement par morceaux : un import validé par
morceaux ne serait plus atomique.

Le vrai coût d'un `SAVEPOINT` n'est pas le temps mais la **sous-transaction** : chaque `SAVEPOINT` qui écrit
consomme un identifiant de sous-transaction, et au-delà de 64 par transaction PostgreSQL déborde son cache
(`suboverflowed`), ce qui ralentit les instantanés des *autres* sessions pendant l'import. Un `SAVEPOINT` par
ligne et par écriture sur 50 000 lignes dépasse largement 64. Si la mesure en charge réelle montre ce ralentissement,
la parade prévue est un `SAVEPOINT` par **lot** (par exemple 100 lignes) ; si une ligne du lot est refusée, le lot
est annulé et ses lignes rejouées une à une, chacune avec son `SAVEPOINT`. Rien n'est à changer dans
`unit_of_work` pour cela : `savepoint()` s'imbrique. Cette parade n'est pas implémentée : elle ne l'a pas été
parce que ce serait de l'optimisation sans mesure.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Passer une `AsyncSession` en paramètre de chaque méthode de dépôt | explicite | change tous les ports, leurs doubles et leurs appelants ; un oubli écrit hors de la transaction sans que rien ne le dise | écarté |
| Un service d'import qui écrit en SQL | une seule session | contourne `ArchitectureService` : un graphe que l'API refuserait, des règles recopiées | écarté |
| Une transaction par ligne | aucun changement | pas d'annulation globale, un `dry_run` qui écrit, pas d'atomicité | écarté |
| Session dans `app.state` ou globale | simple | partagée entre requêtes | écarté |
| Rattraper 40001/40P01 ligne par ligne | import plus tolérant | la transaction est abandonnée : on continuerait sur un état qu'on ne peut plus raisonner, ou on validerait un `ROLLBACK` | écarté |
| Un canal d'audit « import » | trace plus fine | forgeable, et le canal dit la *porte* : l'import entre par HTTP ou MCP | écarté |

## Conséquences

- Hors import, rien ne change : mêmes transactions, mêmes verrous, mêmes erreurs, mêmes tests.
- Une écriture de dépôt dans un import coûte un aller-retour de `SAVEPOINT` supplémentaire (mesuré ci-dessus).
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
