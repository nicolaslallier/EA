---
titre: Gel par package — une version en vigueur, des triggers en base, une seule traduction
date: 2026-10-12
statut: Proposition
affects: backend/migrations/versions/0020_package_versions.py, backend/src/ea/db/models/package_version.py, backend/src/ea/db/unit_of_work.py, backend/src/ea/domain/package_versions.py, backend/src/ea/domain/packages.py, backend/src/ea/domain/baselines.py, backend/src/ea/domain/audit.py, backend/src/ea/domain/errors.py, backend/src/ea/repositories/package_store.py, backend/src/ea/repositories/baseline_store.py, backend/src/ea/repositories/frozen_tables.py, backend/src/ea/services/packages.py, backend/src/ea/services/baselines.py, backend/src/ea/services/frozen_refusals.py, backend/src/ea/api/packages.py, backend/src/ea/api/errors.py, backend/src/ea/api/schemas.py
---

# 74. Gel par package — une version en vigueur, des triggers en base, une seule traduction

Date : 2026-10-12
Statut : Proposition

## Contexte

Les exigences DOC-ADM-008 et DOC-ADM-010 demandent qu'un ensemble d'éléments puisse être publié sous une version
et ne plus bouger sans une réouverture tracée. Le dépôt connaissait trois gels, aucun à l'échelle d'un
sous-arbre : `locked` sur un élément (ADR 0048), la baseline, copie inerte du modèle (ADR 0047), et le diagramme
`frozen` (ADR 0068). Le gel d'un package doit, lui, tenir contre **toute** porte d'écriture : l'API, `/mcp`, un
import, le pipeline, un script, et contre une course avec la publication elle-même. La spec est
`docs/superpowers/specs/2026-10-12-gel-par-package-design.md` ; ce plan en est la moitié serveur (MCP, SPA,
`frozen_by` sur les lectures d'élément et de diagramme, diff et restauration scopés : plan 2).

## Décision

**La version en vigueur est une ligne.** `package_versions` (migration `0020`) garde une ligne par publication :
numéro `vX.Y.Z`, notes, ADR, acteur, date, baseline prise, événement d'audit, et les cinq colonnes de réouverture
(`reopened_at`, `reopened_by_sub`, `reopened_by`, `reopen_reason`, `reopen_audit_event_id`), qui se posent
**ensemble** (CHECK `reopening_whole`). Un package est gelé tant qu'il porte une ligne non fermée ; un sous-arbre
l'est tant qu'un ancêtre l'est. La table est append-only (triggers : seule la fermeture est un `UPDATE` permis).
Rien n'est matérialisé sur les éléments.

**Les triggers sont la règle.** Neuf tables portent un trigger `<table>_frozen` `BEFORE INSERT OR UPDATE OR
DELETE` : `elements`, `relationships`, `element_documents`, `element_stereotypes`, `diagrams`, `diagram_nodes`,
`diagram_objects`, `diagram_edges`, `packages`. Ils appellent `ea_frozen_ancestor(uuid)` (récursion bornée à 64,
`SET search_path = public, pg_temp`), regardent l'ancien **et** le nouveau package (classer dans un sous-arbre gelé
ou en sortir est refusé), et lèvent `SQLSTATE EA001` avec un `DETAIL` `package|version|table|op`. Une erreur du
trigger annule l'écriture : échec fermé. Il n'y a **aucune garde Python** à appeler : une garde est une porte
qu'on peut oublier, un trigger non.

**Une seule traduction : `work()`.** `db/unit_of_work.py` transforme l'`EA001` brut en `PackageFrozenError`
(409 `package_frozen`), y compris dans un import (`apply_rows` rend la ligne refusée en `RowRefusal`). Un
enregistreur (`services/frozen_refusals.py`) consigne le refus dans le journal d'audit
(`package.write_refused_frozen`) **dans sa propre transaction**, hors du contexte de l'unité de travail, avec un
budget de 30 par minute et par `sub`.

**La publication** prend `LOCK TABLE … IN SHARE MODE` sur les neuf tables gardées, en `READ COMMITTED`, puis crée
la baseline scopée, la ligne de version et l'événement dans la même transaction. Ce n'est pas `REPEATABLE READ` :
sous cet isolement l'instantané est pris avant le verrou et une écriture validée entre les deux manquerait à la
baseline sans être refusée. Le verrou d'arbre sérialise deux publications ; un verrou mort (`40P01`) contre un
écrivain ordinaire devient un 409 rejouable (`@replayable`). La **restauration** d'une baseline prend d'abord
`LOCK TABLE … ROW EXCLUSIVE` sur les mêmes tables (liste partagée, `repositories/frozen_tables.py`), sans quoi une
restauration `SERIALIZABLE` planifiée avant la publication écrirait dans le sous-arbre gelé.

**Baseline scopée.** Une baseline porte un périmètre (`scope_*`) : elle copie le sous-arbre, pas le modèle. Une
telle baseline **ne se compare ni ne se restaure** (`BaselineScopedError`, 409 `baseline_scoped`) tant que le plan
2 n'a pas écrit un diff scopé : restaurer comme un modèle entier planifierait la suppression de tout ce qui est
hors périmètre. Son nom embarque son propre uuid (infalsifiable), sa taille est mesurée en octets avec la formule
des baselines (`MAX_BASELINE_BYTES`), et une baseline désignée par une version ne se supprime pas.

**Rôle.** Publier et rouvrir relèvent de `ea-approver` (lister les versions : tout appelant authentifié) (`require_approver` dans
`services/packages.py`, jamais dans le routeur). `dry_run=true` par défaut rend l'aperçu (nombre d'éléments, de
liens, de diagrammes, version suivante).

**Pas de séparation des tâches à la publication (décision du 2026-10-12, à rouvrir si le dépôt passe à plusieurs
approbateurs).** Un approbateur peut publier un sous-arbre qu'il a lui-même modifié en dernier. La publication,
l'acteur, le motif d'une réouverture et les notes (empreinte) restent tracés. Cette règle diffère volontairement
de celle de l'ADR 0048 pour un élément isolé.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Matérialiser le gel sur chaque élément (colonne `frozen_by`) | Lecture directe | N écritures à la publication et à la réouverture, dérive possible, ne couvre pas diagrammes ni liens | Écartée |
| Garde Python dans chaque porte | Messages précis, testable sans base | Une porte oubliée (import, pipeline, script) est ouverte ; c'est déjà la leçon de `guard_element_write` | Écartée |
| Les deux (garde + trigger) | Défense en profondeur | Deux règles à tenir d'accord ; la garde ne ferait que prédire le trigger | Écartée |
| Publier en `REPEATABLE READ` | Instantané cohérent | Instantané pris avant le verrou : une écriture validée entre les deux passe | Écartée |

## Modèle de menaces

Repris de la spec § 10 ; chaque ligne a son test. Les lignes MCP sont celles du plan 2.

| Menace | Parade | Preuve |
|---|---|---|
| Un éditeur publie ou rouvre | `require_approver` dans `services/` ; 403 testé en HTTP (le MCP : plan 2) | `tests/unit/test_package_publication_service.py`, `tests/e2e/test_package_publication_api.py` |
| Contourner le gel par une porte oubliée | Triggers sur chaque table écrite ; test qui parcourt les tables rattachées au modèle | `tests/integration/test_package_freeze_triggers.py`, `tests/integration/test_package_freeze_security.py` (`test_no_table_that_hangs_from_the_model_is_left_unguarded`), `tests/integration/test_frozen_refusal_translation.py` |
| Course : écriture qui passe pendant la publication | `LOCK TABLE … SHARE MODE` + verrou d'arbre ; restauration en `ROW EXCLUSIVE` | `tests/integration/test_package_publication.py` (`gather`, restauration planifiée avant la publication) |
| Déplacer un élément hors d'un package gelé, ou le faire entrer | Ancien et nouveau `package_id` testés | `tests/integration/test_package_freeze_triggers.py` |
| Rouvrir sans laisser de trace | Motif obligatoire, événement, cinq colonnes ensemble, trigger append-only | `tests/integration/test_package_freeze_security.py` (`test_reopening_always_leaves…`), `tests/integration/test_package_freeze_triggers.py` |
| Un agent MCP gèle un sous-arbre par erreur | `dry_run=true` par défaut (HTTP aujourd'hui, MCP : plan 2) | `tests/e2e/test_package_publication_api.py`, `tests/unit/test_package_publication_service.py` |
| Publication d'un énorme sous-arbre (déni de service) | Plafonds de baseline mesurés en octets, `scope_too_large`, avant la lecture | `tests/integration/test_package_publication.py` (plafond), `tests/integration/test_baseline_scope.py` |
| Injection par `notes`, `adr`, `reason` | Bornes de `api/schemas.py`, refus du NUL, requêtes paramétrées, texte brut | `tests/integration/test_package_freeze_security.py` (`test_a_notes_text_made_of_sql_and_markup…`), `tests/unit/test_package_versions.py` |
| Fuite dans les logs et les erreurs | Empreintes et pas de texte dans l'audit ; ni noms ni contenu dans l'erreur ou le log de refus | `tests/integration/test_package_freeze_security.py` (`test_the_audit_trail_keeps_digests…`, `test_a_refusal_never_names…`), `tests/unit/test_package_version_audit.py` |
| Ancêtre gelé inconnu → écriture permise | Échec fermé : une erreur du trigger annule l'écriture | `tests/integration/test_package_freeze_security.py` (`test_a_trigger_that_cannot_decide…`) |
| Inonder le journal de refus | Budget de 30 par minute et par `sub` | `tests/unit/test_frozen_refusal_recorder.py` |

## Conséquences

- Supprimer un élément qu'un diagramme **publié** représente est refusé : la suppression emporterait la boîte du
  diagramme gelé.
- Restaurer une baseline qui touche un sous-arbre publié échoue **en bloc** (409 `package_frozen`) : rien n'est
  écrit, il faut rouvrir d'abord.
- Un script qui écrit **hors `work()`** reçoit l'`EA001` brut, sans traduction ni événement.
- `TRUNCATE` n'est pas gardé sur les neuf tables (l'application n'en émet jamais ; un propriétaire de la base peut
  de toute façon retirer les triggers). À durcir si un rôle d'écriture sans `TRUNCATE` n'est pas provisionné.
- Les baselines scopées ne se comparent ni ne se restaurent jusqu'au plan 2 ; la liste des baselines n'expose pas
  encore leur périmètre.
- L'enregistreur garde ses compteurs par `sub` **en mémoire du processus**, sans les élaguer : un redémarrage
  remet le budget à zéro, plusieurs processus ont chacun le leur, et `suppressed` est un cumul de vie.
- Les refus par un sous-arbre publié **dérogent à la règle de l'ADR 0046** (« une écriture refusée n'écrit aucun événement ») :
  une écriture tentée dans un sous-arbre publié est un signal de sécurité, et l'événement, écrit dans sa propre
  transaction, survit au rollback de l'écriture refusée.
- Un import en `dry_run` qui bute sur un sous-arbre gelé laisse des événements `package.write_refused_frozen`
  **validés** (l'enregistreur a sa propre transaction) alors que rien n'a été écrit.
- `package_versions.version` n'a pas de CHECK sur le format `vX.Y.Z` : une ligne écrite à la main hors format
  ferait lever les lectures. La récursion de `ea_frozen_ancestor` est bornée à 64, la profondeur de domaine à 12.
- Toute nouvelle table qui référence éléments, diagrammes, packages ou relations doit recevoir son trigger, ou
  être listée avec sa raison dans le test de `test_package_freeze_security.py`. Exemptées aujourd'hui :
  `external_references` (cache d'un tiers, nommé exempt par la spec), `package_versions` (append-only par ses
  triggers) ; les autres tables de la liste du test ne référencent pas le modèle ou en sont une copie.
- Une seule décision de procédure reste ouverte : la séparation des tâches (ci-dessus).

## Références

- Spec : `docs/superpowers/specs/2026-10-12-gel-par-package-design.md`
- ADR liés : [0047](0047-baselines-en-copie-normalisee.md), [0048](0048-approbation-et-verrouillage-des-elements.md), [0046](0046-journal-d-audit-append-only.md),
  [0052](0052-session-partagee-entre-cas-d-usage.md)
- Rétention : `docs/audit-retention.md`
- Code concerné : `backend/migrations/versions/0020_package_versions.py`, `backend/src/ea/services/packages.py`
