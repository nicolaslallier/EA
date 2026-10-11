# Historique et versions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Donner au catalogue une mémoire (journal d'audit append-only écrit dans la transaction de chaque écriture), des instantanés comparables et restaurables (baselines globales) et un cycle brouillon → en revue → approuvé → verrouillé par élément, exposés en HTTP, en MCP et dans le SPA.

**Architecture:** `services/` construit les `AuditEvent` (acteur du `Caller`, horloge injectée, `request_id`, canal) et les passe au dépôt par un paramètre keyword-only `audit`; chaque dépôt les `INSERT` par `write_events(session, events)` dans **sa propre** `begin()`, après l'écriture et avant le commit. Les baselines sont des copies normalisées du graphe (`baselines`, `baseline_elements`, `baseline_relationships`, `baseline_boxes`) capturées par `INSERT … SELECT`. L'approbation vit dans des colonnes de `elements` (hors `properties`), avec des règles pures dans `domain/approval.py` appliquées par `ArchitectureService`. `api/` et `mcp/` ne touchent jamais un dépôt: ils appellent les services.

**Tech Stack:** FastAPI + Pydantic v2, SQLAlchemy 2 async, Alembic (0009 → 0011), PostgreSQL (pgvector), `mcp` SDK 2.x, Vue 3 + TypeScript + Vitest, `uv`, `ruff`, `mypy --strict`.

**Spec:** `docs/superpowers/specs/2026-10-10-02-historique-versions-design.md`

## Écarts constatés avec la spec

La spec a été écrite sans lire tout le code; le plan suit le code réel. Chaque écart est repris dans la tâche qui le porte.

1. **`ArchitectureService.delete_element` ne lit rien aujourd'hui** (la spec dit « déjà fait pour 404 »): il s'appuie sur le `rowcount` du dépôt. Le service doit lire l'élément (`get_element`), ses liens (`relations_of`) et ses rattachements avant de supprimer. Les rattachements (documents, diagrammes) n'ont pas de lecteur: Task 5 ajoute `ArchitectureRepository.attachments_of` (deux `SELECT`), car `ArchitectureService` ne tient ni dépôt de documents ni dépôt de diagrammes.
2. **Pas de rôle applicatif séparé, pas de `REVOKE`.** Le déploiement n'a qu'un rôle `ea` propriétaire (docs/adr/0029, `make provision-app`) et une migration Alembic n'a pas `CREATEROLE`. La migration 0009 pose le trigger `BEFORE UPDATE OR DELETE` (garantie effective) et, en plus de la spec, un trigger `BEFORE TRUNCATE` (le `REVOKE TRUNCATE` de la spec n'ayant pas d'équivalent sans second rôle). Le rôle `INSERT, SELECT` seul est **« à faire hors de ce repo »** (dépôt Infra), listé dans l'ADR.
3. **`channel` (`http|mcp|system`) n'est pas dans `after.meta`** comme le dit la spec: `after` est `null` pour un `*.deleted`, le canal serait perdu pile là où l'on en a besoin, et il polluerait `diff_fields`. Colonne dédiée `channel text not null default 'system'` dans la migration 0009. Le canal voyage par un `ContextVar` (`current_channel`, `services/caller.py`) posé par `api/auth.py` (`http`) et `speaking_plainly` (`mcp`), comme l'est déjà le `Caller`.
4. **`AuditWriter` (classe) devient une fonction** `write_events(session, events)` dans `repositories/audit_store.py`: un objet sans état n'a rien à porter.
5. **Les dépôts ouvrent une unité de travail par appel** (`async with self._sessions.begin()`), confirmé dans `architecture_store.py`, `document_store.py`, `diagram_store.py`. Le paramètre `audit` est donc ajouté à 12 méthodes (`add_element`, `save_element`, `delete_element`, `add_relationship`, `delete_relationship`; `DocumentRepository.add/replace/delete`; `DiagramRepository.add/save/delete/replace_layout`), toutes avec défaut `()`. `DocumentService.reindex_all` appelle `replace` **sans** audit: réindexer n'est pas changer le contenu.
6. **Session partagée entre cas d'usage (sous-projet 3, échanges)**: le présent plan n'invente pas d'interface. Il laisse exactement un point d'accroche, dit dans Task 5 et Task 17: les méthodes d'écriture des dépôts reçoivent `audit=` et ouvrent leur `begin()`. Le sous-projet 3 attend une interface commune « ces écritures participent à une transaction ouverte ailleurs » (par exemple un paramètre optionnel `session: AsyncSession | None` accepté par chaque méthode d'écriture, ou un objet d'unité de travail) — à **concevoir une seule fois**, avec le sous-projet 3, et à appliquer aux mêmes 12 méthodes + `apply_restore`. Rien ici ne doit la contredire: `write_events` prend déjà une session.
7. **`ElementRead.of(element)`** reste à un argument; `allowed_transitions` y est calculé depuis `current_caller` (Task 28), pour que le SPA, l'API REST et MCP lisent la même réponse sans que chaque appelant ne passe un rôle.
8. **Le sous-projet 1 (paquets) n'existe pas dans le code** (aucune table `packages`). Conformément à R9, **aucune tâche ne porte le périmètre « paquet »**: `Baseline` n'a pas de `package_id`, `POST /baselines` n'en accepte pas. L'extension (S) est à planifier avec le sous-projet 1.
9. **Spec silencieuse, défaut retenu**: les tables `baseline_*` ne copient pas les colonnes d'approbation (une baseline capturée avant le lot 3 reste valide); une restauration qui modifie un élément `approved` le remet `in_review` (jamais `approved` en silence), un élément recréé est `draft`.
10. **R8 partielle**: le plan de restauration est calculé avant la transaction; `apply_restore` passe en `SERIALIZABLE` et vérifie que ses cibles existent encore, sinon `409 restore_conflict` rejouable. Un plan « exact à l'instant du calcul », pas un verrou sur toute l'opération.
11. **`ElementRead.approved_by`** porte le `sub` Keycloak (la spec ne stocke que `approved_by_sub`).
12. **`/elements/{id}/history` est `entity_kind=element&entity_id=` à la lettre**: les événements `document.*` d'un élément n'y figurent pas (ils ont `entity_kind=document`); l'écran Historique les trouve par `?kind=document`.
13. **`request_id` hors requête** (script `ea.reindex`, `make baseline-capture`) vaut `"-"` (`NO_REQUEST`), colonne `NOT NULL`.
14. **Playwright n'existe pas**: la spec demande un E2E « dès qu'il existe »; aucune tâche ne le crée. La couverture est l'API (`tests/e2e`), les composants (Vitest) et l'intégration.
15. **Hors de ce dépôt (« à faire hors de ce repo »)**: rôle de royaume `ea-approver` et son mapper dans l'import du realm `ea` (dépôt Infra, ADR 0032 encore Proposition); rôle PostgreSQL d'écriture `INSERT, SELECT` seul sur `audit_events`; politique de purge opérateur (documentée ici, exécutée là-bas).
16. **Numéros**: migrations 0009 (audit), 0010 (baselines), 0011 (approbation) — à renuméroter si une autre migration entre avant. ADR: le prochain numéro libre à l'exécution (0042 aujourd'hui) pour le premier, les suivants à la suite; les tâches disent `NNNN`.

## Global Constraints

- Architecture: `api → services → domain ← repositories`; `mcp/` à côté de `api/`, jamais un dépôt ni HTTP vers soi; `domain/` n'importe ni FastAPI ni SQLAlchemy.
- Chaque méthode publique de `ArchitectureService`, `DocumentService`, `IpamService`, `DiagramService`, `AuditService`, `BaselineService` commence par `require_caller()` (lecture) ou `require_editor()` (écriture); `tests/unit/test_service_guards.py` (AST) le vérifie; `transition_element` commence par `require_caller()`.
- Un événement d'audit par cas d'usage (pas par ligne SQL), écrit par le dépôt dans la **même** `begin()` que l'écriture, après son `flush`; un refus (404, 409, 403, 422) n'écrit aucun événement.
- `audit_events`: `id uuid pk`, `at timestamptz`, `actor_sub`, `actor_name`, `action`, `entity_kind`, `entity_id uuid null` **sans FK**, `before jsonb null`, `after jsonb null`, `request_id text not null`, `channel text not null default 'system'`, `correlation text null`; index `(entity_kind, entity_id, at desc)`, `(at desc)`, `(actor_sub, at desc)`; append-only par trigger (`UPDATE`, `DELETE`, `TRUNCATE`).
- L'acteur vient de `current_caller` (jeton vérifié), jamais d'un paramètre; `at` vient de l'horloge injectée du service; jamais de secret ni de contenu de document dans `before/after` (nom, taille, sha256 seulement).
- Rétro-remplissage (migration 0009): un `element.created` / `relationship.created` par ligne existante, `actor_sub='ea-system'`, `at = created_at`, `after` = état courant, `correlation='backfill'`; éléments existants `draft`, non verrouillés; aucune baseline automatique.
- `GET /audit`: lecteur; filtres `entity_kind`, `entity_id`, `actor`, `action`, `since`, `until`, `request_id`, `cursor`, `limit` (≤ 200, défaut 50); tri `at desc, id desc`; curseur opaque `(at, id)`; `since` inclus, `until` exclu; curseur invalide → `422 invalid_cursor`.
- Bornes (longueurs, pages) déclarées **une fois** dans `api/schemas.py`, importées par `mcp/server.py`; chaque nouvel argument borné entre dans `TestTheSameBoundsAsTheHttpAdapter`; une valeur que le schéma ne sait pas exprimer est une erreur de domaine avec sa ligne dans `_STATUS` (`api/errors.py`).
- Baselines: copie normalisée; `POST /baselines` éditeur → `201`, `409` nom dupliqué; `DELETE` → `204`; `GET /baselines/{id}/diff?against=<uuid|current>`; `POST /baselines/{id}/restore` corps `{dry_run: bool = true, confirm: bool = false, keep_new: bool | null = null}`, éditeur; `dry_run` n'écrit rien; appliquer exige `dry_run=false` **et** `confirm=true`; `keep_new` par défaut `false` (baseline globale); élément verrouillé touché → `409 restore_blocked`; conflit d'unicité → `409 restore_conflict` et rollback total; transaction `SERIALIZABLE`.
- Restauration: id d'origine conservé; documents et boîtes d'un élément supprimé depuis ne reviennent pas (documents non capturés); relation devenue illégale = avertissement, pas échec; événements `baseline.restored` puis un par entité touchée, même `request_id`, `correlation='restore:<baseline_id>'`.
- Approbation: `ApprovalState` = `draft | in_review | approved`; `locked: bool`; colonnes `approval_state text not null default 'draft'` (CHECK 3 valeurs), `locked boolean not null default false`, `approved_by_sub text null`, `approved_at timestamptz null`, CHECK `locked ⇒ approval_state = 'approved'`; hors de `properties`.
- Transitions: `submit` (éditeur) draft→in_review; `approve` (`ea-approver`, et pas l'auteur du dernier `element.updated` si `EA_APPROVAL_FOUR_EYES`, défaut `true`) in_review→approved; `reject` (`ea-approver`) in_review→draft; `reopen` (`ea-approver`) approved→draft (efface `approved_by/at` et `locked`); `lock` / `unlock` (`ea-approver`) approved↔locked; rôle insuffisant → `403`, transition interdite par l'état → `409 invalid_transition`.
- Élément `approved`: `update_element` / `delete_element` refusés à un éditeur simple (`ElementLockedError`, `409 element_locked`); seul `ea-approver` modifie, et toute modification repasse l'élément en `in_review`; `locked` interdit toute modification même à l'approbateur; relation ajoutée/retirée sur un élément `locked` refusée, sur un élément `approved` autorisée avec passage en `in_review` (même transaction); `in_review` reste modifiable.
- `APPROVER_ROLE = "ea-approver"` dans `domain/auth.py`; `Caller.can_approve`; `LOCAL_DEVELOPER` et `SYSTEM` reçoivent le rôle; `GET /me` expose `can_approve`; `ElementRead` gagne `approval_state`, `locked`, `approved_by`, `approved_at`, `allowed_transitions`.
- MCP: sept nouveaux outils (`list_audit_events`, `get_element_history`, `list_baselines`, `diff_baselines`, `create_baseline`, `restore_baseline`, `transition_element`), `@speaking_plainly`, annotation `REMOVES` pour `restore_baseline`, `dry_run=True` par défaut, liste complète assertée dans `tests/unit/test_mcp_server.py`, `INSTRUCTIONS` mises à jour.
- OpenAPI: tout changement de schéma = `make openapi` (commit `backend/openapi.json` + `frontend/src/api/schema.d.ts`); `make openapi-check` vert.
- SPA: sections « Historique » `/history` et « Baselines » `/baselines` dans le groupe « Gouvernance » de `router/sections.ts`; état dans l'URL (`?kind= ?entity= ?actor= ?action= ?since= ?cursor=`; `?a= ?b=`); chargements par `useLatestRequest()`; le SPA masque, l'API décide; aucune règle d'approbation ni de métamodèle dupliquée en TypeScript (transitions = `ElementRead.allowed_transitions`); libellés d'actions = table de traduction.
- TDD: test d'échec d'abord, lancé sans `--cov` (`cd backend && uv run pytest tests/unit/<fichier> -x`); `ruff`, `mypy --strict src migrations`, `bandit` propres; couverture ≥ 90 % (`make test`); toute nouvelle contrainte = révision Alembic appliquée puis annulée par `make test-integration` (PostgreSQL **jetable**, jamais le partagé).
- Les extraits de ce plan ne sont pas pré-formatés à 100 colonnes: `make lint` (qui formate) précède chaque commit, `make lint-check` le vérifie; une ligne de chaîne trop longue est coupée à la main.
- Commits Conventional Commits en anglais, terminés par `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`; pas de `--no-verify`.
- Rien de secret dans le code; SQL brut seulement pour du DDL statique de migration (commentaire qui le dit).

## Review Focus

1. **Atomicité refus ⇒ aucun événement** — écriture refusée au milieu (doublon de nom, extrémité de lien absente, `delete` d'un id inconnu, lecteur qui écrit) n'écrit pas d'événement, et un événement survit à la suppression de son entité. Test: Task 5 (PostgreSQL) et Task 6/7 (services).
2. **Curseur et bornes de temps** — curseur forgé ou tronqué ⇒ `422 invalid_cursor` (jamais 500); pagination sans doublon ni trou avec des `at` identiques; `since`/`until` sans fuseau acceptés (lus en UTC). Test: Task 1 (codec), Task 3 (SQL), Task 8 (HTTP).
3. **Restauration sans écriture parasite** — sans `confirm`, ou avec un élément verrouillé, ou sur conflit d'unicité IPAM, rien n'est écrit (ni donnée ni événement); un `dry_run` n'écrit rien. Test: Task 17, Task 18, Task 30.
4. **Jamais `approved` en silence** — modification d'un `approved` par un approbateur, ajout d'un lien sur un `approved`, restauration d'un `approved`: l'élément repasse `in_review`, dans la même transaction que l'écriture. Test: Task 26, Task 30.
5. **Migration sur base peuplée** — le rétro-remplissage donne une première ligne d'historique à chaque élément et lien existant, le trigger refuse `UPDATE`/`DELETE`/`TRUNCATE`, la descente défait tout, et la migration d'approbation laisse `draft` ce qui existait. Test: Task 2, Task 15, Task 25.

---

## Carte des fichiers

**Créés (backend)**
- `backend/src/ea/domain/audit.py` — `AuditAction`, `EntityKind`, `AuditChannel`, `AuditEvent`, `FieldChange`, `diff_fields`, vues de lecture (`element_view`…), `AuditFilter`, `AuditPage`, curseur, `aware`.
- `backend/src/ea/domain/baselines.py` — `Baseline`, `ModelSnapshot`, `BoxSnapshot`, `BaselineDiff`, `diff_snapshots`, `RestorePlan`, `plan_restore`.
- `backend/src/ea/domain/approval.py` — `ApprovalState`, `Transition`, `allowed_transitions`, `apply_transition`, `check_modifiable`, `revert_to_review`.
- `backend/src/ea/db/models/audit.py`, `backend/src/ea/db/models/baseline.py` — tables mappées.
- `backend/src/ea/repositories/audit_store.py` — `write_events`, `PostgresAuditRepository`.
- `backend/src/ea/repositories/baseline_store.py` — `PostgresBaselineRepository`.
- `backend/src/ea/services/audit.py` — `AuditTrail`, `AuditService`.
- `backend/src/ea/services/baselines.py` — `BaselineService`.
- `backend/src/ea/api/audit.py`, `backend/src/ea/api/baselines.py` — routeurs.
- `backend/src/ea/baseline_capture.py` — `python -m ea.baseline_capture` (lot 4).
- `backend/migrations/versions/0009_audit_events.py`, `0010_baselines.py`, `0011_element_approval.py`.
- Tests: `tests/unit/test_audit_domain.py`, `test_audit_service.py`, `test_audit_writes.py`, `test_baselines_domain.py`, `test_restore_plan.py`, `test_baseline_service.py`, `test_approval.py`, `test_element_lock.py`, `test_baseline_capture.py`; `tests/integration/test_audit_schema.py`, `test_audit_atomicity.py`, `test_baseline_store.py`, `test_baseline_restore.py`, `test_element_approval_schema.py`; `tests/e2e/test_audit_api.py`, `test_baseline_api.py`, `test_approval_api.py`.
- `docs/adr/NNNN-journal-d-audit.md`, `NNNN-baselines-en-copie-normalisee.md`, `NNNN-approbation-et-verrouillage.md`, `docs/audit-retention.md`.

**Créés (frontend)**
- `frontend/src/features/history/{auditLabels.ts,useAuditLog.ts,FieldDiff.vue,HistorySection.vue,ElementHistory.vue}`.
- `frontend/src/features/baselines/{useBaselines.ts,BaselinesSection.vue,RestoreDialog.vue}`.
- `frontend/src/features/elements/{useApproval.ts,ApprovalBanner.vue}`.
- Specs: `tests/{FieldDiff,useAuditLog,HistorySection,ElementHistory,useBaselines,BaselinesSection,RestoreDialog,useApproval,ApprovalBanner}.spec.ts`.

**Modifiés**
- `backend/src/ea/domain/{errors.py,ports.py,model.py,auth.py}`, `backend/src/ea/services/{caller.py,architecture.py,documents.py,diagrams.py}`, `backend/src/ea/repositories/{architecture_store.py,document_store.py,diagram_store.py}`, `backend/src/ea/db/models/{__init__.py,architecture.py}`, `backend/src/ea/api/{schemas.py,errors.py,dependencies.py,auth.py,architecture.py,me.py}`, `backend/src/ea/mcp/{server.py,errors.py}`, `backend/src/ea/main.py`, `backend/src/ea/core/config.py`, `backend/tests/conftest.py`, `backend/tests/unit/{test_service_guards.py,test_mcp_server.py,test_migrations.py}`, `backend/openapi.json`, `Makefile`.
- `frontend/src/router/sections.ts`, `frontend/src/features/elements/ElementDetail.vue`, `frontend/src/lib/me.ts`, `frontend/src/api/schema.d.ts` (généré), `frontend/tests/setup.ts`, `frontend/tests/support/api.ts`.
- `CLAUDE.md` (une phrase, dernière tâche).

---

# Lot 1 — Journal d'audit (B5)

### Task 1: Domaine `audit` (valeurs, diff, vues, curseur)

**Files:**
- Create: `backend/src/ea/domain/audit.py`
- Modify: `backend/src/ea/domain/errors.py` (ajouter à la fin, après la ligne 148)
- Test: `backend/tests/unit/test_audit_domain.py`

**Interfaces:**
- Consumes: `ea.domain.model.Element`, `Relationship` (champs lus: `id`, `element_type`, `name`, `description`, `documentation`, `properties`, `created_at`, `updated_at`, `layer`; `relationship_type`, `source_id`, `target_id`, `source_type`, `target_type`, `access_type`, `directed`).
- Produces (importés par les tâches 2 à 9):
  - `AuditAction` (StrEnum; `.kind -> EntityKind`), `EntityKind`, `AuditChannel`
  - `AuditEvent(id, at, actor_sub, actor_name, action, entity_kind, entity_id, before, after, request_id, channel, correlation=None)` frozen dataclass, `.changes -> tuple[FieldChange, ...]`
  - `FieldChange(field, before, after)`, `diff_fields(before, after, *, ignore=frozenset()) -> tuple[FieldChange, ...]`
  - `element_view(element) -> dict[str, Any]`, `relationship_view(relationship)`, `diagram_view(diagram)`, `layout_view(nodes)`, `document_view(document)`
  - `AuditFilter`, `AuditPage(events, next_cursor)`, `encode_cursor(at, event_id) -> str`, `decode_cursor(cursor) -> tuple[datetime, UUID]`, `aware(moment) -> datetime | None`, `MAX_AUDIT_PAGE = 200`, `DEFAULT_AUDIT_PAGE = 50`
  - `errors.InvalidCursorError(DomainError)`

- [ ] **Step 1: Écrire le test qui échoue**

```python
# backend/tests/unit/test_audit_domain.py
"""What the audit trail is made of, without a database (docs/adr/NNNN)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.audit import (
    AuditAction,
    EntityKind,
    FieldChange,
    aware,
    decode_cursor,
    diff_fields,
    document_view,
    element_view,
    encode_cursor,
    layout_view,
)
from ea.domain.diagrams import DiagramNode
from ea.domain.documents import Document
from ea.domain.errors import InvalidCursorError
from ea.domain.model import Element

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(**overrides: object) -> Element:
    return Element.create(
        element_type=E.APPLICATION_COMPONENT,
        name=str(overrides.pop("name", "Billing")),
        now=NOW,
        **overrides,  # type: ignore[arg-type]
    )


class TestDiffFields:
    def test_it_lists_only_what_changed_sorted_by_field(self) -> None:
        before = {"name": "A", "description": "x", "layer": "application"}
        after = {"name": "B", "description": "x", "layer": "application"}

        assert diff_fields(before, after) == (FieldChange("name", "A", "B"),)

    def test_it_walks_into_properties_so_one_attribute_is_one_change(self) -> None:
        before = {"properties": {"owner": "ops", "tier": "1"}}
        after = {"properties": {"owner": "dev", "tier": "1", "cost": "9"}}

        assert diff_fields(before, after) == (
            FieldChange("properties.cost", None, "9"),
            FieldChange("properties.owner", "ops", "dev"),
        )

    def test_a_creation_has_everything_as_new_and_a_deletion_as_gone(self) -> None:
        assert diff_fields(None, {"name": "A"}) == (FieldChange("name", None, "A"),)
        assert diff_fields({"name": "A"}, None) == (FieldChange("name", "A", None),)

    def test_it_can_leave_noise_out(self) -> None:
        changes = diff_fields(
            {"updated_at": "1", "name": "A"},
            {"updated_at": "2", "name": "A"},
            ignore=frozenset({"updated_at"}),
        )

        assert changes == ()


class TestTheViews:
    def test_an_element_view_is_the_read_representation_and_json_safe(self) -> None:
        element = an_element(properties={"owner": "ops"})

        view = element_view(element)

        assert view["id"] == str(element.id)
        assert view["element_type"] == "application_component"
        assert view["layer"] == "application"
        assert view["properties"] == {"owner": "ops"}
        assert view["created_at"] == NOW.isoformat()

    def test_a_document_view_keeps_size_and_digest_and_never_the_text(self) -> None:
        document = Document.create(
            element_id=uuid4(), filename="runbook.md", content="# Secret plan\n", now=NOW
        )

        view = document_view(document)

        assert "Secret" not in str(view)
        assert view["byte_size"] == len("# Secret plan\n".encode())
        assert len(view["sha256"]) == 64

    def test_a_layout_view_is_ordered_so_two_equal_layouts_compare_equal(self) -> None:
        first, second = uuid4(), uuid4()
        nodes = [DiagramNode(second, 1, 2), DiagramNode(first, 3, 4)]

        assert layout_view(nodes) == layout_view(list(reversed(nodes)))


class TestActions:
    def test_an_action_knows_the_kind_of_entity_it_is_about(self) -> None:
        assert AuditAction.ELEMENT_CREATED.kind is EntityKind.ELEMENT
        assert AuditAction.DOCUMENT_ATTACHED.kind is EntityKind.DOCUMENT
        assert AuditAction.DIAGRAM_LAYOUT_REPLACED.kind is EntityKind.DIAGRAM


class TestTheCursor:
    def test_it_round_trips(self) -> None:
        event_id = uuid4()

        assert decode_cursor(encode_cursor(NOW, event_id)) == (NOW, event_id)

    @pytest.mark.parametrize("forged", ["", "not-base64!", "e30", "WyJ4Il0", "W10"])
    def test_a_forged_cursor_is_a_refusal_not_a_crash(self, forged: str) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor(forged)

    def test_a_naive_moment_is_read_as_utc(self) -> None:
        assert aware(datetime(2026, 1, 1, 0, 0)) == datetime(2026, 1, 1, tzinfo=UTC)
        assert aware(None) is None
```

- [ ] **Step 2: Lancer le test, vérifier l'échec attendu**

Run: `cd backend && uv run pytest tests/unit/test_audit_domain.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.domain.audit'`.

- [ ] **Step 3: Ajouter l'erreur de domaine**

À la fin de `backend/src/ea/domain/errors.py`:

```python


class InvalidCursorError(DomainError):
    """A pagination cursor this server did not issue — forged, truncated, or from elsewhere.

    A refusal and not a crash: the cursor travels in a URL, and anyone can edit one.
    """
```

- [ ] **Step 4: Implémenter le module**

```python
# backend/src/ea/domain/audit.py
"""What the catalogue remembers about a change — see docs/adr/NNNN.

Pure: no framework, no driver. An `AuditEvent` is one *use case*, not one SQL
row: deleting an element is one event whose `before` lists what the foreign
keys took with it. `services/` builds the events, the repositories insert them
in the transaction of the write itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from ea.domain.errors import InvalidCursorError
from ea.domain.model import Element, Relationship

if TYPE_CHECKING:
    # Only for annotations: `diagrams` imports `ports`, which imports this module.
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.domain.documents import Document

MAX_AUDIT_PAGE: Final = 200
DEFAULT_AUDIT_PAGE: Final = 50


class EntityKind(StrEnum):
    ELEMENT = "element"
    RELATIONSHIP = "relationship"
    DIAGRAM = "diagram"
    DOCUMENT = "document"


class AuditChannel(StrEnum):
    """Which door the caller came through — said by the adapter, never by the caller."""

    HTTP = "http"
    MCP = "mcp"
    SYSTEM = "system"


class AuditAction(StrEnum):
    """The closed list of things the trail can say. Never a free string."""

    ELEMENT_CREATED = "element.created"
    ELEMENT_UPDATED = "element.updated"
    ELEMENT_DELETED = "element.deleted"
    RELATIONSHIP_CREATED = "relationship.created"
    RELATIONSHIP_DELETED = "relationship.deleted"
    DIAGRAM_CREATED = "diagram.created"
    DIAGRAM_UPDATED = "diagram.updated"
    DIAGRAM_DELETED = "diagram.deleted"
    DIAGRAM_LAYOUT_REPLACED = "diagram.layout_replaced"
    DOCUMENT_ATTACHED = "document.attached"
    DOCUMENT_REVISED = "document.revised"
    DOCUMENT_DISCARDED = "document.discarded"

    @property
    def kind(self) -> EntityKind:
        return EntityKind(self.value.split(".", 1)[0])


@dataclass(frozen=True, slots=True)
class FieldChange:
    field: str
    before: Any
    after: Any


def _flatten(value: Mapping[str, Any] | None, prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, item in (value or {}).items():
        path = f"{prefix}{key}"
        if isinstance(item, Mapping) and item:
            flat.update(_flatten(item, f"{path}."))
        else:
            flat[path] = item
    return flat


def diff_fields(
    before: Mapping[str, Any] | None,
    after: Mapping[str, Any] | None,
    *,
    ignore: frozenset[str] = frozenset(),
) -> tuple[FieldChange, ...]:
    """The fields that differ, sorted by name; nested maps are walked (`properties.owner`)."""
    old, new = _flatten(before), _flatten(after)
    return tuple(
        FieldChange(field, old.get(field), new.get(field))
        for field in sorted(old.keys() | new.keys())
        if field not in ignore and old.get(field) != new.get(field)
    )


@dataclass(frozen=True, slots=True)
class AuditEvent:
    id: UUID
    at: datetime
    actor_sub: str
    actor_name: str
    action: AuditAction
    entity_kind: EntityKind
    entity_id: UUID | None
    before: Mapping[str, Any] | None
    after: Mapping[str, Any] | None
    request_id: str
    channel: AuditChannel
    correlation: str | None = None

    @property
    def changes(self) -> tuple[FieldChange, ...]:
        # `updated_at` moves on every write: it is noise in a diff.
        return diff_fields(self.before, self.after, ignore=frozenset({"updated_at"}))


# --- Read representations: what `before` and `after` hold ------------------


def element_view(element: Element) -> dict[str, Any]:
    return {
        "id": str(element.id),
        "element_type": element.element_type.value,
        "layer": element.layer,
        "aspect": element.element_type.aspect.value,
        "name": element.name,
        "description": element.description,
        "documentation": element.documentation,
        "properties": dict(element.properties),
        "created_at": element.created_at.isoformat(),
        "updated_at": element.updated_at.isoformat(),
    }


def relationship_view(relationship: Relationship) -> dict[str, Any]:
    return {
        "id": str(relationship.id),
        "relationship_type": relationship.relationship_type.value,
        "source_id": str(relationship.source_id),
        "target_id": str(relationship.target_id),
        "source_type": relationship.source_type.value,
        "target_type": relationship.target_type.value,
        "name": relationship.name,
        "access_type": relationship.access_type.value if relationship.access_type else None,
        "directed": relationship.directed,
        "properties": dict(relationship.properties),
        "created_at": relationship.created_at.isoformat(),
    }


def diagram_view(diagram: Diagram) -> dict[str, Any]:
    return {"id": str(diagram.id), "name": diagram.name, "description": diagram.description}


def layout_view(nodes: Sequence[DiagramNode]) -> dict[str, Any]:
    ordered = sorted(nodes, key=lambda node: str(node.element_id))
    return {
        "nodes": [
            {
                "element_id": str(node.element_id),
                "x": node.x,
                "y": node.y,
                "width": node.width,
                "height": node.height,
            }
            for node in ordered
        ]
    }


def document_view(document: Document) -> dict[str, Any]:
    """Name, size and digest — never the text: a log is not a second copy of a runbook."""
    raw = document.content.encode()
    return {
        "id": str(document.id),
        "element_id": str(document.element_id),
        "filename": document.filename,
        "byte_size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


# --- Reading the trail -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuditFilter:
    """All criteria combine with AND. `since` is inclusive, `until` exclusive."""

    entity_kind: EntityKind | None = None
    entity_id: UUID | None = None
    actor: str | None = None
    action: AuditAction | None = None
    since: datetime | None = None
    until: datetime | None = None
    request_id: str | None = None
    cursor: str | None = None
    limit: int = DEFAULT_AUDIT_PAGE


@dataclass(frozen=True, slots=True)
class AuditPage:
    events: tuple[AuditEvent, ...]
    next_cursor: str | None


def _utc(moment: datetime) -> datetime:
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def aware(moment: datetime | None) -> datetime | None:
    """A moment typed without a zone is read as UTC; comparing it to a `timestamptz` would crash."""
    return None if moment is None else _utc(moment)


def encode_cursor(at: datetime, event_id: UUID) -> str:
    raw = json.dumps([at.isoformat(), str(event_id)]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        at, event_id = json.loads(base64.urlsafe_b64decode(padded))
        return _utc(datetime.fromisoformat(at)), UUID(event_id)
    except (ValueError, TypeError, binascii.Error, AttributeError) as error:
        msg = "the cursor is not one this server issued"
        raise InvalidCursorError(msg) from error
```

- [ ] **Step 5: Relancer, vérifier le succès**

Run: `cd backend && uv run pytest tests/unit/test_audit_domain.py -x`
Expected: PASS (11 tests). Puis `cd backend && uv run mypy src/ea/domain/audit.py` — Expected: `Success`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain/audit.py backend/src/ea/domain/errors.py backend/tests/unit/test_audit_domain.py
git commit -m "feat(audit): pure audit domain with field diff, read views and cursor" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Table `audit_events`, migration 0009 (trigger, rétro-remplissage)

**Files:**
- Create: `backend/src/ea/db/models/audit.py`, `backend/migrations/versions/0009_audit_events.py`
- Modify: `backend/src/ea/db/models/__init__.py` (lignes 12-27: import + `__all__`)
- Test: `backend/tests/unit/test_migrations.py` (ajout en fin de fichier), `backend/tests/integration/test_audit_schema.py`

**Interfaces:**
- Consumes: `ea.db.base.Base`.
- Produces: `AuditEventRecord` (colonnes de la table, ci-dessous) importée par Task 3.

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à la fin de `backend/tests/unit/test_migrations.py`:

```python
def test_the_audit_table_names_its_entity_without_a_foreign_key() -> None:
    """An event must outlive what it describes (docs/adr/NNNN)."""
    table = Base.metadata.tables["audit_events"]

    assert not table.foreign_keys
    assert table.c.entity_id.nullable is True
    assert table.c.request_id.nullable is False
    assert table.c.channel.nullable is False
    assert {index.name for index in table.indexes} == {
        "ix_audit_events_entity",
        "ix_audit_events_at",
        "ix_audit_events_actor_sub",
    }
```

```python
# backend/tests/integration/test_audit_schema.py
"""The audit table as the server holds it: append-only, and born with a history."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.domain.archimate import ElementType as E

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)

INSERT_EVENT = text(
    "INSERT INTO audit_events (id, at, actor_sub, actor_name, action, entity_kind, "
    "request_id, channel) VALUES (:id, :at, 'a', 'a', 'element.created', 'element', '-', 'http')"
)


async def test_a_stored_event_cannot_be_changed_removed_or_truncated(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        await connection.execute(INSERT_EVENT, {"id": uuid4(), "at": AT})

    for statement in (
        "UPDATE audit_events SET actor_name = 'someone else'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ):
        with pytest.raises(DBAPIError) as raised:
            async with engine_at_head.begin() as connection:
                # Static statements, no runtime value: the three ways to erase a trace.
                await connection.execute(text(statement))
        assert "append-only" in str(raised.value)

    async with engine_at_head.connect() as connection:
        remaining = (await connection.execute(text("SELECT count(*) FROM audit_events"))).scalar_one()
    assert remaining == 1


async def test_the_migration_gives_an_existing_catalogue_a_first_line_of_history(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    element_id, other_id, link_id = uuid4(), uuid4(), uuid4()
    await asyncio.to_thread(command.upgrade, alembic_config, "0008")
    try:
        async with postgres_engine.begin() as connection:
            for identifier, name in ((element_id, "Billing"), (other_id, "db-01")):
                await connection.execute(
                    text(
                        "INSERT INTO elements (id, element_type, layer, aspect, name, "
                        "created_at, updated_at) VALUES (:id, :type, :layer, :aspect, :name, :at, :at)"
                    ),
                    {
                        "id": identifier,
                        "type": E.NODE.value,
                        "layer": E.NODE.layer.value,
                        "aspect": E.NODE.aspect.value,
                        "name": name,
                        "at": AT,
                    },
                )
            await connection.execute(
                text(
                    "INSERT INTO relationships (id, relationship_type, source_id, target_id, "
                    "source_type, target_type, created_at) VALUES "
                    "(:id, 'association', :s, :t, 'node', 'node', :at)"
                ),
                {"id": link_id, "s": element_id, "t": other_id, "at": AT},
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0009")

        async with postgres_engine.connect() as connection:
            rows = (
                await connection.execute(
                    text(
                        "SELECT action, entity_id, actor_sub, correlation, at, after "
                        "FROM audit_events ORDER BY action, entity_id"
                    )
                )
            ).all()
        assert [row[0] for row in rows] == ["element.created"] * 2 + ["relationship.created"]
        assert {row[1] for row in rows} == {element_id, other_id, link_id}
        assert {row[2] for row in rows} == {"ea-system"}
        assert {row[3] for row in rows} == {"backfill"}
        assert {row[4] for row in rows} == {AT}
        assert {row[5]["name"] for row in rows if row[0] == "element.created"} == {
            "Billing",
            "db-01",
        }

        await asyncio.to_thread(command.downgrade, alembic_config, "0008")
        async with postgres_engine.connect() as connection:
            exists = (
                await connection.execute(text("SELECT to_regclass('audit_events')"))
            ).scalar_one()
        assert exists is None
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -x`
Expected: FAIL — `KeyError: 'audit_events'`.

- [ ] **Step 3: Écrire le modèle**

```python
# backend/src/ea/db/models/audit.py
"""The audit trail: one append-only table — see docs/adr/NNNN.

`entity_id` is deliberately not a foreign key: an event has to outlive the
element it describes, which is the whole point of a trail. Immutability is a
trigger created in revision 0009, not a column.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_kind", "entity_id", text("at DESC")),
        Index("ix_audit_events_at", text("at DESC")),
        Index("ix_audit_events_actor_sub", "actor_sub", text("at DESC")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_sub: Mapped[str] = mapped_column(Text, nullable=False)
    actor_name: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False, server_default="system")
    correlation: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Dans `backend/src/ea/db/models/__init__.py`, ajouter l'import (avant `architecture`, ordre alphabétique) et le nom dans `__all__`:

```python
from ea.db.models.audit import AuditEventRecord

__all__ = [
    "AuditEventRecord",
    "Base",
    # … les noms existants, inchangés
]
```

- [ ] **Step 4: Écrire la migration**

```python
# backend/migrations/versions/0009_audit_events.py
"""le journal d'audit

Revision ID: 0009
Revises: 0008
Date: 2026-10-10

Une table append-only: ce que le catalogue sait de ses propres changements.
`entity_id` n'a aucune clé étrangère, volontairement — l'événement doit survivre
à l'élément qu'il décrit. L'immuabilité est un trigger (`UPDATE`, `DELETE` et
`TRUNCATE` lèvent), parce que le déploiement n'a qu'un rôle propriétaire `ea`
(docs/adr/0029) : un `REVOKE` ne lierait personne. Le rôle d'écriture
`INSERT, SELECT` seul est à provisionner hors de ce repo.

Le rétro-remplissage donne à chaque élément et chaque lien existant un premier
événement (`ea-system`, `correlation = 'backfill'`), pour que l'historique ne
commence pas par un trou. Les diagrammes et les documents n'en reçoivent pas.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# DDL statique, aucune valeur d'exécution : rien à lier.
_FUNCTION = """
CREATE FUNCTION audit_events_are_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only' USING ERRCODE = 'restrict_violation';
END
$$
"""
_ROW_TRIGGER = """
CREATE TRIGGER audit_events_no_update_delete BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_are_append_only()
"""
_TRUNCATE_TRIGGER = """
CREATE TRIGGER audit_events_no_truncate BEFORE TRUNCATE ON audit_events
FOR EACH STATEMENT EXECUTE FUNCTION audit_events_are_append_only()
"""

_BACKFILL_ELEMENTS = """
INSERT INTO audit_events
    (id, at, actor_sub, actor_name, action, entity_kind, entity_id, before, after,
     request_id, channel, correlation)
SELECT gen_random_uuid(), e.created_at, 'ea-system', 'ea-system', 'element.created', 'element',
       e.id, NULL,
       jsonb_build_object(
           'id', CAST(e.id AS text), 'element_type', e.element_type, 'layer', e.layer,
           'aspect', e.aspect, 'name', e.name, 'description', e.description,
           'documentation', e.documentation, 'properties', e.properties,
           'created_at', to_jsonb(e.created_at), 'updated_at', to_jsonb(e.updated_at)),
       '-', 'system', 'backfill'
FROM elements e
"""
_BACKFILL_RELATIONSHIPS = """
INSERT INTO audit_events
    (id, at, actor_sub, actor_name, action, entity_kind, entity_id, before, after,
     request_id, channel, correlation)
SELECT gen_random_uuid(), r.created_at, 'ea-system', 'ea-system', 'relationship.created',
       'relationship', r.id, NULL,
       jsonb_build_object(
           'id', CAST(r.id AS text), 'relationship_type', r.relationship_type,
           'source_id', CAST(r.source_id AS text), 'target_id', CAST(r.target_id AS text),
           'source_type', r.source_type, 'target_type', r.target_type, 'name', r.name,
           'access_type', r.access_type, 'directed', r.directed, 'properties', r.properties,
           'created_at', to_jsonb(r.created_at)),
       '-', 'system', 'backfill'
FROM relationships r
"""


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_sub", sa.Text(), nullable=False),
        sa.Column("actor_name", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_kind", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("before", postgresql.JSONB(), nullable=True),
        sa.Column("after", postgresql.JSONB(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), server_default="system", nullable=False),
        sa.Column("correlation", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
    )
    op.create_index(
        "ix_audit_events_entity",
        "audit_events",
        ["entity_kind", "entity_id", sa.text("at DESC")],
    )
    op.create_index("ix_audit_events_at", "audit_events", [sa.text("at DESC")])
    op.create_index(
        "ix_audit_events_actor_sub", "audit_events", ["actor_sub", sa.text("at DESC")]
    )
    # Le rétro-remplissage d'abord : le trigger ne gêne pas un INSERT, mais une
    # migration qui se corrige elle-même doit pouvoir le faire avant d'être scellée.
    op.execute(_BACKFILL_ELEMENTS)
    op.execute(_BACKFILL_RELATIONSHIPS)
    op.execute(_FUNCTION)
    op.execute(_ROW_TRIGGER)
    op.execute(_TRUNCATE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_events_no_truncate ON audit_events")
    op.execute("DROP TRIGGER audit_events_no_update_delete ON audit_events")
    op.execute("DROP FUNCTION audit_events_are_append_only()")
    op.drop_index("ix_audit_events_actor_sub", table_name="audit_events")
    op.drop_index("ix_audit_events_at", table_name="audit_events")
    op.drop_index("ix_audit_events_entity", table_name="audit_events")
    op.drop_table("audit_events")
```

- [ ] **Step 5: Relancer le test unitaire, puis l'intégration**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -x` — Expected: PASS.
Run: `make test-postgres` — Expected: PASS, dont `test_audit_schema.py` (le test d'immuabilité et celui du rétro-remplissage). Si `gen_random_uuid()` n'existe pas (PostgreSQL < 13), l'image jetable est trop vieille: stopper et le signaler, ne pas ajouter d'extension.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/db/models/audit.py backend/src/ea/db/models/__init__.py backend/migrations/versions/0009_audit_events.py backend/tests/unit/test_migrations.py backend/tests/integration/test_audit_schema.py
git commit -m "feat(audit): audit_events table, append-only triggers and backfill (migration 0009)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Dépôt d'audit (`write_events`, lecture paginée) et port

**Files:**
- Create: `backend/src/ea/repositories/audit_store.py`
- Modify: `backend/src/ea/domain/ports.py` (imports lignes 14-21; ajouter `AuditRepository` après `DiagramRepository`, ligne ~227)
- Test: `backend/tests/integration/test_audit_store.py`

**Interfaces:**
- Consumes: `AuditEventRecord` (Task 2), `AuditEvent`, `AuditFilter`, `AuditPage`, `encode_cursor`, `decode_cursor` (Task 1).
- Produces:
  - `write_events(session: AsyncSession, events: Sequence[AuditEvent]) -> None`
  - `PostgresAuditRepository(sessions: async_sessionmaker[AsyncSession])` avec `async page(self, criteria: AuditFilter) -> AuditPage`
  - Port `AuditRepository(Protocol)` dans `ea.domain.ports` avec `async def page(self, criteria: AuditFilter) -> AuditPage`.

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

```python
# backend/tests/integration/test_audit_store.py
"""Writing and reading the trail against a real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.audit import (
    AuditAction,
    AuditChannel,
    AuditEvent,
    AuditFilter,
    EntityKind,
)
from ea.repositories.audit_store import PostgresAuditRepository, write_events

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_event(**overrides: object) -> AuditEvent:
    fields: dict[str, object] = {
        "id": uuid4(),
        "at": T0,
        "actor_sub": "editor-1",
        "actor_name": "editor",
        "action": AuditAction.ELEMENT_CREATED,
        "entity_kind": EntityKind.ELEMENT,
        "entity_id": uuid4(),
        "before": None,
        "after": {"name": "Billing"},
        "request_id": "req-1",
        "channel": AuditChannel.HTTP,
    }
    return AuditEvent(**{**fields, **overrides})  # type: ignore[arg-type]


async def test_an_event_round_trips_and_a_rolled_back_transaction_leaves_none(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresAuditRepository(sessions)
    kept, lost = an_event(), an_event()

    async with sessions.begin() as session:
        write_events(session, [kept])
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            write_events(session, [lost])
            raise RuntimeError

    page = await store.page(AuditFilter())
    assert [event.id for event in page.events] == [kept.id]
    assert page.events[0] == kept


async def test_pagination_has_no_hole_and_no_duplicate_even_when_moments_are_equal(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresAuditRepository(sessions)
    events = [an_event(at=T0 + timedelta(seconds=index // 3)) for index in range(7)]
    async with sessions.begin() as session:
        write_events(session, events)

    seen: list = []
    cursor = None
    while True:
        page = await store.page(AuditFilter(limit=2, cursor=cursor))
        seen.extend(event.id for event in page.events)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert sorted(seen, key=str) == sorted((e.id for e in events), key=str)
    assert len(seen) == len(set(seen)) == 7


async def test_the_filters_combine_and_since_is_inclusive_until_exclusive(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresAuditRepository(sessions)
    subject = uuid4()
    early = an_event(at=T0, entity_id=subject, actor_name="alice", actor_sub="sub-a")
    late = an_event(
        at=T0 + timedelta(hours=1),
        entity_id=subject,
        action=AuditAction.ELEMENT_UPDATED,
        request_id="req-2",
    )
    other = an_event(entity_id=uuid4())
    async with sessions.begin() as session:
        write_events(session, [early, late, other])

    by_entity = await store.page(AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=subject))
    by_actor = await store.page(AuditFilter(actor="alice"))
    by_sub = await store.page(AuditFilter(actor="sub-a"))
    by_action = await store.page(AuditFilter(action=AuditAction.ELEMENT_UPDATED))
    by_request = await store.page(AuditFilter(request_id="req-2"))
    window = await store.page(
        AuditFilter(entity_id=subject, since=T0, until=T0 + timedelta(hours=1))
    )

    assert [e.id for e in by_entity.events] == [late.id, early.id]  # newest first
    assert [e.id for e in by_actor.events] == [early.id]
    assert [e.id for e in by_sub.events] == [early.id]
    assert [e.id for e in by_action.events] == [late.id]
    assert [e.id for e in by_request.events] == [late.id]
    assert [e.id for e in window.events] == [early.id]
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `make test-postgres`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.repositories.audit_store'`.

- [ ] **Step 3: Ajouter le port**

Dans `backend/src/ea/domain/ports.py`, ajouter l'import `from ea.domain.audit import AuditEvent, AuditFilter, AuditPage` (avec les imports `ea.domain.*` existants, lignes 14-21), puis après la classe `DiagramRepository`:

```python
class AuditRepository(Protocol):
    """Reads the audit trail. Writing is not here: an event is inserted by the
    repository that makes the write it describes, in the same transaction
    (docs/adr/NNNN)."""

    async def page(self, criteria: AuditFilter) -> AuditPage:
        """Newest first (`at`, then `id`), at most `criteria.limit` events and a cursor."""
        ...
```

(`AuditEvent` sert aux tâches suivantes; l'importer ici évite d'y revenir.)

- [ ] **Step 4: Implémenter le dépôt**

```python
# backend/src/ea/repositories/audit_store.py
"""The PostgreSQL side of the audit trail — see docs/adr/NNNN.

Two things live here and they are deliberately not one class. `write_events` is
a function over a session that *another* repository already holds open: an
event is inserted in the transaction of the write it describes, so it is there
if the write is committed and gone if it is not. `PostgresAuditRepository` is
the read side and opens its own units of work, like every other store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, or_, select

from ea.db.models.audit import AuditEventRecord
from ea.domain.audit import (
    AuditAction,
    AuditChannel,
    AuditEvent,
    AuditFilter,
    AuditPage,
    EntityKind,
    decode_cursor,
    encode_cursor,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def event_row(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "at": event.at,
        "actor_sub": event.actor_sub,
        "actor_name": event.actor_name,
        "action": event.action.value,
        "entity_kind": event.entity_kind.value,
        "entity_id": event.entity_id,
        "before": dict(event.before) if event.before is not None else None,
        "after": dict(event.after) if event.after is not None else None,
        "request_id": event.request_id,
        "channel": event.channel.value,
        "correlation": event.correlation,
    }


def event_from_row(row: AuditEventRecord) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        at=row.at,
        actor_sub=row.actor_sub,
        actor_name=row.actor_name,
        action=AuditAction(row.action),
        entity_kind=EntityKind(row.entity_kind),
        entity_id=row.entity_id,
        before=row.before,
        after=row.after,
        request_id=row.request_id,
        channel=AuditChannel(row.channel),
        correlation=row.correlation,
    )


def write_events(session: AsyncSession, events: Sequence[AuditEvent]) -> None:
    """Add the events to the unit of work the caller holds open — flushed at its commit.

    No flush here: the caller has already flushed the write itself (that is where
    a refusal surfaces), so an event is only ever added behind a write that held.
    """
    session.add_all([AuditEventRecord(**event_row(event)) for event in events])


def _matching(criteria: AuditFilter) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if criteria.entity_kind is not None:
        clauses.append(AuditEventRecord.entity_kind == criteria.entity_kind.value)
    if criteria.entity_id is not None:
        clauses.append(AuditEventRecord.entity_id == criteria.entity_id)
    if criteria.actor is not None:
        clauses.append(
            or_(
                AuditEventRecord.actor_sub == criteria.actor,
                AuditEventRecord.actor_name == criteria.actor,
            )
        )
    if criteria.action is not None:
        clauses.append(AuditEventRecord.action == criteria.action.value)
    if criteria.since is not None:
        clauses.append(AuditEventRecord.at >= criteria.since)
    if criteria.until is not None:
        clauses.append(AuditEventRecord.at < criteria.until)
    if criteria.request_id is not None:
        clauses.append(AuditEventRecord.request_id == criteria.request_id)
    if criteria.cursor is not None:
        at, event_id = decode_cursor(criteria.cursor)
        clauses.append(
            or_(
                AuditEventRecord.at < at,
                and_(AuditEventRecord.at == at, AuditEventRecord.id < event_id),
            )
        )
    return clauses


class PostgresAuditRepository:
    """`AuditRepository` over the append-only table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def page(self, criteria: AuditFilter) -> AuditPage:
        statement = (
            select(AuditEventRecord)
            .where(*_matching(criteria))
            .order_by(AuditEventRecord.at.desc(), AuditEventRecord.id.desc())
            .limit(criteria.limit + 1)
        )
        async with self._sessions() as session:
            rows = list((await session.scalars(statement)).all())
        shown = rows[: criteria.limit]
        events = tuple(event_from_row(row) for row in shown)
        more = len(rows) > criteria.limit
        return AuditPage(
            events=events,
            next_cursor=encode_cursor(shown[-1].at, shown[-1].id) if more else None,
        )
```

- [ ] **Step 5: Relancer, vérifier le succès**

Run: `make test-postgres` — Expected: PASS. Puis `cd backend && uv run mypy src/ea/repositories/audit_store.py src/ea/domain/ports.py` — Expected: `Success`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/repositories/audit_store.py backend/src/ea/domain/ports.py backend/tests/integration/test_audit_store.py
git commit -m "feat(audit): write_events in the caller's transaction and a keyset-paged reader" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `AuditTrail`, `AuditService`, canal, double mémoire

**Files:**
- Create: `backend/src/ea/services/audit.py`, `backend/tests/unit/test_audit_service.py`
- Modify: `backend/src/ea/services/caller.py` (ajouts après `current_caller`, ligne 22; et après `acting_as`), `backend/tests/conftest.py` (imports lignes 14-43; nouvelle classe avant `InMemoryRepository`, ligne ~248; fixtures lignes 771-785), `backend/tests/unit/test_service_guards.py` (dict `WRITES`, ligne 29)

**Interfaces:**
- Consumes: Task 1 (`AuditEvent`…), `ea.core.logging.request_id` (ContextVar, `NO_REQUEST="-"`), `require_caller`.
- Produces:
  - `services/caller.py`: `current_channel: ContextVar[AuditChannel]` (défaut `AuditChannel.SYSTEM`), `acting_through(channel) -> ContextManager[AuditChannel]`
  - `services/audit.py`: `AuditTrail(clock: Callable[[], datetime])` avec `event(action, *, entity_id, before=None, after=None, correlation=None) -> AuditEvent`; `AuditService(repository: AuditRepository)` avec `async list_events(criteria: AuditFilter) -> AuditPage` (lecture)
  - `tests/conftest.py`: `InMemoryAuditLog` (`events: list[AuditEvent]`, `append(events)`, `async page(criteria)`), fixture `audit_log`

- [ ] **Step 1: Écrire le test qui échoue**

```python
# backend/tests/unit/test_audit_service.py
"""Who, when, through which door — and who may read the trail."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.core.logging import request_id
from ea.domain.audit import AuditAction, AuditChannel, AuditFilter, EntityKind
from ea.domain.errors import NotAuthenticatedError
from ea.services.audit import AuditService, AuditTrail
from ea.services.caller import acting_through
from tests.conftest import FIXED_NOW, InMemoryAuditLog


class TestTheTrail:
    def test_an_event_carries_the_caller_the_clock_the_request_and_the_channel(self) -> None:
        trail = AuditTrail(lambda: FIXED_NOW)
        entity = uuid4()
        token = request_id.set("req-42")
        try:
            with acting_through(AuditChannel.MCP):
                event = trail.event(
                    AuditAction.ELEMENT_CREATED, entity_id=entity, after={"name": "A"}
                )
        finally:
            request_id.reset(token)

        assert (event.actor_sub, event.actor_name) == ("editor-1", "editor")
        assert event.at == FIXED_NOW
        assert event.request_id == "req-42"
        assert event.channel is AuditChannel.MCP
        assert event.entity_kind is EntityKind.ELEMENT
        assert event.entity_id == entity

    def test_outside_a_request_the_id_is_a_dash_and_the_channel_is_the_system(self) -> None:
        event = AuditTrail(lambda: FIXED_NOW).event(AuditAction.ELEMENT_DELETED, entity_id=None)

        assert event.request_id == "-"
        assert event.channel is AuditChannel.SYSTEM

    @pytest.mark.usefixtures("nobody_calling")
    def test_nobody_calling_means_no_event_at_all(self) -> None:
        with pytest.raises(NotAuthenticatedError):
            AuditTrail(lambda: FIXED_NOW).event(AuditAction.ELEMENT_CREATED, entity_id=None)


@pytest.mark.asyncio
class TestReadingTheTrail:
    async def test_any_authenticated_caller_may_read(self, audit_log: InMemoryAuditLog) -> None:
        service = AuditService(audit_log)
        audit_log.append(
            [AuditTrail(lambda: FIXED_NOW).event(AuditAction.ELEMENT_CREATED, entity_id=None)]
        )

        page = await service.list_events(AuditFilter())

        assert len(page.events) == 1

    @pytest.mark.usefixtures("nobody_calling")
    async def test_an_anonymous_reader_is_refused(self, audit_log: InMemoryAuditLog) -> None:
        with pytest.raises(NotAuthenticatedError):
            await AuditService(audit_log).list_events(AuditFilter())

    async def test_a_page_larger_than_the_bound_is_refused(
        self, audit_log: InMemoryAuditLog
    ) -> None:
        with pytest.raises(ValueError, match="at most 200"):
            await AuditService(audit_log).list_events(AuditFilter(limit=201))
        with pytest.raises(ValueError, match="at least 1"):
            await AuditService(audit_log).list_events(AuditFilter(limit=0))
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_audit_service.py -x`
Expected: FAIL — `ImportError: cannot import name 'InMemoryAuditLog' from 'tests.conftest'`.

- [ ] **Step 3: Ajouter le canal à `services/caller.py`**

Imports: ajouter `from ea.domain.audit import AuditChannel`. Après la ligne `current_caller: ContextVar[...] = ...`:

```python
#: Which door the current call came through. The adapter that knows sets it —
#: `api/auth.py` (http), `speaking_plainly` (mcp); a script is the system.
current_channel: ContextVar[AuditChannel] = ContextVar(
    "ea_current_channel", default=AuditChannel.SYSTEM
)
```

À la fin du fichier:

```python


@contextmanager
def acting_through(channel: AuditChannel) -> Iterator[AuditChannel]:
    token = current_channel.set(channel)
    try:
        yield channel
    finally:
        current_channel.reset(token)
```

- [ ] **Step 4: Implémenter `services/audit.py`**

```python
# backend/src/ea/services/audit.py
"""Building audit events, and reading the trail — see docs/adr/NNNN.

`AuditTrail` is the one place an `AuditEvent` is made: the actor comes from the
verified caller and never from a parameter, the moment from the service's
injected clock, the request from the logging context, the door from the
adapter. A use case says *what* happened; the repository inserts the event in
the same transaction as the write.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ea.core.logging import request_id
from ea.domain.audit import MAX_AUDIT_PAGE, AuditAction, AuditEvent, AuditFilter, AuditPage
from ea.services.caller import current_channel, require_caller

if TYPE_CHECKING:
    from ea.domain.ports import AuditRepository

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditTrail:
    """Makes the events of one service; holds no state but the clock."""

    def __init__(self, clock: Clock = _utc_now) -> None:
        self._now = clock

    def event(
        self,
        action: AuditAction,
        *,
        entity_id: UUID | None,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
        correlation: str | None = None,
    ) -> AuditEvent:
        caller = require_caller()
        return AuditEvent(
            id=uuid4(),
            at=self._now(),
            actor_sub=caller.subject,
            actor_name=caller.username,
            action=action,
            entity_kind=action.kind,
            entity_id=entity_id,
            before=before,
            after=after,
            request_id=request_id.get(),
            channel=current_channel.get(),
            correlation=correlation,
        )


class AuditService:
    """The single entry point `api/` and `mcp/` use to read the trail."""

    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def list_events(self, criteria: AuditFilter) -> AuditPage:
        """Any authenticated caller reads (spec §8, R7: restricting it is one guard)."""
        require_caller()
        if criteria.limit < 1:
            msg = "a page has at least 1 event"
            raise ValueError(msg)
        if criteria.limit > MAX_AUDIT_PAGE:
            msg = f"a page has at most {MAX_AUDIT_PAGE} events"
            raise ValueError(msg)
        return await self._repository.page(criteria)
```

- [ ] **Step 5: Double mémoire et fixtures dans `tests/conftest.py`**

Ajouter aux imports du fichier: `from ea.domain.audit import AuditEvent, AuditFilter, AuditPage, decode_cursor, encode_cursor`. Insérer juste avant `class InMemoryRepository:`:

```python
class InMemoryAuditLog:
    """The `audit_events` table: append-only, and written only by a write that succeeded.

    The three repository doubles share one of these (the `audit_log` fixture), so
    a service test asserts on what a real transaction would have committed: a
    refused write appends nothing, because the double raises before it does.
    """

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, events: Sequence[AuditEvent]) -> None:
        self.events.extend(events)

    async def page(self, criteria: AuditFilter) -> AuditPage:
        def matches(event: AuditEvent) -> bool:
            return all(
                [
                    criteria.entity_kind is None or event.entity_kind == criteria.entity_kind,
                    criteria.entity_id is None or event.entity_id == criteria.entity_id,
                    criteria.actor is None or criteria.actor in (event.actor_sub, event.actor_name),
                    criteria.action is None or event.action == criteria.action,
                    criteria.since is None or event.at >= criteria.since,
                    criteria.until is None or event.at < criteria.until,
                    criteria.request_id is None or event.request_id == criteria.request_id,
                ]
            )

        ordered = sorted(
            (event for event in self.events if matches(event)),
            key=lambda event: (event.at, event.id.int),
            reverse=True,
        )
        if criteria.cursor is not None:
            at, event_id = decode_cursor(criteria.cursor)
            ordered = [e for e in ordered if (e.at, e.id.int) < (at, event_id.int)]
        shown = ordered[: criteria.limit]
        more = len(ordered) > criteria.limit
        return AuditPage(
            events=tuple(shown),
            next_cursor=encode_cursor(shown[-1].at, shown[-1].id) if more else None,
        )
```

Remplacer les trois fixtures `repository`, `documents`, `diagrams` (lignes ~771-780) et ajouter `audit_log`:

```python
@pytest.fixture
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def repository(audit_log: InMemoryAuditLog) -> InMemoryRepository:
    return InMemoryRepository(audit_log)


@pytest.fixture
def documents(audit_log: InMemoryAuditLog) -> InMemoryDocuments:
    return InMemoryDocuments(audit_log)


@pytest.fixture
def diagrams(audit_log: InMemoryAuditLog) -> InMemoryDiagrams:
    return InMemoryDiagrams(audit_log)
```

(Les constructeurs des trois doubles acceptent `audit_log` à la Task 5 et 7; en attendant, `InMemoryRepository.__init__` ne le prend pas encore — Step 6 ci-dessous le fait pour que ce commit reste vert.)

- [ ] **Step 6: Faire accepter `audit_log` aux trois doubles sans changer leur comportement**

Dans `InMemoryRepository.__init__` (ligne ~263), `InMemoryDocuments.__init__` et `InMemoryDiagrams.__init__`, le même changement d'en-tête (le reste du corps est inchangé):

```python
    def __init__(self, audit_log: InMemoryAuditLog | None = None) -> None:
        self.audit_log = audit_log if audit_log is not None else InMemoryAuditLog()
        # … les attributs que le constructeur posait déjà, inchangés
```

- [ ] **Step 7: Mettre à jour la garde AST**

Dans `backend/tests/unit/test_service_guards.py`: ajouter `from ea.services.audit import AuditService` et, dans `WRITES`, la ligne `AuditService: set(),` (toutes ses méthodes publiques sont des lectures: `require_caller`).

- [ ] **Step 8: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_audit_service.py tests/unit/test_service_guards.py tests/unit/test_architecture_service.py -x`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/ea/services/audit.py backend/src/ea/services/caller.py backend/tests/conftest.py backend/tests/unit/test_audit_service.py backend/tests/unit/test_service_guards.py
git commit -m "feat(audit): audit trail builder, read service and request channel" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Dépôt du graphe — paramètre `audit`, `attachments_of`, atomicité

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (classe `ArchitectureRepository`, lignes 54-92; ajouter `Attachments` après `ElementFilter`, ligne ~50), `backend/src/ea/repositories/architecture_store.py` (imports ligne 36-41; méthodes lignes 244-337), `backend/tests/conftest.py` (`InMemoryRepository`, méthodes lignes ~270-345)
- Test: `backend/tests/integration/test_audit_atomicity.py`

**Interfaces:**
- Consumes: `write_events` (Task 3), `InMemoryAuditLog` (Task 4).
- Produces (nouvelles signatures, `audit` keyword-only avec défaut `()`):
  - `add_element(element, *, audit=()) -> Element`; `save_element(element, *, audit=()) -> Element`; `delete_element(element_id, *, audit=()) -> bool` (n'écrit les événements que si une ligne est supprimée); `add_relationship(relationship, *, audit=()) -> Relationship`; `delete_relationship(relationship_id, *, audit=()) -> bool`
  - `Attachments(document_ids: tuple[UUID, ...] = (), diagram_ids: tuple[UUID, ...] = ())` et `async attachments_of(element_id) -> Attachments`
  - **Point d'accroche pour la session partagée du sous-projet 3**: ces méthodes ouvrent leur `begin()`; aucune ne reçoit de session. Ne pas en ajouter ici.

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

```python
# backend/tests/integration/test_audit_atomicity.py
"""An event is in the database if and only if the write it describes is."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, AuditFilter, EntityKind
from ea.domain.errors import DuplicateElementError, ElementNotFoundError
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def event_for(action: AuditAction, entity_id: object) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=NOW,
        actor_sub="editor-1",
        actor_name="editor",
        action=action,
        entity_kind=action.kind,
        entity_id=entity_id,  # type: ignore[arg-type]
        before=None,
        after={"x": 1},
        request_id="req",
        channel=AuditChannel.HTTP,
    )


def an_element(name: str) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW)


async def test_a_refused_write_leaves_no_event_and_a_deletion_leaves_its_trace(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    first, clone = an_element("db-01"), an_element("db-01")

    await graph.add_element(first, audit=(event_for(AuditAction.ELEMENT_CREATED, first.id),))
    with pytest.raises(DuplicateElementError):
        await graph.add_element(clone, audit=(event_for(AuditAction.ELEMENT_CREATED, clone.id),))
    ghost = Relationship.between(R.ASSOCIATION, first, an_element("other"), now=NOW)
    with pytest.raises(ElementNotFoundError):
        await graph.add_relationship(
            ghost, audit=(event_for(AuditAction.RELATIONSHIP_CREATED, ghost.id),)
        )
    assert (
        await graph.delete_element(
            uuid4(), audit=(event_for(AuditAction.ELEMENT_DELETED, uuid4()),)
        )
        is False
    )

    only_the_first = await trail.page(AuditFilter())
    assert [e.entity_id for e in only_the_first.events] == [first.id]

    assert await graph.delete_element(
        first.id, audit=(event_for(AuditAction.ELEMENT_DELETED, first.id),)
    )
    history = await trail.page(AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=first.id))
    assert [e.action for e in history.events] == [
        AuditAction.ELEMENT_DELETED,
        AuditAction.ELEMENT_CREATED,
    ]
    assert await graph.get_element(first.id) is None  # the element is gone, its story is not


async def test_a_save_and_a_link_write_their_events_with_them(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    left, right = an_element("a"), an_element("b")
    await graph.add_element(left)
    await graph.add_element(right)

    renamed = left.rename("a2", now=NOW)
    await graph.save_element(renamed, audit=(event_for(AuditAction.ELEMENT_UPDATED, left.id),))
    link = Relationship.between(R.ASSOCIATION, renamed, right, now=NOW)
    await graph.add_relationship(
        link, audit=(event_for(AuditAction.RELATIONSHIP_CREATED, link.id),)
    )
    await graph.delete_relationship(
        link.id, audit=(event_for(AuditAction.RELATIONSHIP_DELETED, link.id),)
    )

    actions = [e.action for e in (await trail.page(AuditFilter())).events]
    assert sorted(a.value for a in actions) == [
        "element.updated",
        "relationship.created",
        "relationship.deleted",
    ]


async def test_attachments_of_names_what_the_foreign_keys_would_take(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.domain.documents import Document
    from ea.repositories.diagram_store import PostgresDiagramRepository
    from ea.repositories.document_store import PostgresDocumentRepository

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    element = await graph.add_element(an_element("db-01"))
    document = await PostgresDocumentRepository(sessions).add(
        Document.create(element_id=element.id, filename="a.md", content="# A\n", now=NOW)
    )
    diagrams = PostgresDiagramRepository(sessions)
    diagram = await diagrams.add(Diagram.create(name="Vue", now=NOW))
    await diagrams.replace_layout(diagram.id, [DiagramNode(element.id, 0, 0)], now=NOW)

    attached = await graph.attachments_of(element.id)

    assert attached.document_ids == (document.id,)
    assert attached.diagram_ids == (diagram.id,)
    assert (await graph.attachments_of(uuid4())).document_ids == ()
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `make test-postgres`
Expected: FAIL — `TypeError: add_element() got an unexpected keyword argument 'audit'`.

- [ ] **Step 3: Étendre le port**

Dans `backend/src/ea/domain/ports.py`, après `ElementFilter`:

```python
@dataclass(frozen=True, slots=True)
class Attachments:
    """What the foreign keys of revision 0006 take with an element, besides its links."""

    document_ids: tuple[UUID, ...] = ()
    diagram_ids: tuple[UUID, ...] = ()
```

Dans `ArchitectureRepository`, remplacer les cinq signatures d'écriture:

```python
    async def add_element(self, element: Element, *, audit: Sequence[AuditEvent] = ()) -> Element:
        """Store a new element; `audit` is inserted in the same transaction."""
        ...

    async def save_element(self, element: Element, *, audit: Sequence[AuditEvent] = ()) -> Element:
        """Persist an already-existing element, overwriting its stored form."""
        ...

    async def delete_element(self, element_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        """Remove an element and every relationship attached to it.

        `audit` is written only if a row was deleted; `False` writes nothing.
        """
        ...

    async def add_relationship(
        self, relationship: Relationship, *, audit: Sequence[AuditEvent] = ()
    ) -> Relationship: ...

    async def delete_relationship(
        self, relationship_id: UUID, *, audit: Sequence[AuditEvent] = ()
    ) -> bool: ...

    async def attachments_of(self, element_id: UUID) -> Attachments:
        """The documents and diagrams that follow this element when it is deleted."""
        ...
```

- [ ] **Step 4: Implémenter dans `architecture_store.py`**

Imports à ajouter: `from ea.db.models.diagram import DiagramNodeRecord`, `from ea.db.models.document import ElementDocument`, `from ea.domain.ports import Attachments, ElementFilter, GraphView` (remplace l'import existant), `from ea.repositories.audit_store import write_events`; sous `TYPE_CHECKING`: `from ea.domain.audit import AuditEvent`. Remplacer les cinq méthodes d'écriture:

```python
    async def add_element(self, element: Element, *, audit: Sequence[AuditEvent] = ()) -> Element:
        async with self._sessions.begin() as session:
            session.add(ElementRecord(**element_row(element)))
            await _flush_refusing(session, element)
            write_events(session, audit)
        return element

    async def save_element(self, element: Element, *, audit: Sequence[AuditEvent] = ()) -> Element:
        async with self._sessions.begin() as session:
            row = await session.get(ElementRecord, element.id)
            if row is None:
                msg = f"no element with id {element.id}"
                raise ElementNotFoundError(msg)
            for column, value in element_row(element).items():
                setattr(row, column, value)
            await _flush_refusing(session, element)
            write_events(session, audit)
        return element

    async def delete_element(self, element_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        """Its relationships follow by the foreign keys."""
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementRecord).where(ElementRecord.id == element_id)
            )
            if not _rows_affected(deleted):
                return False
            write_events(session, audit)
        return True

    async def add_relationship(
        self, relationship: Relationship, *, audit: Sequence[AuditEvent] = ()
    ) -> Relationship:
        async with self._sessions.begin() as session:
            session.add(RelationshipRecord(**relationship_row(relationship)))
            try:
                await session.flush()
            except IntegrityError as error:
                if not (constraint_of(error) or "").startswith(_MISSING_END_PREFIX):
                    raise
                msg = (
                    f"cannot link {relationship.source_id} to {relationship.target_id}: "
                    "one of them does not exist"
                )
                raise ElementNotFoundError(msg) from error
            write_events(session, audit)
        return relationship

    async def delete_relationship(
        self, relationship_id: UUID, *, audit: Sequence[AuditEvent] = ()
    ) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(RelationshipRecord).where(RelationshipRecord.id == relationship_id)
            )
            if not _rows_affected(deleted):
                return False
            write_events(session, audit)
        return True

    async def attachments_of(self, element_id: UUID) -> Attachments:
        documents = select(ElementDocument.id).where(ElementDocument.element_id == element_id)
        diagrams = select(DiagramNodeRecord.diagram_id).where(
            DiagramNodeRecord.element_id == element_id
        )
        async with self._sessions() as session:
            return Attachments(
                document_ids=tuple((await session.scalars(documents.order_by(ElementDocument.id)))),
                diagram_ids=tuple(
                    (await session.scalars(diagrams.order_by(DiagramNodeRecord.diagram_id)))
                ),
            )
```

- [ ] **Step 5: Faire suivre le double `InMemoryRepository`** (`tests/conftest.py`)

Remplacer les méthodes d'écriture (ajouter `Attachments` aux imports de `ea.domain.ports` et `AuditEvent` à ceux de `ea.domain.audit`):

```python
    async def add_element(self, element: Element, *, audit: Sequence[AuditEvent] = ()) -> Element:
        self._refuse_a_taken_address(element)
        self.elements[element.id] = element
        self.audit_log.append(audit)
        return element

    async def save_element(self, element: Element, *, audit: Sequence[AuditEvent] = ()) -> Element:
        if element.id not in self.elements:
            raise ElementNotFoundError(str(element.id))
        self._refuse_a_taken_address(element)
        self.elements[element.id] = element
        self.audit_log.append(audit)
        return element

    async def delete_element(self, element_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        if self.elements.pop(element_id, None) is None:
            return False
        self.audit_log.append(audit)
        return True

    async def add_relationship(
        self, relationship: Relationship, *, audit: Sequence[AuditEvent] = ()
    ) -> Relationship:
        self.relationships[relationship.id] = relationship
        self.audit_log.append(audit)
        return relationship

    async def delete_relationship(
        self, relationship_id: UUID, *, audit: Sequence[AuditEvent] = ()
    ) -> bool:
        if self.relationships.pop(relationship_id, None) is None:
            return False
        self.audit_log.append(audit)
        return True

    async def attachments_of(self, element_id: UUID) -> Attachments:
        return Attachments()
```

- [ ] **Step 6: Relancer**

Run: `make test-postgres` — Expected: PASS. Run: `cd backend && uv run pytest tests/unit -x -q` — Expected: PASS (les services n'utilisent pas encore `audit`). Run: `cd backend && uv run mypy src migrations` — Expected: `Success`.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/domain/ports.py backend/src/ea/repositories/architecture_store.py backend/tests/conftest.py backend/tests/integration/test_audit_atomicity.py
git commit -m "feat(audit): graph repository writes audit events in its own transaction" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `ArchitectureService` émet un événement par cas d'usage

**Files:**
- Modify: `backend/src/ea/services/architecture.py` (imports lignes 14-31; `__init__` lignes 43-52; `create_element` 54-83; `update_element` 99-153; `delete_element` 155-172; `connect` 175-228; `disconnect` 253-262)
- Test: `backend/tests/unit/test_audit_writes.py` (créé ici, complété aux Task 7)

**Interfaces:**
- Consumes: Task 1 vues, Task 4 `AuditTrail`, Task 5 `audit=`/`attachments_of`/`relations_of`.
- Produces: comportement — `create_element` → `element.created` (`after=element_view`); `update_element` → `element.updated` (`before`, `after`); `delete_element` → `element.deleted` (`before = element_view + {"cascaded": {"relationships": [{"id","relationship_type"}], "documents": [ids], "diagrams": [ids]}}`); `connect` → `relationship.created`; `disconnect` → `relationship.deleted`. `ArchitectureService.__init__` garde sa signature (`repository, *, clock`).

- [ ] **Step 1: Écrire le test qui échoue**

```python
# backend/tests/unit/test_audit_writes.py
"""Every write use case leaves exactly one event; every refusal leaves none."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    ElementNotFoundError,
    IllegalRelationshipError,
    NotAuthorisedError,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import FIXED_NOW, InMemoryAuditLog, a_reader

pytestmark = pytest.mark.asyncio


class TestTheGraph:
    async def test_creating_an_element_says_who_when_and_what(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        element = await service.create_element(
            element_type=E.APPLICATION_COMPONENT, name="Billing", properties={"owner": "ops"}
        )

        (event,) = audit_log.events
        assert event.action is AuditAction.ELEMENT_CREATED
        assert (event.actor_sub, event.actor_name, event.at) == ("editor-1", "editor", FIXED_NOW)
        assert event.entity_id == element.id
        assert event.before is None
        assert event.after is not None and event.after["properties"] == {"owner": "ops"}

    async def test_updating_records_the_element_before_and_after(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        audit_log.events.clear()

        await service.update_element(element.id, name="db-02", description="primary")

        (event,) = audit_log.events
        assert event.action is AuditAction.ELEMENT_UPDATED
        assert {c.field for c in event.changes} == {"name", "description"}
        assert event.before is not None and event.before["name"] == "db-01"
        assert event.after is not None and event.after["name"] == "db-02"

    async def test_deleting_lists_what_the_foreign_keys_took_with_the_element(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        doomed = await service.create_element(element_type=E.NODE, name="db-01")
        other = await service.create_element(element_type=E.NODE, name="db-02")
        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=doomed.id, target_id=other.id
        )
        audit_log.events.clear()

        await service.delete_element(doomed.id)

        (event,) = audit_log.events
        assert event.action is AuditAction.ELEMENT_DELETED
        assert event.after is None
        assert event.before is not None
        assert event.before["name"] == "db-01"
        assert event.before["cascaded"] == {
            "relationships": [{"id": str(link.id), "relationship_type": "association"}],
            "documents": [],
            "diagrams": [],
        }

    async def test_connecting_and_disconnecting_each_leave_one_event(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        left = await service.create_element(element_type=E.NODE, name="a")
        right = await service.create_element(element_type=E.NODE, name="b")
        audit_log.events.clear()

        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=left.id, target_id=right.id
        )
        await service.disconnect(link.id)

        created, deleted = audit_log.events
        assert (created.action, deleted.action) == (
            AuditAction.RELATIONSHIP_CREATED,
            AuditAction.RELATIONSHIP_DELETED,
        )
        assert created.after is not None and created.after["source_id"] == str(left.id)
        assert deleted.before is not None and deleted.before["id"] == str(link.id)

    async def test_a_refusal_leaves_no_event(
        self, service: ArchitectureService, audit_log: InMemoryAuditLog
    ) -> None:
        await service.create_element(
            element_type=E.NODE, name="h1", properties={"ip_address": "10.0.0.1", "vrf": "default"}
        )
        data = await service.create_element(element_type=E.DATA_OBJECT, name="Invoice")
        function = await service.create_element(element_type=E.APPLICATION_FUNCTION, name="Inv")
        audit_log.events.clear()

        with pytest.raises(ElementNotFoundError):
            await service.update_element(uuid4(), name="x")
        with pytest.raises(ElementNotFoundError):
            await service.delete_element(uuid4())
        with pytest.raises(ElementNotFoundError):
            await service.disconnect(uuid4())
        with pytest.raises(AddressAlreadyAssignedError):
            await service.create_element(
                element_type=E.NODE,
                name="h2",
                properties={"ip_address": "10.0.0.1", "vrf": "default"},
            )
        with pytest.raises(IllegalRelationshipError):
            await service.connect(
                relationship_type=R.ACCESS, source_id=data.id, target_id=function.id
            )
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await service.create_element(element_type=E.NODE, name="nope")

        assert audit_log.events == []
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_audit_writes.py -x`
Expected: FAIL — `ValueError: not enough values to unpack (expected 1, got 0)` (aucun événement n'est émis).

- [ ] **Step 3: Implémenter dans `services/architecture.py`**

Imports: ajouter `from ea.domain.audit import AuditAction, element_view, relationship_view` et `from ea.services.audit import AuditTrail`. Dans `__init__`, après `self._now = clock`: `self._trail = AuditTrail(clock)`.

`create_element`: remplacer la ligne `stored = await self._repository.add_element(element)` par:

```python
        event = self._trail.event(
            AuditAction.ELEMENT_CREATED, entity_id=element.id, after=element_view(element)
        )
        stored = await self._repository.add_element(element, audit=(event,))
```

`update_element`: remplacer `stored = await self._repository.save_element(updated)` par:

```python
        event = self._trail.event(
            AuditAction.ELEMENT_UPDATED,
            entity_id=current.id,
            before=element_view(current),
            after=element_view(updated),
        )
        stored = await self._repository.save_element(updated, audit=(event,))
```

`delete_element`: remplacer le corps après `require_editor()` par:

```python
        require_editor()
        current = await self.get_element(element_id)
        links = await self._repository.relations_of(element_id)
        attached = await self._repository.attachments_of(element_id)
        event = self._trail.event(
            AuditAction.ELEMENT_DELETED,
            entity_id=element_id,
            before={
                **element_view(current),
                "cascaded": {
                    "relationships": [
                        {"id": str(link.id), "relationship_type": link.relationship_type.value}
                        for link in links.relationships
                    ],
                    "documents": [str(identifier) for identifier in attached.document_ids],
                    "diagrams": [str(identifier) for identifier in attached.diagram_ids],
                },
            },
        )
        if not await self._repository.delete_element(element_id, audit=(event,)):
            msg = f"no element with id {element_id}"
            raise ElementNotFoundError(msg)
        logger.info(
            "element deleted, with everything attached to it",
            extra={"action": "deleted", "element_id": str(element_id)},
        )
```

`connect`: remplacer `stored = await self._repository.add_relationship(relationship)` par:

```python
        event = self._trail.event(
            AuditAction.RELATIONSHIP_CREATED,
            entity_id=relationship.id,
            after=relationship_view(relationship),
        )
        stored = await self._repository.add_relationship(relationship, audit=(event,))
```

`disconnect`: remplacer le `if not await ...delete_relationship(...)` par:

```python
        require_editor()
        current = await self.get_relationship(relationship_id)
        event = self._trail.event(
            AuditAction.RELATIONSHIP_DELETED,
            entity_id=relationship_id,
            before=relationship_view(current),
        )
        if not await self._repository.delete_relationship(relationship_id, audit=(event,)):
            msg = f"no relationship with id {relationship_id}"
            raise ElementNotFoundError(msg)
```

(`get_relationship` lève `ElementNotFoundError` si absent et commence par `require_caller()`: `require_editor()` reste la première instruction de `disconnect`.)

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_audit_writes.py tests/unit/test_architecture_service.py tests/unit/test_write_audit.py tests/unit/test_ipam_service.py tests/unit/test_service_guards.py -x`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/services/architecture.py backend/tests/unit/test_audit_writes.py
git commit -m "feat(audit): architecture use cases emit one audit event each" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Documents et diagrammes écrivent leur audit

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (`DocumentRepository` lignes 145-175 ; `DiagramRepository` lignes 201-227), `backend/src/ea/repositories/document_store.py` (méthodes `add` 82-117, `replace` 163-183, `delete` 185-190 ; imports), `backend/src/ea/repositories/diagram_store.py` (méthodes `add` 90-108, `save` 110-124, `delete` 126-131, `replace_layout` 133-157 ; imports), `backend/src/ea/services/documents.py` (`attach_text` 69-100, `revise_text` 134-162, `discard` 164-173), `backend/src/ea/services/diagrams.py` (`create` 59-65, `update` 91-99, `delete` 101-106, `replace_layout` 108-134), `backend/tests/conftest.py` (`InMemoryDocuments` ~420-480, `InMemoryDiagrams` ~520-570)
- Test: `backend/tests/unit/test_audit_writes.py` (compléter), `backend/tests/integration/test_audit_atomicity.py` (compléter)

**Interfaces:**
- Consumes: `write_events`, `AuditTrail`, `document_view`, `diagram_view`, `layout_view`.
- Produces (keyword-only `audit: Sequence[AuditEvent] = ()`): `DocumentRepository.add(document, chunks=(), *, audit=())`, `.replace(document, chunks=(), *, audit=())`, `.delete(document_id, *, audit=())`; `DiagramRepository.add(diagram, *, audit=())`, `.save(diagram, *, audit=())`, `.delete(diagram_id, *, audit=())`, `.replace_layout(diagram_id, nodes, *, now, audit=())`. Événements: `document.attached|revised|discarded`, `diagram.created|updated|deleted|layout_replaced`.

- [ ] **Step 1: Ajouter les tests qui échouent** (à la suite de `tests/unit/test_audit_writes.py`)

```python
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import DiagramNotFoundError, DocumentNotFoundError, DuplicateDocumentError
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService


class TestDocuments:
    async def test_attach_revise_discard_each_leave_one_event_without_the_text(
        self,
        service: ArchitectureService,
        document_service: DocumentService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        audit_log.events.clear()

        document = await document_service.attach_text(
            element.id, filename="runbook.md", content="# Secret plan\n"
        )
        await document_service.revise_text(
            document.id, filename="runbook.md", content="# Other plan\n"
        )
        await document_service.discard(document.id)

        attached, revised, discarded = audit_log.events
        assert [e.action for e in audit_log.events] == [
            AuditAction.DOCUMENT_ATTACHED,
            AuditAction.DOCUMENT_REVISED,
            AuditAction.DOCUMENT_DISCARDED,
        ]
        assert "plan" not in str(audit_log.events)
        assert attached.after is not None and attached.after["sha256"]
        assert revised.before is not None and revised.after is not None
        assert revised.before["sha256"] != revised.after["sha256"]
        assert discarded.before is not None and discarded.after is None

    async def test_a_refused_document_write_leaves_no_event(
        self,
        service: ArchitectureService,
        document_service: DocumentService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        await document_service.attach_text(element.id, filename="a.md", content="# A\n")
        audit_log.events.clear()

        with pytest.raises(DuplicateDocumentError):
            await document_service.attach_text(element.id, filename="a.md", content="# B\n")
        with pytest.raises(DocumentNotFoundError):
            await document_service.discard(uuid4())

        assert audit_log.events == []

    async def test_reindexing_is_not_a_change_of_content(
        self,
        service: ArchitectureService,
        document_service: DocumentService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        await document_service.attach_text(element.id, filename="a.md", content="# A\n")
        audit_log.events.clear()

        await document_service.reindex_all()

        assert audit_log.events == []


class TestDiagrams:
    async def test_every_diagram_write_leaves_one_event(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        audit_log.events.clear()

        diagram = await diagram_service.create(name="Vue", description="")
        await diagram_service.update(diagram.id, name="Vue 2")
        await diagram_service.replace_layout(diagram.id, [DiagramNode(element.id, 1, 2)])
        await diagram_service.delete(diagram.id)

        assert [e.action for e in audit_log.events] == [
            AuditAction.DIAGRAM_CREATED,
            AuditAction.DIAGRAM_UPDATED,
            AuditAction.DIAGRAM_LAYOUT_REPLACED,
            AuditAction.DIAGRAM_DELETED,
        ]
        layout = audit_log.events[2]
        assert layout.before == {"nodes": []}
        assert layout.after is not None and len(layout.after["nodes"]) == 1

    async def test_a_refused_diagram_write_leaves_no_event(
        self, diagram_service: DiagramService, audit_log: InMemoryAuditLog
    ) -> None:
        with pytest.raises(DiagramNotFoundError):
            await diagram_service.delete(uuid4())
        with pytest.raises(DiagramNotFoundError):
            await diagram_service.update(uuid4(), name="x")

        assert audit_log.events == []
```

Et à la suite de `tests/integration/test_audit_atomicity.py`:

```python
async def test_a_document_and_its_event_commit_or_fail_together(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.documents import Document
    from ea.domain.errors import DuplicateDocumentError
    from ea.repositories.document_store import PostgresDocumentRepository

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    documents = PostgresDocumentRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    element = await graph.add_element(an_element("db-01"))
    first = Document.create(element_id=element.id, filename="a.md", content="# A\n", now=NOW)
    second = Document.create(element_id=element.id, filename="a.md", content="# B\n", now=NOW)

    await documents.add(first, audit=(event_for(AuditAction.DOCUMENT_ATTACHED, first.id),))
    with pytest.raises(DuplicateDocumentError):
        await documents.add(second, audit=(event_for(AuditAction.DOCUMENT_ATTACHED, second.id),))
    assert await documents.delete(uuid4(), audit=(event_for(AuditAction.DOCUMENT_DISCARDED, None),)) is False

    page = await trail.page(AuditFilter(entity_kind=EntityKind.DOCUMENT))
    assert [e.entity_id for e in page.events] == [first.id]


async def test_a_diagram_layout_and_its_event_commit_together(engine_at_head: AsyncEngine) -> None:
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.repositories.diagram_store import PostgresDiagramRepository

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    diagrams = PostgresDiagramRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    element = await graph.add_element(an_element("db-01"))
    diagram = await diagrams.add(
        Diagram.create(name="Vue", now=NOW),
        audit=(event_for(AuditAction.DIAGRAM_CREATED, None),),
    )

    assert not await diagrams.replace_layout(
        uuid4(),
        [DiagramNode(element.id, 0, 0)],
        now=NOW,
        audit=(event_for(AuditAction.DIAGRAM_LAYOUT_REPLACED, None),),
    )
    assert await diagrams.replace_layout(
        diagram.id,
        [DiagramNode(element.id, 0, 0)],
        now=NOW,
        audit=(event_for(AuditAction.DIAGRAM_LAYOUT_REPLACED, diagram.id),),
    )

    actions = [e.action for e in (await trail.page(AuditFilter(entity_kind=EntityKind.DIAGRAM))).events]
    assert sorted(a.value for a in actions) == ["diagram.created", "diagram.layout_replaced"]
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_audit_writes.py -x`
Expected: FAIL — `ValueError: not enough values to unpack (expected 3, got 0)`.

- [ ] **Step 3: Étendre les ports** (`domain/ports.py`)

`DocumentRepository` et `DiagramRepository` (les docstrings existantes sont conservées; ajouter `from ea.domain.audit import AuditEvent` aux imports de `ports.py` si Task 3 ne l'a pas fait):

```python
class DocumentRepository(Protocol):
    async def add(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: Sequence[AuditEvent] = (),
    ) -> Document: ...

    async def replace(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: Sequence[AuditEvent] = (),
    ) -> Document: ...

    async def delete(self, document_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool: ...
    # … get, list_for_element, all_document_ids, search: inchangées


class DiagramRepository(Protocol):
    async def add(self, diagram: Diagram, *, audit: Sequence[AuditEvent] = ()) -> Diagram: ...

    async def save(self, diagram: Diagram, *, audit: Sequence[AuditEvent] = ()) -> Diagram: ...

    async def delete(self, diagram_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool: ...

    async def replace_layout(
        self,
        diagram_id: UUID,
        nodes: Sequence[DiagramNode],
        *,
        now: datetime,
        audit: Sequence[AuditEvent] = (),
    ) -> bool: ...
    # … list_all, get, nodes_of: inchangées
```

- [ ] **Step 4: Dépôts PostgreSQL**

`document_store.py`: ajouter `from ea.repositories.audit_store import write_events` et (TYPE_CHECKING) `from ea.domain.audit import AuditEvent`. `add` et `replace` gardent leur corps; seuls changent l'en-tête et la dernière ligne de la transaction:

```python
    async def add(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: Sequence[AuditEvent] = (),
    ) -> Document:
        ...  # inchangé jusqu'à la ligne ci-dessous, dans le `async with`
            session.add_all(_chunk_rows(document, chunks))
            write_events(session, audit)
        return document

    async def replace(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: Sequence[AuditEvent] = (),
    ) -> Document:
        ...  # inchangé jusqu'à la ligne ci-dessous, dans le `async with`
            session.add_all(_chunk_rows(document, chunks))
            write_events(session, audit)
        return document
```

`delete`:

```python
    async def delete(self, document_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        """Delete a document. Its passages go with it, by the foreign key."""
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementDocument).where(ElementDocument.id == document_id)
            )
            if not _rows_affected(deleted):
                return False
            write_events(session, audit)
        return True
```

`diagram_store.py`: mêmes imports. `add` et `save` gardent leur corps; l'en-tête et la dernière ligne de la transaction changent:

```python
    async def add(self, diagram: Diagram, *, audit: Sequence[AuditEvent] = ()) -> Diagram:
        ...  # inchangé: session.add(...), puis le try/except du flush
            write_events(session, audit)  # dans le `async with`, après le flush
        return diagram

    async def save(self, diagram: Diagram, *, audit: Sequence[AuditEvent] = ()) -> Diagram:
        ...  # inchangé: lecture de la ligne, affectations, try/except du flush
            write_events(session, audit)
        return diagram
```

`delete`:

```python
    async def delete(self, diagram_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        """Its nodes follow by the foreign key."""
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(DiagramRecord).where(DiagramRecord.id == diagram_id)
            )
            if not cast("CursorResult[Any]", deleted).rowcount:
                return False
            write_events(session, audit)
        return True
```

`replace_layout`: le `return False` d'un diagramme absent reste avant toute écriture, donc sans événement.

```python
    async def replace_layout(
        self,
        diagram_id: UUID,
        nodes: Sequence[DiagramNode],
        *,
        now: datetime,
        audit: Sequence[AuditEvent] = (),
    ) -> bool:
        async with self._sessions.begin() as session:
            row = await session.get(DiagramRecord, diagram_id, with_for_update=True)
            if row is None:
                return False
            ...  # inchangé: delete des boîtes, session.add_all(...)
            row.updated_at = now
            write_events(session, audit)
        return True
```

- [ ] **Step 5: Doubles** (`tests/conftest.py`)

`InMemoryDocuments`: `add` et `replace` gardent leur corps et ajoutent l'événement après leurs affectations:

```python
    async def add(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: Sequence[AuditEvent] = (),
    ) -> Document:
        ...  # le refus de doublon, inchangé, lève avant
        self.documents[document.id] = document
        self.chunks[document.id] = tuple(chunks)
        self.audit_log.append(audit)
        return document

    async def replace(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: Sequence[AuditEvent] = (),
    ) -> Document:
        if document.id not in self.documents:
            raise DocumentNotFoundError(str(document.id))
        self.documents[document.id] = document
        self.chunks[document.id] = tuple(chunks)
        self.audit_log.append(audit)
        return document
```

`delete`:

```python
    async def delete(self, document_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        """The passages go with the document — here by hand, in PostgreSQL by
        the foreign key from `document_chunks`."""
        self.chunks.pop(document_id, None)
        if self.documents.pop(document_id, None) is None:
            return False
        self.audit_log.append(audit)
        return True
```

`InMemoryDiagrams`:

```python
    async def add(self, diagram: Diagram, *, audit: Sequence[AuditEvent] = ()) -> Diagram:
        self._refuse_a_taken_name(diagram)
        self.diagrams[diagram.id] = diagram
        self.audit_log.append(audit)
        return diagram

    async def save(self, diagram: Diagram, *, audit: Sequence[AuditEvent] = ()) -> Diagram:
        if diagram.id not in self.diagrams:
            raise DiagramNotFoundError(str(diagram.id))
        self._refuse_a_taken_name(diagram)
        self.diagrams[diagram.id] = diagram
        self.audit_log.append(audit)
        return diagram

    async def replace_layout(
        self,
        diagram_id: UUID,
        nodes: Sequence[DiagramNode],
        *,
        now: datetime,
        audit: Sequence[AuditEvent] = (),
    ) -> bool:
        stored = self.diagrams.get(diagram_id)
        if stored is None:
            return False
        self.nodes[diagram_id] = tuple(nodes)
        self.diagrams[diagram_id] = stored.touched(now)
        self.audit_log.append(audit)
        return True
```

`delete`:

```python
    async def delete(self, diagram_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        self.nodes.pop(diagram_id, None)
        if self.diagrams.pop(diagram_id, None) is None:
            return False
        self.audit_log.append(audit)
        return True
```

(`replace_layout` est donné plus haut.)

- [ ] **Step 6: Services**

`services/documents.py`: imports `from ea.domain.audit import AuditAction, document_view`, `from ea.services.audit import AuditTrail`; `self._trail = AuditTrail(clock)` dans `__init__`. `attach_text`: remplacer `stored = await self._repository.add(document, passages)` par

```python
        event = self._trail.event(
            AuditAction.DOCUMENT_ATTACHED, entity_id=document.id, after=document_view(document)
        )
        stored = await self._repository.add(document, passages, audit=(event,))
```

`revise_text`: remplacer `stored = await self._repository.replace(revised, passages)` par

```python
        event = self._trail.event(
            AuditAction.DOCUMENT_REVISED,
            entity_id=revised.id,
            before=document_view(current),
            after=document_view(revised),
        )
        stored = await self._repository.replace(revised, passages, audit=(event,))
```

`discard`:

```python
    async def discard(self, document_id: UUID) -> None:
        require_editor()
        current = await self.get(document_id)
        event = self._trail.event(
            AuditAction.DOCUMENT_DISCARDED, entity_id=document_id, before=document_view(current)
        )
        if not await self._repository.delete(document_id, audit=(event,)):
            msg = f"no document with id {document_id}"
            raise DocumentNotFoundError(msg)
        logger.info(
            "document discarded, passages included",
            extra={"action": "discarded", "document_id": str(document_id)},
        )
```

`services/diagrams.py`: imports `from ea.domain.audit import AuditAction, diagram_view, layout_view`, `from ea.services.audit import AuditTrail`; `self._trail = AuditTrail(clock)`. `create`:

```python
        require_editor()
        diagram = Diagram.create(name=name, description=description, now=self._now())
        event = self._trail.event(
            AuditAction.DIAGRAM_CREATED, entity_id=diagram.id, after=diagram_view(diagram)
        )
        stored = await self._repository.add(diagram, audit=(event,))
        self._audit("diagram created: %r", stored, "diagram_created")
        return stored
```

`update`:

```python
        require_editor()
        current = await self.get(diagram_id)
        revised = current.revise(name=name, description=description, now=self._now())
        event = self._trail.event(
            AuditAction.DIAGRAM_UPDATED,
            entity_id=diagram_id,
            before=diagram_view(current),
            after=diagram_view(revised),
        )
        stored = await self._repository.save(revised, audit=(event,))
        self._audit("diagram updated: %r", stored, "diagram_updated")
        return stored
```

`delete`:

```python
        require_editor()
        current = await self.get(diagram_id)
        event = self._trail.event(
            AuditAction.DIAGRAM_DELETED, entity_id=diagram_id, before=diagram_view(current)
        )
        if not await self._repository.delete(diagram_id, audit=(event,)):
            raise _not_found(diagram_id)
        logger.info(
            "diagram deleted", extra={"action": "diagram_deleted", "diagram_id": str(diagram_id)}
        )
```

`replace_layout`: après le contrôle `missing`, remplacer l'appel `replace_layout` par

```python
        event = self._trail.event(
            AuditAction.DIAGRAM_LAYOUT_REPLACED,
            entity_id=diagram_id,
            before=layout_view(await self._repository.nodes_of(diagram_id)),
            after=layout_view(layout),
        )
        if not await self._repository.replace_layout(
            diagram_id, layout, now=self._now(), audit=(event,)
        ):
            raise _not_found(diagram_id)  # deleted between the read and the write
```

- [ ] **Step 7: Relancer**

Run: `cd backend && uv run pytest tests/unit -x -q` — Expected: PASS. Run: `make test-postgres` — Expected: PASS. Run: `cd backend && uv run mypy src migrations` — Expected: `Success`.

- [ ] **Step 8: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(audit): documents and diagrams write their audit events atomically" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8: API HTTP — `GET /audit`, `GET /elements/{id}/history`, câblage

**Files:**
- Create: `backend/src/ea/api/audit.py`, `backend/tests/e2e/test_audit_api.py`
- Modify: `backend/src/ea/api/schemas.py` (imports ligne 14-37; bornes après ligne 110; modèles en fin de fichier), `backend/src/ea/api/errors.py` (import ligne 29-48; `_STATUS` ligne ~76), `backend/src/ea/api/dependencies.py` (après `file_service_of`, ligne ~126), `backend/src/ea/api/auth.py` (fonction `authenticate`, lignes 38-50), `backend/src/ea/main.py` (imports lignes 15-60; `_lifespan` signature ligne ~137 et corps lignes 212-290; `create_app` signature ligne ~440 et corps ~500-537; `_mount_mcp` ligne ~402 (fait en Task 9)), `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés)

**Interfaces:**
- Consumes: `AuditService`, `PostgresAuditRepository`, `AuditFilter`, `aware`.
- Produces:
  - schémas: `AuditEventRead(id, at, actor_sub, actor_name, action: AuditAction, entity_kind: EntityKind, entity_id: UUID | None, before, after, request_id, channel: AuditChannel, correlation, changes: list[FieldChangeRead])` avec `AuditEventRead.of(event)`; `FieldChangeRead(field, before, after)`; `AuditPageRead(items, next_cursor)`
  - bornes: `Actor`, `RequestIdFilter`, `CursorToken` (dans `api/schemas.py`); `Limit` (≤ 200) réutilisé
  - `app.state.audit_service`; dépendance `Audit`; `audit_service_of(app)`; `create_app(..., audit: AuditRepository | None = None)`
  - `current_channel` posé à `HTTP` par `authenticate`
  - `_STATUS[InvalidCursorError] = (422, "invalid_cursor")`

- [ ] **Step 1: Écrire le test e2e qui échoue**

```python
# backend/tests/e2e/test_audit_api.py
"""The audit endpoints, end to end against doubled stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryAuditLog, StaticVerifier, a_reader, an_editor

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, audit_log: InMemoryAuditLog, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier({EDITOR: an_editor(), READER: a_reader()})
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        audit=audit_log,
        verifier=verifier,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def as_(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    response = await client.post(
        "/elements",
        json={"element_type": "node", "name": name},
        headers=as_(EDITOR),
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestReadingTheTrail:
    async def test_a_write_over_http_is_in_the_trail_with_its_door_and_its_diff(
        self, client: httpx.AsyncClient
    ) -> None:
        element = await make(client, "db-01")
        await client.patch(
            f"/elements/{element['id']}", json={"name": "db-02"}, headers=as_(EDITOR)
        )

        page = (await client.get("/audit", headers=as_(READER))).json()

        assert [e["action"] for e in page["items"]] == ["element.updated", "element.created"]
        updated = page["items"][0]
        assert updated["channel"] == "http"
        assert updated["actor_name"] == "editor"
        assert updated["entity_id"] == element["id"]
        assert [(c["field"], c["before"], c["after"]) for c in updated["changes"]] == [
            ("name", "db-01", "db-02")
        ]

    async def test_the_history_of_an_element_is_the_audit_filtered_to_it(
        self, client: httpx.AsyncClient
    ) -> None:
        first, second = await make(client, "a"), await make(client, "b")

        history = (
            await client.get(f"/elements/{first['id']}/history", headers=as_(READER))
        ).json()

        assert [e["entity_id"] for e in history["items"]] == [first["id"]]
        assert second["id"] not in str(history)

    async def test_pagination_is_stable_and_a_forged_cursor_is_a_422(
        self, client: httpx.AsyncClient
    ) -> None:
        for name in "abcde":
            await make(client, name)

        collected: list[str] = []
        cursor: str | None = None
        for _ in range(10):
            params: dict[str, Any] = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            page = (await client.get("/audit", params=params, headers=as_(READER))).json()
            collected.extend(e["id"] for e in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        forged = await client.get("/audit", params={"cursor": "garbage!"}, headers=as_(READER))

        assert len(collected) == len(set(collected)) == 5
        assert forged.status_code == 422
        assert forged.json()["error"] == "invalid_cursor"

    async def test_moments_without_a_zone_are_read_as_utc_and_bounds_are_enforced(
        self, client: httpx.AsyncClient
    ) -> None:
        await make(client, "a")

        naive = await client.get(
            "/audit", params={"since": "2000-01-01T00:00:00"}, headers=as_(READER)
        )
        too_big = await client.get("/audit", params={"limit": 201}, headers=as_(READER))
        unknown = await client.get("/audit", params={"action": "element.exploded"}, headers=as_(READER))

        assert naive.status_code == 200 and len(naive.json()["items"]) == 1
        assert too_big.status_code == 422
        assert unknown.status_code == 422

    async def test_reading_needs_a_token(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/audit")).status_code == 401
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/e2e/test_audit_api.py -x`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'audit'`.

- [ ] **Step 3: Erreur 422, bornes et modèles de réponse**

`api/errors.py`: ajouter `InvalidCursorError` à l'import de `ea.domain.errors` (ordre alphabétique) et, dans `_STATUS`, avant le bloc « Authentication »:

```python
    # --- The audit trail (docs/adr/NNNN) ---
    InvalidCursorError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_cursor"),
```

`api/schemas.py`: imports `from typing import Annotated, Any, Final` ; `from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, EntityKind, FieldChange`. Après `LinkName` (ligne 102):

```python
#: The audit filters, bounded once for both adapters (docs/adr/NNNN).
Actor = Annotated[str, Field(max_length=200)]
RequestIdFilter = Annotated[str, Field(max_length=128)]
CursorToken = Annotated[str, Field(max_length=512)]
```

En fin de fichier:

```python
# --- The audit trail (docs/adr/NNNN) ---------------------------------------


class FieldChangeRead(BaseModel):
    field: str
    before: Any = None
    after: Any = None


class AuditEventRead(BaseModel):
    """One use case that changed the catalogue — the diff is computed server-side,
    so a client displays it and never decides what counts as a change."""

    id: UUID
    at: datetime
    actor_sub: str
    actor_name: str
    action: AuditAction
    entity_kind: EntityKind
    entity_id: UUID | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    request_id: str
    channel: AuditChannel
    correlation: str | None
    changes: list[FieldChangeRead]

    @classmethod
    def of(cls, event: AuditEvent) -> AuditEventRead:
        return cls(
            id=event.id,
            at=event.at,
            actor_sub=event.actor_sub,
            actor_name=event.actor_name,
            action=event.action,
            entity_kind=event.entity_kind,
            entity_id=event.entity_id,
            before=dict(event.before) if event.before is not None else None,
            after=dict(event.after) if event.after is not None else None,
            request_id=event.request_id,
            channel=event.channel,
            correlation=event.correlation,
            changes=[_change(change) for change in event.changes],
        )


def _change(change: FieldChange) -> FieldChangeRead:
    return FieldChangeRead(field=change.field, before=change.before, after=change.after)


class AuditPageRead(BaseModel):
    items: list[AuditEventRead]
    next_cursor: str | None = Field(
        description="Pass it back as `cursor` for the next page; absent on the last one."
    )
```

- [ ] **Step 4: Routeur, dépendance, canal**

`api/dependencies.py`: ajouter `from ea.services.audit import AuditService` et, avant `session_factory_of`:

```python
def audit_service_of(app: FastAPI) -> AuditService:
    """The audit reader the lifespan attached, or a clear failure — a wiring fault, so a 500."""
    service: AuditService | None = getattr(app.state, "audit_service", None)
    if service is None:
        msg = "no audit service on the application — is postgres_enabled on?"
        raise RuntimeError(msg)
    return service


def get_audit_service(request: Request) -> AuditService:
    return audit_service_of(request.app)


Audit = Annotated[AuditService, Depends(get_audit_service)]
```

`api/auth.py`: importer `from ea.domain.audit import AuditChannel` et `from ea.services.caller import current_caller, current_channel`; dans `authenticate`, après `current_caller.set(caller)` ajouter `current_channel.set(AuditChannel.HTTP)`.

```python
# backend/src/ea/api/audit.py
"""The audit trail over HTTP: one question, newest first, a page at a time.

Reads only. A trail that could be written to over HTTP would not be one: events
are made by the services, in the transaction of the write they describe.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from ea.api.dependencies import Audit
from ea.api.schemas import (
    Actor,
    AuditEventRead,
    AuditPageRead,
    CursorToken,
    ErrorResponse,
    Limit,
    RequestIdFilter,
)
from ea.domain.audit import DEFAULT_AUDIT_PAGE, AuditAction, AuditFilter, AuditPage, EntityKind, aware

router = APIRouter(tags=["audit"])

LimitQuery = Annotated[Limit, Query()]


def _read(page: AuditPage) -> AuditPageRead:
    return AuditPageRead(
        items=[AuditEventRead.of(event) for event in page.events], next_cursor=page.next_cursor
    )


@router.get(
    "/audit",
    response_model=AuditPageRead,
    responses={422: {"model": ErrorResponse}},
)
async def list_audit_events(
    service: Audit,
    entity_kind: Annotated[EntityKind | None, Query()] = None,
    entity_id: Annotated[UUID | None, Query()] = None,
    actor: Annotated[Actor | None, Query()] = None,
    action: Annotated[AuditAction | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    request_id: Annotated[RequestIdFilter | None, Query()] = None,
    cursor: Annotated[CursorToken | None, Query()] = None,
    limit: LimitQuery = DEFAULT_AUDIT_PAGE,
) -> AuditPageRead:
    """Who changed what, and when — newest first. `since` is inclusive, `until` exclusive."""
    return _read(
        await service.list_events(
            AuditFilter(
                entity_kind=entity_kind,
                entity_id=entity_id,
                actor=actor,
                action=action,
                since=aware(since),
                until=aware(until),
                request_id=request_id,
                cursor=cursor,
                limit=limit,
            )
        )
    )


@router.get("/elements/{element_id}/history", response_model=AuditPageRead)
async def read_element_history(
    element_id: UUID,
    service: Audit,
    cursor: Annotated[CursorToken | None, Query()] = None,
    limit: LimitQuery = DEFAULT_AUDIT_PAGE,
) -> AuditPageRead:
    """The trail of one element — still readable once the element is deleted."""
    return _read(
        await service.list_events(
            AuditFilter(
                entity_kind=EntityKind.ELEMENT, entity_id=element_id, cursor=cursor, limit=limit
            )
        )
    )
```

- [ ] **Step 5: Câblage dans `main.py`**

Imports: `from ea.api.audit import router as audit_router`, `from ea.domain.ports import AuditRepository` (dans le bloc existant `from ea.domain.ports import (...)`), `from ea.repositories.audit_store import PostgresAuditRepository`, `from ea.services.audit import AuditService`.

`_lifespan(...)`: ajouter le paramètre `audit`; juste après `metadata_store = file_metadata`, ajouter `audit_store = audit`; dans le bloc `if settings.postgres_enabled:` après la construction de `metadata_store`:

```python
def _lifespan(
    settings: Settings,
    *,
    open_graph: bool,
    # … documents, indexer, diagrams, file_metadata: inchangés
    audit: AuditRepository | None = None,
) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    ...
            metadata_store = file_metadata
            audit_store = audit
            if settings.postgres_enabled:
                ...
                if metadata_store is None:
                    metadata_store = PostgresFileMetadataRepository(app.state.db_sessions)
                if audit_store is None:
                    audit_store = PostgresAuditRepository(app.state.db_sessions)
```

et, juste avant le commentaire « Files live in MinIO »:

```python
            if audit_store is not None:
                app.state.audit_service = AuditService(audit_store)
```

`create_app(...)`: ajouter le paramètre `audit: AuditRepository | None = None` (après `file_metadata`), le passer à `_lifespan(..., audit=audit)`, puis après le bloc `if files is not None:`:

```python
    if audit is not None:
        app.state.audit_service = AuditService(audit)
```

et, avec les autres `include_router`:

```python
    app.include_router(audit_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
```

Mettre à jour la docstring de `create_app` d'une ligne: « `audit` does the same for the audit trail (docs/adr/NNNN). »

- [ ] **Step 6: Relancer, régénérer le contrat**

Run: `cd backend && uv run pytest tests/e2e tests/unit/test_auth_contract.py tests/unit/test_openapi.py -x -q` — Expected: `test_openapi` peut échouer tant que le schéma n'est pas régénéré; le reste PASS.
Run: `make openapi` — Expected: « Schéma et client régénérés. », `git diff --stat` montre `backend/openapi.json` et `frontend/src/api/schema.d.ts`.
Run: `make openapi-check` — Expected: « Client généré à jour. »
Run: `cd backend && uv run pytest tests/e2e tests/unit -x -q` — Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src backend/tests/e2e/test_audit_api.py backend/openapi.json frontend/src/api/schema.d.ts
git commit -m "feat(audit): GET /audit and element history over HTTP, wired in the lifespan" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Outils MCP de lecture de l'audit

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports lignes 34-77; `INSTRUCTIONS` ligne ~122-148; type `AuditProvider` après `FileProvider` ligne ~158; `build_mcp_server` signature ligne 294 et fin d'outils), `backend/src/ea/mcp/errors.py` (`speaking_plainly`, bloc `token = get_access_token()`, lignes 76-80), `backend/src/ea/main.py` (`_mount_mcp`, appel ligne ~402), `backend/tests/unit/test_mcp_server.py` (fixtures lignes 38-70, liste ligne ~86, classe `TestTheSameBoundsAsTheHttpAdapter` ligne ~1040)

**Interfaces:**
- Consumes: `AuditService`, `AuditPageRead`, `Actor`, `RequestIdFilter`, `CursorToken`, `Limit`.
- Produces:
  - `build_mcp_server(get_service, get_documents, get_ipam, get_files, get_audit, *, version=..., token_verifier=..., auth=...)`; `AuditProvider = Callable[[], AuditService]`
  - outils `list_audit_events(entity_kind, entity_id, actor, action, since, until, request_id, cursor, limit) -> AuditPageRead` et `get_element_history(element_id, cursor, limit) -> AuditPageRead`, annotation `READS`
  - `speaking_plainly` exécute chaque outil dans `acting_through(AuditChannel.MCP)`

- [ ] **Step 1: Écrire les tests qui échouent** (dans `tests/unit/test_mcp_server.py`)

Fixtures (les deux, lignes 38-70) — ajouter `audit_log: InMemoryAuditLog` aux paramètres et `lambda: AuditService(audit_log)` comme cinquième argument de `build_mcp_server`; imports: `from ea.services.audit import AuditService`, `from tests.conftest import InMemoryAuditLog, InMemoryObjectStore, a_reader`. Ajouter `"list_audit_events"`, `"get_element_history"` à l'ensemble de la liste complète (test `test_it_exposes_the_whole_service_and_nothing_else`). Ajouter la classe:

```python
@pytest.mark.asyncio
class TestTheAuditTools:
    async def test_an_agent_reads_the_history_of_an_element_it_changed(
        self, server: MCPServer[Any]
    ) -> None:
        created = await an_element(server, "node", "db-01")
        await call(server, "update_element", element_id=created["id"], name="db-02")

        history = await call(server, "get_element_history", element_id=created["id"])
        everything = await call(server, "list_audit_events", action="element.created")

        assert [i["action"] for i in history["items"]] == ["element.updated", "element.created"]
        assert history["items"][0]["channel"] == "mcp"
        assert [i["entity_id"] for i in everything["items"]] == [created["id"]]

    async def test_an_anonymous_agent_is_refused(
        self, server: MCPServer[Any], nobody_calling: None
    ) -> None:
        result = await server.call_tool("list_audit_events", {})

        assert result.is_error
```

Dans `TestTheSameBoundsAsTheHttpAdapter`, ajouter au premier `parametrize` rien; au second (paramètres de requête) ajouter:

```python
            ("list_audit_events", "actor", "/audit", "actor"),
            ("list_audit_events", "request_id", "/audit", "request_id"),
            ("list_audit_events", "cursor", "/audit", "cursor"),
            ("list_audit_events", "limit", "/audit", "limit"),
            ("get_element_history", "limit", "/elements/{element_id}/history", "limit"),
            ("get_element_history", "cursor", "/elements/{element_id}/history", "cursor"),
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: FAIL — `TypeError: build_mcp_server() takes 4 positional arguments but 5 were given`.

- [ ] **Step 3: Implémenter**

`mcp/errors.py`: dans `plainly`, remplacer le bloc `token = get_access_token()` … `with caller:` par:

```python
        token = get_access_token()
        caller: AbstractContextManager[object] = (
            nullcontext() if token is None else acting_as(caller_of(token))
        )
        try:
            # Every MCP call is stamped as such in the audit trail (docs/adr/NNNN):
            # the adapter says which door it is, the caller never does.
            with caller, acting_through(AuditChannel.MCP):
                answer = await tool(*args, **kwargs)
```

avec les imports `from ea.domain.audit import AuditChannel` et `from ea.services.caller import acting_as, acting_through`.

`mcp/server.py`: imports `from datetime import datetime`; dans le bloc `from ea.api.schemas import (...)`: `Actor, AuditPageRead, CursorToken, RequestIdFilter`; `from ea.domain.audit import DEFAULT_AUDIT_PAGE, AuditAction, AuditFilter, EntityKind, aware`; `from ea.services.audit import AuditService`. Après `FileProvider`: `AuditProvider = Callable[[], AuditService]`. Ajouter à `INSTRUCTIONS` (avant le `\` final), un paragraphe:

```
Every change to the catalogue is kept in an audit trail. Before rewriting an
element, call `get_element_history` to see who changed it, when and how; use
`list_audit_events` to ask the trail anything else (by actor, by action, by
period). The trail is read-only: it records what happened, it cannot be edited.
```

Signature: `def build_mcp_server(get_service, get_documents, get_ipam, get_files, get_audit: AuditProvider, *, version=...` (docstring: `get_audit` est requis pour la même raison que `get_files`). Outils, à la fin de la fonction:

```python
    # --- The audit trail (docs/adr/NNNN) ----------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_audit_events(
        entity_kind: EntityKind | None = None,
        entity_id: ElementId | None = None,
        actor: Actor | None = None,
        action: AuditAction | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        request_id: RequestIdFilter | None = None,
        cursor: CursorToken | None = None,
        limit: Limit = DEFAULT_AUDIT_PAGE,
    ) -> AuditPageRead:
        """Who changed what, and when — newest first, a page at a time.

        `actor` matches a user name or a Keycloak subject. `since` is inclusive,
        `until` exclusive; a moment without a zone is read as UTC. Each event
        carries `changes`, the field-by-field diff. Pass `next_cursor` back as
        `cursor` to read the next page.
        """
        page = await get_audit().list_events(
            AuditFilter(
                entity_kind=entity_kind,
                entity_id=entity_id,
                actor=actor,
                action=action,
                since=aware(since),
                until=aware(until),
                request_id=request_id,
                cursor=cursor,
                limit=limit,
            )
        )
        return AuditPageRead.of(page)

    @server.tool(annotations=READS)
    @speaking_plainly
    async def get_element_history(
        element_id: ElementId,
        cursor: CursorToken | None = None,
        limit: Limit = DEFAULT_AUDIT_PAGE,
    ) -> AuditPageRead:
        """The trail of one element, newest first — readable after it is deleted.

        Read this before rewriting an element: it says who last changed it and what.
        """
        page = await get_audit().list_events(
            AuditFilter(
                entity_kind=EntityKind.ELEMENT, entity_id=element_id, cursor=cursor, limit=limit
            )
        )
        return AuditPageRead.of(page)
```

Ajouter dans `api/schemas.py` (classe `AuditPageRead`) la fabrique manquante, pour que les deux adaptateurs rendent la même chose:

```python
    @classmethod
    def of(cls, page: AuditPage) -> AuditPageRead:
        return cls(
            items=[AuditEventRead.of(event) for event in page.events],
            next_cursor=page.next_cursor,
        )
```

(import `AuditPage` depuis `ea.domain.audit`), et remplacer `_read` de `api/audit.py` par `AuditPageRead.of`.

`main.py` `_mount_mcp` (import `audit_service_of` depuis `ea.api.dependencies`):

```python
    server = build_mcp_server(
        lambda: architecture_service_of(app),
        lambda: document_service_of(app),
        lambda: ipam_service_of(app),
        lambda: file_service_of(app),
        lambda: audit_service_of(app),
        version=app.version,
        token_verifier=KeycloakTokenVerifier(_LazyVerifier(app)) if auth else None,
        auth=auth_settings(settings) if auth else None,
    )
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit tests/e2e -x -q` — Expected: PASS. Run: `make openapi-check` — Expected: à jour (MCP n'est pas dans OpenAPI; `AuditPageRead.of` non plus).

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(mcp): audit trail tools, and every MCP call stamped as such" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 10: SPA — section « Historique »

**Files:**
- Create: `frontend/src/features/history/auditLabels.ts`, `frontend/src/features/history/useAuditLog.ts`, `frontend/src/features/history/FieldDiff.vue`, `frontend/src/features/history/HistorySection.vue`, `frontend/tests/FieldDiff.spec.ts`, `frontend/tests/useAuditLog.spec.ts`, `frontend/tests/HistorySection.spec.ts`
- Modify: `frontend/src/router/sections.ts` (`GROUPS` ligne 33-37; `SECTIONS` fin de liste ligne ~97), `frontend/tests/support/api.ts` (fin de fichier)

**Interfaces:**
- Consumes: types générés `components['schemas']['AuditEventRead' | 'AuditPageRead' | 'AuditAction' | 'EntityKind']` (régénérés en Task 8), `api`, `unwrap`, `useLatestRequest`.
- Produces:
  - `useAuditLog()` → `{ events: Ref<AuditEvent[]>, nextCursor: Ref<string|null>, status, error, load(filters: AuditFilters, limit?: number): Promise<void> }`
  - `type AuditFilters = { kind?: EntityKind; entity?: string; actor?: string; action?: AuditAction; since?: string; cursor?: string }`
  - `FieldDiff.vue` props `{ changes: { field: string; before?: unknown; after?: unknown }[] }`
  - `ACTION_LABELS: Record<AuditAction, string>`, `KIND_LABELS: Record<EntityKind, string>`
  - `anAuditEvent(overrides)` dans `tests/support/api.ts`
  - route `history` (`/history`) dans le groupe `governance`

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à la fin de `frontend/tests/support/api.ts`:

```ts
type AuditEventRead = components['schemas']['AuditEventRead']

/** One line of the audit trail, as `GET /audit` returns it. */
export function anAuditEvent(overrides: Partial<AuditEventRead> = {}): AuditEventRead {
  return {
    id: '11111111-1111-4111-8111-111111111111',
    at: '2026-10-10T12:00:00Z',
    actor_sub: 'editor-1',
    actor_name: 'editor',
    action: 'element.updated',
    entity_kind: 'element',
    entity_id: '22222222-2222-4222-8222-222222222222',
    before: { name: 'db-01' },
    after: { name: 'db-02' },
    request_id: 'req-1',
    channel: 'http',
    correlation: null,
    changes: [{ field: 'name', before: 'db-01', after: 'db-02' }],
    ...overrides,
  }
}
```

```ts
// frontend/tests/FieldDiff.spec.ts
import { render, screen } from '@testing-library/vue'
import { describe, expect, it } from 'vitest'

import FieldDiff from '../src/features/history/FieldDiff.vue'

describe('FieldDiff', () => {
  it('shows each field with what it was and what it became', () => {
    render(FieldDiff, {
      props: {
        changes: [
          { field: 'name', before: 'db-01', after: 'db-02' },
          { field: 'properties.owner', before: null, after: 'ops' },
        ],
      },
    })

    expect(screen.getByText('name')).toBeTruthy()
    expect(screen.getByText('db-01')).toBeTruthy()
    expect(screen.getByText('db-02')).toBeTruthy()
    expect(screen.getByText('properties.owner')).toBeTruthy()
    expect(screen.getByText('—')).toBeTruthy() // an absent value is a dash, not "null"
  })

  it('says so when nothing differs', () => {
    render(FieldDiff, { props: { changes: [] } })

    expect(screen.getByText('Aucun champ modifié.')).toBeTruthy()
  })
})
```

```ts
// frontend/tests/useAuditLog.spec.ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useAuditLog } from '../src/features/history/useAuditLog'
import { anAuditEvent, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useAuditLog', () => {
  it('sends the filters under the names the backend reads, and drops the empty ones', async () => {
    const calls = stubApi([
      { path: '/audit', body: { items: [anAuditEvent()], next_cursor: 'next' } },
    ])
    const log = useAuditLog()

    await log.load({ kind: 'element', entity: 'abc', actor: '', cursor: 'c1' }, 20)

    const query = calls[0]?.url.searchParams
    expect(query?.get('entity_kind')).toBe('element')
    expect(query?.get('entity_id')).toBe('abc')
    expect(query?.get('cursor')).toBe('c1')
    expect(query?.get('limit')).toBe('20')
    expect(query?.has('actor')).toBe(false)
    expect(log.events.value).toHaveLength(1)
    expect(log.nextCursor.value).toBe('next')
  })

  it('keeps a failure in its state rather than throwing', async () => {
    stubApi([{ path: '/audit', status: 422, body: { error: 'invalid_cursor', detail: 'bad' } }])
    const log = useAuditLog()

    await log.load({ cursor: 'garbage' })

    expect(log.status.value).toBe('error')
    expect(log.error.value).not.toBe('')
  })
})
```

```ts
// frontend/tests/HistorySection.spec.ts
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import HistorySection from '../src/features/history/HistorySection.vue'
import { createAppRouter } from '../src/router'
import { anAuditEvent, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

async function open(query = '', body: unknown = { items: [anAuditEvent()], next_cursor: null }) {
  const calls = stubApi([{ path: '/audit', body }])
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/history${query}`)
  await router.isReady()
  render(HistorySection, { global: { plugins: [router] } })
  return { calls, router }
}

describe('HistorySection', () => {
  it('lists the trail and unfolds a line into its field-by-field diff', async () => {
    await open()

    expect(await screen.findByText('Élément modifié')).toBeTruthy()
    expect(screen.getByText('editor')).toBeTruthy()
    expect(screen.queryByText('db-02')).toBeNull() // folded until asked
    await fireEvent.click(screen.getByText('Voir les champs'))
    expect(screen.getByText('db-02')).toBeTruthy()
  })

  it('reads its filters from the URL', async () => {
    const { calls } = await open('?kind=element&entity=abc&actor=editor&action=element.updated')

    await screen.findByText('Élément modifié')
    const query = calls[0]?.url.searchParams
    expect(query?.get('entity_kind')).toBe('element')
    expect(query?.get('entity_id')).toBe('abc')
    expect(query?.get('actor')).toBe('editor')
    expect(query?.get('action')).toBe('element.updated')
  })

  it('walks to the next page by writing the cursor in the URL', async () => {
    const { router } = await open('', { items: [anAuditEvent()], next_cursor: 'page-2' })

    await fireEvent.click(await screen.findByRole('button', { name: 'Page suivante' }))

    expect(router.currentRoute.value.query.cursor).toBe('page-2')
  })

  it('links an element line to the element', async () => {
    await open()

    const link = await screen.findByRole('link', { name: "Voir l'élément" })
    expect(link.getAttribute('href')).toContain('element=22222222-2222-4222-8222-222222222222')
  })
})
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd frontend && npm test -- --run tests/FieldDiff.spec.ts tests/useAuditLog.spec.ts tests/HistorySection.spec.ts`
Expected: FAIL — « Failed to resolve import "../src/features/history/FieldDiff.vue" ».

- [ ] **Step 3: Implémenter**

```ts
// frontend/src/features/history/auditLabels.ts
// Translation tables, and not one rule: what counts as a change, and which
// action exists, is the backend's. A label missing here is a TypeScript error
// the day the backend adds an action (the `Record` is exhaustive).
import type { components } from '../../api/schema'

export type AuditAction = components['schemas']['AuditAction']
export type EntityKind = components['schemas']['EntityKind']

export const ACTION_LABELS: Record<AuditAction, string> = {
  'element.created': 'Élément créé',
  'element.updated': 'Élément modifié',
  'element.deleted': 'Élément supprimé',
  'relationship.created': 'Relation créée',
  'relationship.deleted': 'Relation supprimée',
  'diagram.created': 'Diagramme créé',
  'diagram.updated': 'Diagramme renommé',
  'diagram.deleted': 'Diagramme supprimé',
  'diagram.layout_replaced': 'Diagramme mis en page',
  'document.attached': 'Document attaché',
  'document.revised': 'Document révisé',
  'document.discarded': 'Document retiré',
}

export const KIND_LABELS: Record<EntityKind, string> = {
  element: 'Élément',
  relationship: 'Relation',
  diagram: 'Diagramme',
  document: 'Document',
}
```

```ts
// frontend/src/features/history/useAuditLog.ts
// The audit trail (docs/adr/NNNN): one page at a time, the page named by the URL.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'
import type { AuditAction, EntityKind } from './auditLabels'

export type AuditEvent = components['schemas']['AuditEventRead']

export type AuditFilters = {
  kind?: EntityKind
  entity?: string
  actor?: string
  action?: AuditAction
  since?: string
  cursor?: string
}

/** An empty box in a form is "no filter", not a filter on the empty string. */
function given<T extends string>(value: T | undefined): T | undefined {
  return value ? value : undefined
}

export function useAuditLog() {
  const events = ref<AuditEvent[]>([])
  const nextCursor = ref<string | null>(null)
  // The URL changes faster than the network answers: the page on screen must be
  // the page the URL names now.
  const request = useLatestRequest()
  const { status, error } = request

  async function load(filters: AuditFilters, limit?: number): Promise<void> {
    await request.run(
      async (signal) =>
        unwrap(
          await api.GET('/audit', {
            params: {
              query: {
                entity_kind: given(filters.kind),
                entity_id: given(filters.entity),
                actor: given(filters.actor),
                action: given(filters.action),
                since: given(filters.since),
                cursor: given(filters.cursor),
                limit,
              },
            },
            signal,
          }),
        ),
      (page) => {
        events.value = page.items
        nextCursor.value = page.next_cursor
      },
      () => {
        events.value = []
        nextCursor.value = null
      },
    )
  }

  return { events, nextCursor, status, error, load }
}
```

```vue
<!-- frontend/src/features/history/FieldDiff.vue -->
<script setup lang="ts">
// A field-by-field diff, as the server computed it. This component draws; it
// never decides what counts as a change.
defineProps<{ changes: { field: string; before?: unknown; after?: unknown }[] }>()

function show(value: unknown): string {
  if (value === null || value === undefined) {
    return '—'
  }
  return typeof value === 'string' ? value : JSON.stringify(value)
}
</script>

<template>
  <p v-if="changes.length === 0" class="empty">Aucun champ modifié.</p>
  <table v-else class="diff">
    <thead>
      <tr>
        <th scope="col">Champ</th>
        <th scope="col">Avant</th>
        <th scope="col">Après</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="change in changes" :key="change.field">
        <th scope="row">{{ change.field }}</th>
        <td class="before">{{ show(change.before) }}</td>
        <td class="after">{{ show(change.after) }}</td>
      </tr>
    </tbody>
  </table>
</template>

<style scoped>
.diff {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  padding: 0.25rem 0.5rem;
  border-bottom: 1px solid var(--border);
  vertical-align: top;
  overflow-wrap: anywhere;
}
.before {
  color: var(--danger, #b3261e);
}
.after {
  color: var(--success, #1b6e3a);
}
.empty {
  opacity: 0.6;
  font-style: italic;
}
</style>
```

```vue
<!-- frontend/src/features/history/HistorySection.vue -->
<script setup lang="ts">
// Qui a changé quoi, et quand (docs/adr/NNNN).
//
// Les filtres et la page sont dans l'URL (`?kind=`, `?entity=`, `?actor=`,
// `?action=`, `?since=`, `?cursor=`) : une question qu'on partage et qu'on
// remonte avec « précédent ». Changer un filtre remplace l'entrée d'historique,
// tourner la page en pousse une.
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { ACTION_LABELS, KIND_LABELS, type AuditAction, type EntityKind } from './auditLabels'
import FieldDiff from './FieldDiff.vue'
import { useAuditLog, type AuditFilters } from './useAuditLog'

const route = useRoute()
const router = useRouter()
const log = useAuditLog()

function param(name: string): string {
  const value = route.query[name]
  return typeof value === 'string' ? value : ''
}

const filters = computed<AuditFilters>(() => ({
  kind: (param('kind') || undefined) as EntityKind | undefined,
  entity: param('entity') || undefined,
  actor: param('actor') || undefined,
  action: (param('action') || undefined) as AuditAction | undefined,
  since: param('since') || undefined,
  cursor: param('cursor') || undefined,
}))

const draft = ref({
  kind: param('kind'),
  actor: param('actor'),
  action: param('action'),
  since: param('since'),
})

watch(filters, (value) => void log.load(value), { immediate: true })

function apply(): void {
  const next: Record<string, string> = {}
  for (const [key, value] of Object.entries(draft.value)) {
    if (value) {
      next[key] = value
    }
  }
  if (param('entity')) {
    next.entity = param('entity')
  }
  // Un filtre changé repart de la première page : l'ancien curseur ne dit rien du nouveau.
  void router.replace({ query: next })
}

function nextPage(): void {
  if (log.nextCursor.value) {
    void router.push({ query: { ...route.query, cursor: log.nextCursor.value } })
  }
}

const actions = Object.entries(ACTION_LABELS) as [AuditAction, string][]
const kinds = Object.entries(KIND_LABELS) as [EntityKind, string][]

function when(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 19)}`
}
</script>

<template>
  <section aria-labelledby="history-title">
    <h2 id="history-title">Historique</h2>

    <form class="filters" @submit.prevent="apply">
      <label>
        Type
        <select v-model="draft.kind">
          <option value="">Tous</option>
          <option v-for="[value, label] in kinds" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label>
        Action
        <select v-model="draft.action">
          <option value="">Toutes</option>
          <option v-for="[value, label] in actions" :key="value" :value="value">{{ label }}</option>
        </select>
      </label>
      <label>
        Acteur
        <input v-model="draft.actor" type="text" maxlength="200" />
      </label>
      <label>
        Depuis
        <input v-model="draft.since" type="date" />
      </label>
      <button type="submit">Filtrer</button>
    </form>

    <p v-if="log.status.value === 'error'" role="alert">{{ log.error.value }}</p>
    <p v-else-if="log.status.value === 'ready' && log.events.value.length === 0" class="empty">
      Aucun événement.
    </p>

    <ol class="events">
      <li v-for="event in log.events.value" :key="event.id" class="event">
        <header>
          <strong>{{ ACTION_LABELS[event.action] }}</strong>
          <span>{{ event.actor_name }}</span>
          <time :datetime="event.at">{{ when(event.at) }}</time>
          <span class="channel">{{ event.channel }}</span>
          <RouterLink
            v-if="event.entity_kind === 'element' && event.entity_id"
            :to="{ name: 'elements', query: { element: event.entity_id } }"
          >
            Voir l'élément
          </RouterLink>
        </header>
        <details>
          <summary>Voir les champs</summary>
          <FieldDiff :changes="event.changes" />
        </details>
      </li>
    </ol>

    <button v-if="log.nextCursor.value" type="button" @click="nextPage">Page suivante</button>
  </section>
</template>

<style scoped>
.filters {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: end;
  margin-bottom: 1rem;
}
.events {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.5rem;
}
.event {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.6rem 0.8rem;
}
.event header {
  display: flex;
  flex-wrap: wrap;
  gap: 0.8rem;
  align-items: baseline;
}
.channel {
  opacity: 0.6;
  font-size: 0.8rem;
}
.empty {
  opacity: 0.6;
  font-style: italic;
}
</style>
```

`frontend/src/router/sections.ts`: ajouter à `GROUPS` `{ id: 'governance', label: 'Gouvernance' },` (après `storage`), et à la fin de `SECTIONS`:

```ts
  {
    path: '/history',
    name: 'history',
    label: 'Historique',
    summary: 'Qui a changé quoi, et quand : le journal d’audit du catalogue.',
    group: 'governance',
    view: () => import('../features/history/HistorySection.vue'),
  },
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/FieldDiff.spec.ts tests/useAuditLog.spec.ts tests/HistorySection.spec.ts tests/router.spec.ts tests/AppNav.spec.ts` — Expected: PASS.
Run: `cd frontend && npm run lint && npm run typecheck` — Expected: sans erreur.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/history frontend/src/router/sections.ts frontend/tests
git commit -m "feat(spa): history section with filters in the URL and field-by-field diffs" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 11: SPA — historique sur la fiche élément

**Files:**
- Create: `frontend/src/features/history/ElementHistory.vue`, `frontend/tests/ElementHistory.spec.ts`
- Modify: `frontend/src/features/elements/ElementDetail.vue` (imports lignes 9-13; template, juste avant `<p class="actions">`, ligne ~76)

**Interfaces:**
- Consumes: `useAuditLog`, `FieldDiff`, `ACTION_LABELS` (Task 10).
- Produces: `<ElementHistory :element-id="string" />` — se charge à l'ouverture du `<details>` (aucun appel réseau tant qu'il est fermé: les specs existantes d'`ElementDetail`, du catalogue et des diagrammes n'ont donc aucune route à ajouter).

- [ ] **Step 1: Écrire le test qui échoue**

```ts
// frontend/tests/ElementHistory.spec.ts
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import ElementHistory from '../src/features/history/ElementHistory.vue'
import { createAppRouter } from '../src/router'
import { anAuditEvent, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

async function mount() {
  const calls = stubApi([{ path: '/audit', body: { items: [anAuditEvent()], next_cursor: null } }])
  const router = createAppRouter(createMemoryHistory())
  const { container } = render(ElementHistory, {
    props: { elementId: '22222222-2222-4222-8222-222222222222' },
    global: { plugins: [router] },
  })
  return { calls, container }
}

/** Open the panel the way the browser does: the `open` attribute, then a `toggle` event. */
async function open(container: Element): Promise<void> {
  const details = container.querySelector('details')
  if (!details) {
    throw new Error('no <details> rendered')
  }
  details.open = true
  await fireEvent(details, new Event('toggle'))
}

describe('ElementHistory', () => {
  it('asks nothing until the panel is opened', async () => {
    const { calls } = await mount()

    expect(calls).toHaveLength(0)
  })

  it('then lists the trail of that element and links to the whole of it', async () => {
    const { calls, container } = await mount()

    await open(container)

    expect(await screen.findByText('Élément modifié')).toBeTruthy()
    expect(calls[0]?.url.searchParams.get('entity_kind')).toBe('element')
    expect(calls[0]?.url.searchParams.get('entity_id')).toBe('22222222-2222-4222-8222-222222222222')
    expect(screen.getByRole('link', { name: 'Tout l’historique' }).getAttribute('href')).toContain(
      'entity=22222222-2222-4222-8222-222222222222',
    )
  })
})
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd frontend && npm test -- --run tests/ElementHistory.spec.ts`
Expected: FAIL — « Failed to resolve import "../src/features/history/ElementHistory.vue" ».

- [ ] **Step 3: Implémenter**

```vue
<!-- frontend/src/features/history/ElementHistory.vue -->
<script setup lang="ts">
// L'historique d'un élément, sur sa fiche. Fermé par défaut et chargé à
// l'ouverture : la fiche s'ouvre sans une requête de plus.
import { RouterLink } from 'vue-router'

import { ACTION_LABELS } from './auditLabels'
import FieldDiff from './FieldDiff.vue'
import { useAuditLog } from './useAuditLog'

const props = defineProps<{ elementId: string }>()
const log = useAuditLog()

/** Ten lines are a glance; the section holds the rest. */
const GLANCE = 10

function onToggle(event: Event): void {
  if ((event.target as HTMLDetailsElement).open) {
    void log.load({ kind: 'element', entity: props.elementId }, GLANCE)
  }
}
</script>

<template>
  <details class="history" @toggle="onToggle">
    <summary>Historique</summary>
    <p v-if="log.status.value === 'error'" role="alert">{{ log.error.value }}</p>
    <ul>
      <li v-for="event in log.events.value" :key="event.id">
        <strong>{{ ACTION_LABELS[event.action] }}</strong>
        — {{ event.actor_name }}, {{ event.at.slice(0, 10) }}
        <FieldDiff :changes="event.changes" />
      </li>
    </ul>
    <RouterLink :to="{ name: 'history', query: { kind: 'element', entity: elementId } }">
      Tout l’historique
    </RouterLink>
  </details>
</template>

<style scoped>
.history {
  border-top: 1px solid var(--border);
  padding-top: 0.8rem;
}
ul {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.6rem;
}
</style>
```

Dans `ElementDetail.vue`: ajouter `import ElementHistory from '../history/ElementHistory.vue'` et, juste avant `<p class="actions">`, `<ElementHistory :element-id="element.id" />`.

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/ElementHistory.spec.ts tests/ElementDetail.spec.ts tests/ElementCatalogue.spec.ts tests/DiagramsSection.spec.ts` — Expected: PASS (aucune requête de plus tant que le panneau est fermé).
Run: `cd frontend && npm run lint && npm run typecheck` — Expected: sans erreur.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/history/ElementHistory.vue frontend/src/features/elements/ElementDetail.vue frontend/tests/ElementHistory.spec.ts
git commit -m "feat(spa): element history panel on the element sheet" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Vérification du lot 1 et ADR du journal d'audit

**Files:**
- Create: `docs/adr/NNNN-journal-d-audit.md` (NNNN = prochain numéro libre de `docs/adr/` à l'exécution; 0042 aujourd'hui)
- Modify: les occurrences `docs/adr/NNNN` laissées dans le code des tâches 1 à 9 (voir Step 1)

**Interfaces:**
- Consumes: tout le lot 1.
- Produces: l'ADR; un lot 1 livrable seul.

- [ ] **Step 1: Fixer le numéro de l'ADR**

Run: `ls docs/adr | tail -3` — noter le prochain numéro libre, `N1`. Run: `grep -rln "docs/adr/NNNN" backend/src backend/tests backend/migrations frontend/src frontend/tests` puis remplacer `NNNN` par `N1` dans ces fichiers (`sed -i '' 's#docs/adr/NNNN#docs/adr/N1#g'` sur la liste obtenue, après l'avoir relue). Expected: `grep -rn "NNNN" backend/src frontend/src` ne retourne plus rien pour le lot 1.

- [ ] **Step 2: Écrire l'ADR**

```markdown
---
titre: Journal d'audit append-only écrit par les services dans la transaction de l'écriture
date: 2026-10-10
statut: Proposition
affects: domain/audit.py, services/audit.py, services/architecture.py, services/documents.py, services/diagrams.py, repositories/audit_store.py, api/audit.py, mcp/server.py, migrations 0009
---

# Journal d'audit append-only écrit par les services dans la transaction de l'écriture

## Contexte

Le catalogue sait ce qu'il contient, pas ce qui l'a fait changer. Les lignes de
log de `services/` (`action=created`, docs/adr/0021) sont jetables, non
interrogeables et sans état avant/après. Les agents MCP écrivent dans le même
graphe que le SPA et le pipeline: « qui a changé quoi, quand, par quelle porte »
est devenu une question d'exploitation. Le déploiement n'a qu'une base
PostgreSQL et un seul rôle propriétaire `ea` (docs/adr/0029, 0033).

## Décision

- Une table `audit_events` (migration 0009), `entity_id` **sans clé étrangère**:
  l'événement doit survivre à l'entité.
- **Un événement par cas d'usage**, pas par ligne SQL. `delete_element` écrit un
  `element.deleted` dont `before.cascaded` liste les relations, documents et
  diagrammes emportés par les clés étrangères.
- Les `AuditEvent` sont construits par `services/` (`AuditTrail`): acteur du
  `Caller` vérifié, horloge injectée, `request_id` du contexte de log, canal
  (`http|mcp|system`) posé par l'adaptateur. Jamais un paramètre d'appelant.
- Les dépôts reçoivent `audit=` (keyword-only, défaut `()`) sur leurs écritures
  et l'insèrent par `write_events(session, events)` dans **leur** `begin()`,
  après le `flush` de l'écriture: l'événement existe si et seulement si
  l'écriture a tenu. Un refus n'écrit rien.
- `before`/`after` sont les vues de lecture des entités; le texte d'un document
  n'est jamais copié (nom, taille, sha256).
- Immuabilité: triggers `BEFORE UPDATE OR DELETE` et `BEFORE TRUNCATE` qui
  lèvent. Le `REVOKE` n'est pas posé: un seul rôle propriétaire existe, qui
  pourrait se redonner le droit.
- Rétro-remplissage dans la migration: un événement `*.created` par élément et
  lien existants, `ea-system`, `correlation='backfill'`.
- Lecture: `GET /audit` (pagination par curseur `(at, id)` opaque), `GET
  /elements/{id}/history`, outils MCP `list_audit_events` et
  `get_element_history`, écran SPA « Historique ». Tout appelant authentifié lit.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Triggers PostgreSQL qui écrivent l'audit | Rien à oublier côté code | Ne connaissent ni l'acteur du jeton, ni le canal, ni le cas d'usage; un événement par ligne | Écartée |
| Audit écrit après coup par le service (2e transaction) | Aucun changement de dépôt | Écriture et trace peuvent diverger au crash | Écartée |
| `REVOKE UPDATE, DELETE` seul | Standard | Sans effet sur le rôle propriétaire unique | Écartée (trigger; REVOKE à faire hors de ce repo) |
| Chaîne de hachage du journal | Détecte une falsification par opérateur | Hors périmètre (spec R2); `pg_dump` suffit | Écartée |

## Conséquences

- Toute nouvelle écriture d'un dépôt doit prendre `audit=`; oublier de le passer
  ne casse rien — c'est `tests/unit/test_audit_writes.py` qui l'attrape.
- Les écritures de dépôt ouvrent chacune leur transaction: l'atomicité couvre
  *une* écriture et son événement, pas plusieurs cas d'usage. Un cas d'usage à
  plusieurs écritures dans une même transaction (sous-projet 3, échanges)
  demandera une interface commune de session partagée, à concevoir avec lui.
- Volume ~2 Ko/événement; rétention manuelle (`docs/audit-retention.md`).
- À faire hors de ce repo: rôle PostgreSQL d'écriture `INSERT, SELECT` seul.

## Références

- ADR liés: [0021](0021-des-logs-que-quelqu-un-peut-lire.md), [0029](0029-nouvelle-adresse-du-cluster-et-postgresql-sur-le-mac.md), [0032](0032-authentification-par-keycloak.md), [0033](0033-postgresql-seul-pour-le-graphe.md)
- Code: `backend/src/ea/domain/audit.py`, `backend/src/ea/repositories/audit_store.py`, `backend/migrations/versions/0009_audit_events.py`
```

(Remplacer `N1`-spécifique: le nom de fichier est `docs/adr/N1-journal-d-audit.md`.)

- [ ] **Step 3: Vérifier le lot**

Run: `make check` — Expected: PASS (lint, types, `openapi-check`, tests avec plancher 90 %, Vitest, pipelines).
Run: `make test-integration` — Expected: PASS (migration 0009 appliquée puis annulée; atomicité; immuabilité).

- [ ] **Step 4: Commit**

```bash
git add docs/adr backend frontend
git commit -m "docs(adr): append-only audit trail written in the transaction of the write" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 2 — Baselines globales (B4)

> Le périmètre « paquet » n'est pas planifié ici (Écart 8). Dans ce lot, `docs/adr/BBBB` désigne l'ADR des baselines, fixé à la Task 23.

### Task 13: Domaine `baselines` (instantané, diff)

**Files:**
- Create: `backend/src/ea/domain/baselines.py`, `backend/tests/unit/test_baselines_domain.py`
- Modify: `backend/src/ea/domain/audit.py` (`EntityKind` et `AuditAction`), `backend/src/ea/domain/errors.py` (fin de fichier)

**Interfaces:**
- Consumes: `Element`, `Relationship`, `FieldChange`, `diff_fields` (Task 1).
- Produces:
  - `Baseline(id, name, description, created_by_sub, created_by, created_at, element_count=0, relationship_count=0, box_count=0)`; `Baseline.create(*, name, description, created_by_sub, created_by, now)`; `.with_counts(elements, relationships, boxes)`; `baseline_view(baseline) -> dict`
  - `BoxSnapshot(diagram_id, element_id, x, y, width, height)`; `ModelSnapshot(elements, relationships, boxes=())`
  - `EntityRef(kind, id, label, type)`; `Modified(kind, id, label, type, changes)`; `BaselineDiff(elements_added, elements_removed, elements_modified, relationships_added, relationships_removed, relationships_modified)`; `diff_snapshots(older, newer) -> BaselineDiff`
  - `element_content(element) -> dict`, `relationship_content(relationship) -> dict`
  - `AuditAction.BASELINE_CREATED|BASELINE_DELETED|BASELINE_RESTORED` (`baseline.created|deleted|restored`), `EntityKind.BASELINE`
  - erreurs: `BaselineNotFoundError`, `DuplicateBaselineError`, `RestoreBlockedError`, `RestoreConflictError`

- [ ] **Step 1: Écrire le test qui échoue**

```python
# backend/tests/unit/test_baselines_domain.py
"""A snapshot of the model, and what separates two of them (docs/adr/BBBB)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, EntityKind, FieldChange
from ea.domain.baselines import Baseline, ModelSnapshot, diff_snapshots
from ea.domain.model import Element, Relationship

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(name: str, **overrides: object) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW, **overrides)  # type: ignore[arg-type]


class TestABaseline:
    def test_a_name_is_trimmed_and_cannot_be_blank(self) -> None:
        made = Baseline.create(
            name="  Avant migration ", description="", created_by_sub="s", created_by="n", now=NOW
        )

        assert made.name == "Avant migration"
        with pytest.raises(ValueError, match="blank"):
            Baseline.create(name="  ", description="", created_by_sub="s", created_by="n", now=NOW)

    def test_the_actions_on_baselines_are_about_baselines(self) -> None:
        assert AuditAction.BASELINE_RESTORED.kind is EntityKind.BASELINE
        assert AuditAction.BASELINE_CREATED.value == "baseline.created"


class TestTheDiff:
    def test_what_appeared_vanished_or_changed(self) -> None:
        kept, gone, changed = an_element("kept"), an_element("gone"), an_element("billing")
        new = an_element("new")
        older = ModelSnapshot(elements=(kept, gone, changed), relationships=())
        renamed = replace(changed, name="invoicing", properties={"owner": "ops"})
        newer = ModelSnapshot(elements=(kept, renamed, new), relationships=())

        diff = diff_snapshots(older, newer)

        assert [e.label for e in diff.elements_added] == ["new"]
        assert [e.label for e in diff.elements_removed] == ["gone"]
        (modified,) = diff.elements_modified
        assert modified.id == changed.id
        assert modified.changes == (
            FieldChange("name", "billing", "invoicing"),
            FieldChange("properties.owner", None, "ops"),
        )

    def test_a_timestamp_alone_is_not_a_modification(self) -> None:
        element = an_element("a")
        touched = replace(element, updated_at=NOW + timedelta(days=1))

        diff = diff_snapshots(
            ModelSnapshot((element,), ()), ModelSnapshot((touched,), ())
        )

        assert diff.elements_modified == ()

    def test_relationships_are_labelled_with_the_names_of_their_ends(self) -> None:
        left, right = an_element("a"), an_element("b")
        link = Relationship.between(R.ASSOCIATION, left, right, now=NOW)
        older = ModelSnapshot((left, right), ())
        newer = ModelSnapshot((left, right), (link,))

        diff = diff_snapshots(older, newer)

        (added,) = diff.relationships_added
        assert (added.kind, added.type) == ("relationship", "association")
        assert "a" in added.label and "b" in added.label
        assert diff_snapshots(newer, older).relationships_removed[0].id == link.id

    def test_two_equal_snapshots_have_an_empty_diff(self) -> None:
        snapshot = ModelSnapshot((an_element("a"),), ())

        assert diff_snapshots(snapshot, snapshot).is_empty
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_baselines_domain.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.domain.baselines'`.

- [ ] **Step 3: Étendre `audit.py` et `errors.py`**

Dans `EntityKind` ajouter `BASELINE = "baseline"`; dans `AuditAction` ajouter, après `DOCUMENT_DISCARDED`:

```python
    BASELINE_CREATED = "baseline.created"
    BASELINE_DELETED = "baseline.deleted"
    BASELINE_RESTORED = "baseline.restored"
```

À la fin de `errors.py`:

```python


class BaselineNotFoundError(DomainError):
    """A baseline referenced by id is absent from the store."""


class DuplicateBaselineError(DomainError):
    """A baseline name is already taken."""


class RestoreBlockedError(DomainError):
    """A restoration would change an element that is locked — unlock it first."""


class RestoreConflictError(DomainError):
    """A restoration the catalogue refused halfway (uniqueness, a concurrent write).

    Everything was rolled back; reading the plan again and retrying is safe.
    """
```

- [ ] **Step 4: Implémenter le domaine**

```python
# backend/src/ea/domain/baselines.py
"""A baseline: a frozen copy of the model, comparable and restorable — docs/adr/BBBB.

Pure. A snapshot holds *values* (the elements, the relationships, the boxes of
diagrams), so a diff is the comparison of two sets and needs no database. What
counts as a modification is the content of an entity — never its timestamps.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from uuid import UUID, uuid4

from ea.domain.audit import FieldChange, diff_fields
from ea.domain.model import Element, Relationship

MAX_BASELINE_NAME_LENGTH: Final = 200


@dataclass(frozen=True, slots=True)
class Baseline:
    """A named snapshot. The counts are read from the copy, never written."""

    id: UUID
    name: str
    description: str
    created_by_sub: str
    created_by: str
    created_at: datetime
    element_count: int = 0
    relationship_count: int = 0
    box_count: int = 0

    @classmethod
    def create(
        cls, *, name: str, description: str, created_by_sub: str, created_by: str, now: datetime
    ) -> Baseline:
        cleaned = name.strip()
        if not cleaned:
            msg = "a baseline name cannot be blank"
            raise ValueError(msg)
        if len(cleaned) > MAX_BASELINE_NAME_LENGTH:
            msg = f"a baseline name is at most {MAX_BASELINE_NAME_LENGTH} characters"
            raise ValueError(msg)
        return cls(
            id=uuid4(),
            name=cleaned,
            description=description.strip(),
            created_by_sub=created_by_sub,
            created_by=created_by,
            created_at=now,
        )

    def with_counts(self, elements: int, relationships: int, boxes: int) -> Baseline:
        return Baseline(
            self.id,
            self.name,
            self.description,
            self.created_by_sub,
            self.created_by,
            self.created_at,
            elements,
            relationships,
            boxes,
        )


def baseline_view(baseline: Baseline) -> dict[str, Any]:
    return {"id": str(baseline.id), "name": baseline.name, "description": baseline.description}


@dataclass(frozen=True, slots=True)
class BoxSnapshot:
    """One box of one diagram, as captured."""

    diagram_id: UUID
    element_id: UUID
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    elements: tuple[Element, ...]
    relationships: tuple[Relationship, ...]
    boxes: tuple[BoxSnapshot, ...] = ()


def element_content(element: Element) -> dict[str, Any]:
    """What makes two versions of an element different — not its timestamps."""
    return {
        "element_type": element.element_type.value,
        "name": element.name,
        "description": element.description,
        "documentation": element.documentation,
        "properties": dict(element.properties),
    }


def relationship_content(relationship: Relationship) -> dict[str, Any]:
    return {
        "relationship_type": relationship.relationship_type.value,
        "source_id": str(relationship.source_id),
        "target_id": str(relationship.target_id),
        "name": relationship.name,
        "access_type": relationship.access_type.value if relationship.access_type else None,
        "directed": relationship.directed,
        "properties": dict(relationship.properties),
    }


@dataclass(frozen=True, slots=True)
class EntityRef:
    kind: str  # "element" | "relationship"
    id: UUID
    label: str
    type: str


@dataclass(frozen=True, slots=True)
class Modified:
    kind: str
    id: UUID
    label: str
    type: str
    changes: tuple[FieldChange, ...]


@dataclass(frozen=True, slots=True)
class BaselineDiff:
    elements_added: tuple[EntityRef, ...]
    elements_removed: tuple[EntityRef, ...]
    elements_modified: tuple[Modified, ...]
    relationships_added: tuple[EntityRef, ...]
    relationships_removed: tuple[EntityRef, ...]
    relationships_modified: tuple[Modified, ...]

    @property
    def is_empty(self) -> bool:
        return not any(
            (
                self.elements_added,
                self.elements_removed,
                self.elements_modified,
                self.relationships_added,
                self.relationships_removed,
                self.relationships_modified,
            )
        )


def _link_label(link: Relationship, names: dict[UUID, str]) -> str:
    source = names.get(link.source_id, str(link.source_id))
    target = names.get(link.target_id, str(link.target_id))
    return f"{source} —{link.relationship_type.value}→ {target}"


def diff_snapshots(older: ModelSnapshot, newer: ModelSnapshot) -> BaselineDiff:
    """`added` is in `newer` only, `removed` in `older` only, `modified` in both and different."""
    names = {e.id: e.name for e in (*older.elements, *newer.elements)}
    old_el = {e.id: e for e in older.elements}
    new_el = {e.id: e for e in newer.elements}
    old_rel = {r.id: r for r in older.relationships}
    new_rel = {r.id: r for r in newer.relationships}

    def element_ref(element: Element) -> EntityRef:
        return EntityRef("element", element.id, element.name, element.element_type.value)

    def link_ref(link: Relationship) -> EntityRef:
        return EntityRef(
            "relationship", link.id, _link_label(link, names), link.relationship_type.value
        )

    def by_label(refs: Sequence[EntityRef]) -> tuple[EntityRef, ...]:
        return tuple(sorted(refs, key=lambda ref: (ref.label, str(ref.id))))

    elements_modified = []
    for identifier in old_el.keys() & new_el.keys():
        changes = diff_fields(element_content(old_el[identifier]), element_content(new_el[identifier]))
        if changes:
            after = new_el[identifier]
            elements_modified.append(
                Modified("element", identifier, after.name, after.element_type.value, changes)
            )
    relationships_modified = []
    for identifier in old_rel.keys() & new_rel.keys():
        changes = diff_fields(
            relationship_content(old_rel[identifier]), relationship_content(new_rel[identifier])
        )
        if changes:
            after_link = new_rel[identifier]
            relationships_modified.append(
                Modified(
                    "relationship",
                    identifier,
                    _link_label(after_link, names),
                    after_link.relationship_type.value,
                    changes,
                )
            )

    return BaselineDiff(
        elements_added=by_label([element_ref(new_el[i]) for i in new_el.keys() - old_el.keys()]),
        elements_removed=by_label([element_ref(old_el[i]) for i in old_el.keys() - new_el.keys()]),
        elements_modified=tuple(sorted(elements_modified, key=lambda m: (m.label, str(m.id)))),
        relationships_added=by_label([link_ref(new_rel[i]) for i in new_rel.keys() - old_rel.keys()]),
        relationships_removed=by_label(
            [link_ref(old_rel[i]) for i in old_rel.keys() - new_rel.keys()]
        ),
        relationships_modified=tuple(
            sorted(relationships_modified, key=lambda m: (m.label, str(m.id)))
        ),
    )
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_baselines_domain.py tests/unit/test_audit_domain.py -x` — Expected: PASS. Run: `cd backend && uv run mypy src/ea/domain` — Expected: `Success`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain backend/tests/unit/test_baselines_domain.py
git commit -m "feat(baselines): pure snapshot and diff domain, baseline audit actions" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 14: `plan_restore` (pur)

**Files:**
- Modify: `backend/src/ea/domain/baselines.py` (ajouter à la fin)
- Test: `backend/tests/unit/test_restore_plan.py`

**Interfaces:**
- Consumes: Task 13, `validate_relationship` (`ea.domain.archimate`), `IllegalRelationshipError`.
- Produces:
  - `ElementUpdate(current: Element, restored: Element, changes: tuple[FieldChange, ...])`
  - `RestorePlan(elements_to_create=(), elements_to_update=(), elements_to_delete=(), relationships_to_create=(), relationships_to_delete=(), cascaded_relationships=0, boxes_to_restore=(), documents_lost=0, boxes_lost=0, warnings=(), blocked=())` avec `.is_blocked`, `.is_noop`
  - `plan_restore(snapshot, current, *, keep_new, locked=frozenset(), diagrams_present=frozenset()) -> RestorePlan`

- [ ] **Step 1: Écrire le test qui échoue**

```python
# backend/tests/unit/test_restore_plan.py
"""What a restoration would do — every case, decided without a database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.baselines import BoxSnapshot, ModelSnapshot, plan_restore
from ea.domain.model import Element, Relationship

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def node(name: str) -> Element:
    return Element.create(element_type=E.NODE, name=name, now=NOW)


def link(source: Element, target: Element) -> Relationship:
    return Relationship.between(R.ASSOCIATION, source, target, now=NOW)


class TestElements:
    def test_what_vanished_is_recreated_with_its_original_id(self) -> None:
        gone = node("gone")

        plan = plan_restore(
            ModelSnapshot((gone,), ()), ModelSnapshot((), ()), keep_new=False
        )

        assert [e.id for e in plan.elements_to_create] == [gone.id]

    def test_what_changed_is_put_back_field_by_field(self) -> None:
        original = node("billing")
        edited = replace(original, name="invoicing")

        plan = plan_restore(
            ModelSnapshot((original,), ()), ModelSnapshot((edited,), ()), keep_new=False
        )

        (update,) = plan.elements_to_update
        assert update.restored.name == "billing"
        assert [c.field for c in update.changes] == ["name"]

    def test_what_is_new_is_deleted_unless_asked_to_keep(self) -> None:
        newer = node("new")
        snapshot, current = ModelSnapshot((), ()), ModelSnapshot((newer,), ())

        exact = plan_restore(snapshot, current, keep_new=False)
        kept = plan_restore(snapshot, current, keep_new=True)

        assert [e.id for e in exact.elements_to_delete] == [newer.id]
        assert kept.elements_to_delete == ()

    def test_an_identical_model_is_a_no_op(self) -> None:
        a = node("a")

        assert plan_restore(
            ModelSnapshot((a,), ()), ModelSnapshot((a,), ()), keep_new=False
        ).is_noop


class TestRelationships:
    def test_missing_links_come_back_and_stray_ones_between_known_elements_go(self) -> None:
        a, b = node("a"), node("b")
        wanted = link(a, b)
        stray = Relationship.between(R.ASSOCIATION, a, b, now=NOW, name="stray")

        plan = plan_restore(
            ModelSnapshot((a, b), (wanted,)),
            ModelSnapshot((a, b), (stray,)),
            keep_new=False,
        )

        assert [r.id for r in plan.relationships_to_create] == [wanted.id]
        assert [r.id for r in plan.relationships_to_delete] == [stray.id]

    def test_a_link_to_a_kept_new_element_is_kept_too(self) -> None:
        a, fresh = node("a"), node("fresh")
        to_fresh = link(a, fresh)

        plan = plan_restore(
            ModelSnapshot((a,), ()),
            ModelSnapshot((a, fresh), (to_fresh,)),
            keep_new=True,
        )

        assert plan.relationships_to_delete == ()

    def test_links_that_die_with_a_deleted_element_are_counted_not_listed(self) -> None:
        a, fresh = node("a"), node("fresh")
        to_fresh = link(a, fresh)

        plan = plan_restore(
            ModelSnapshot((a,), ()),
            ModelSnapshot((a, fresh), (to_fresh,)),
            keep_new=False,
        )

        assert plan.relationships_to_delete == ()
        assert plan.cascaded_relationships == 1

    def test_a_modified_link_is_replaced_under_the_same_id(self) -> None:
        a, b = node("a"), node("b")
        original = link(a, b)
        edited = replace(original, name="renamed")

        plan = plan_restore(
            ModelSnapshot((a, b), (original,)),
            ModelSnapshot((a, b), (edited,)),
            keep_new=False,
        )

        assert [r.id for r in plan.relationships_to_delete] == [original.id]
        assert [r.name for r in plan.relationships_to_create] == [""]

    def test_a_link_that_is_no_longer_legal_is_a_warning_not_a_failure(self) -> None:
        data, function = (
            Element.create(element_type=E.DATA_OBJECT, name="Invoice", now=NOW),
            Element.create(element_type=E.APPLICATION_FUNCTION, name="Invoicing", now=NOW),
        )
        illegal = replace(
            Relationship.between(R.ASSOCIATION, data, function, now=NOW),
            relationship_type=R.ACCESS,
        )

        plan = plan_restore(
            ModelSnapshot((data, function), (illegal,)),
            ModelSnapshot((data, function), ()),
            keep_new=False,
        )

        assert plan.relationships_to_create == ()
        assert len(plan.warnings) == 1 and "not permitted" in plan.warnings[0]


class TestLocksAndBoxes:
    def test_a_locked_element_that_would_change_blocks_the_plan(self) -> None:
        original = node("billing")
        edited = replace(original, name="invoicing")

        plan = plan_restore(
            ModelSnapshot((original,), ()),
            ModelSnapshot((edited,), ()),
            keep_new=False,
            locked=frozenset({original.id}),
        )

        assert plan.is_blocked
        assert [e.id for e in plan.blocked] == [original.id]

    def test_a_locked_element_nothing_touches_does_not_block(self) -> None:
        a = node("a")

        plan = plan_restore(
            ModelSnapshot((a,), ()),
            ModelSnapshot((a,), ()),
            keep_new=False,
            locked=frozenset({a.id}),
        )

        assert not plan.is_blocked

    def test_boxes_come_back_only_on_a_diagram_that_still_exists(self) -> None:
        a = node("a")
        kept_diagram, lost_diagram = uuid4(), uuid4()
        boxes = (
            BoxSnapshot(kept_diagram, a.id, 1, 2, 132, 46),
            BoxSnapshot(lost_diagram, a.id, 3, 4, 132, 46),
        )

        plan = plan_restore(
            ModelSnapshot((a,), (), boxes),
            ModelSnapshot((a,), ()),
            keep_new=False,
            diagrams_present=frozenset({kept_diagram}),
        )

        assert [b.diagram_id for b in plan.boxes_to_restore] == [kept_diagram]
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_restore_plan.py -x`
Expected: FAIL — `ImportError: cannot import name 'plan_restore'`.

- [ ] **Step 3: Implémenter** (ajouter à la fin de `domain/baselines.py`; ajouter `from dataclasses import field` aux imports, `from ea.domain.archimate import validate_relationship`, `from ea.domain.errors import IllegalRelationshipError`)

```python
@dataclass(frozen=True, slots=True)
class ElementUpdate:
    """An element to put back: what it is now, what it becomes, and the fields between."""

    current: Element
    restored: Element
    changes: tuple[FieldChange, ...]


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """What a restoration would do. `documents_lost` is filled in by the service,
    which can ask the store; everything else is decided here."""

    elements_to_create: tuple[Element, ...] = ()
    elements_to_update: tuple[ElementUpdate, ...] = ()
    elements_to_delete: tuple[Element, ...] = ()
    relationships_to_create: tuple[Relationship, ...] = ()
    relationships_to_delete: tuple[Relationship, ...] = ()
    cascaded_relationships: int = 0
    boxes_to_restore: tuple[BoxSnapshot, ...] = ()
    documents_lost: int = 0
    boxes_lost: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)
    blocked: tuple[Element, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked)

    @property
    def is_noop(self) -> bool:
        return not any(
            (
                self.elements_to_create,
                self.elements_to_update,
                self.elements_to_delete,
                self.relationships_to_create,
                self.relationships_to_delete,
                self.boxes_to_restore,
            )
        )


def plan_restore(
    snapshot: ModelSnapshot,
    current: ModelSnapshot,
    *,
    keep_new: bool,
    locked: frozenset[UUID] = frozenset(),
    diagrams_present: frozenset[UUID] = frozenset(),
) -> RestorePlan:
    """Make `current` equal `snapshot` — over the elements the snapshot knows.

    `keep_new` keeps what was created since the baseline (and every link that
    touches it); otherwise it is deleted, and its links go with it by the
    foreign keys — counted in `cascaded_relationships`, not listed. A link the
    metamodel no longer allows is a warning and is skipped. An element in
    `locked` that the plan would change, delete or link blocks it.
    """
    snap_el = {e.id: e for e in snapshot.elements}
    cur_el = {e.id: e for e in current.elements}

    to_create = tuple(snap_el[i] for i in snap_el.keys() - cur_el.keys())
    updates = []
    for identifier in snap_el.keys() & cur_el.keys():
        changes = diff_fields(
            element_content(cur_el[identifier]), element_content(snap_el[identifier])
        )
        if changes:
            updates.append(ElementUpdate(cur_el[identifier], snap_el[identifier], changes))
    to_delete = () if keep_new else tuple(cur_el[i] for i in cur_el.keys() - snap_el.keys())
    deleted_ids = {e.id for e in to_delete}

    snap_rel = {r.id: r for r in snapshot.relationships}
    cur_rel = {r.id: r for r in current.relationships}
    perimeter = snap_el.keys()
    links_to_delete = [
        link
        for identifier, link in cur_rel.items()
        if (
            identifier not in snap_rel
            and link.source_id in perimeter
            and link.target_id in perimeter
        )
        or (
            identifier in snap_rel
            and relationship_content(link) != relationship_content(snap_rel[identifier])
        )
    ]
    deleting = {link.id for link in links_to_delete}
    wanted = [
        link
        for identifier, link in snap_rel.items()
        if identifier not in cur_rel or identifier in deleting
    ]
    warnings: list[str] = []
    links_to_create: list[Relationship] = []
    for link in wanted:
        try:
            validate_relationship(
                link.relationship_type,
                link.source_type,
                link.target_type,
                same_element=link.source_id == link.target_id,
            )
        except IllegalRelationshipError as refusal:
            warnings.append(f"relationship {link.id} skipped: {refusal}")
        else:
            links_to_create.append(link)
    cascaded = sum(
        1
        for identifier, link in cur_rel.items()
        if identifier not in deleting
        and (link.source_id in deleted_ids or link.target_id in deleted_ids)
    )

    present_after = set(snap_el) | (set(cur_el) if keep_new else set())
    current_boxes = {(b.diagram_id, b.element_id): b for b in current.boxes}
    boxes = tuple(
        box
        for box in snapshot.boxes
        if box.diagram_id in diagrams_present
        and box.element_id in present_after
        and current_boxes.get((box.diagram_id, box.element_id)) != box
    )
    boxes_lost = sum(1 for box in current.boxes if box.element_id in deleted_ids)

    touched = (
        {u.current.id for u in updates}
        | deleted_ids
        | {end for link in (*links_to_delete, *links_to_create) for end in (link.source_id, link.target_id)}
    )
    blocked = tuple(
        sorted((cur_el[i] for i in touched if i in locked and i in cur_el), key=lambda e: e.name)
    )

    return RestorePlan(
        elements_to_create=to_create,
        elements_to_update=tuple(updates),
        elements_to_delete=to_delete,
        relationships_to_create=tuple(links_to_create),
        relationships_to_delete=tuple(links_to_delete),
        cascaded_relationships=cascaded,
        boxes_to_restore=boxes,
        boxes_lost=boxes_lost,
        warnings=tuple(warnings),
        blocked=blocked,
    )
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_restore_plan.py tests/unit/test_baselines_domain.py -x` — Expected: PASS. Run: `cd backend && uv run mypy src/ea/domain/baselines.py` — Expected: `Success`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/domain/baselines.py backend/tests/unit/test_restore_plan.py
git commit -m "feat(baselines): pure restoration planner" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 15: Tables `baseline_*` et migration 0010

**Files:**
- Create: `backend/src/ea/db/models/baseline.py`, `backend/migrations/versions/0010_baselines.py`, `backend/tests/integration/test_baseline_schema.py`
- Modify: `backend/src/ea/db/models/__init__.py`

**Interfaces:**
- Produces: `BaselineRecord`, `BaselineElementRecord`, `BaselineRelationshipRecord`, `BaselineBoxRecord` (colonnes ci-dessous), consommés par Task 16.

- [ ] **Step 1: Écrire le test qui échoue** (`tests/integration/test_baseline_schema.py`)

```python
"""The baseline tables as the server holds them (docs/adr/BBBB)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


async def test_a_baseline_name_is_unique_and_its_copy_goes_with_it(
    engine_at_head: AsyncEngine,
) -> None:
    baseline_id, element_id = uuid4(), uuid4()
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO baselines (id, name, created_by_sub, created_by, created_at) "
                "VALUES (:id, 'v1', 's', 'n', :at)"
            ),
            {"id": baseline_id, "at": AT},
        )
        await connection.execute(
            text(
                "INSERT INTO baseline_elements (baseline_id, id, element_type, layer, aspect, "
                "name, created_at, updated_at) "
                "VALUES (:b, :e, 'node', 'technology', 'active_structure', 'n', :at, :at)"
            ),
            {"b": baseline_id, "e": element_id, "at": AT},
        )

    with pytest.raises(IntegrityError):
        async with engine_at_head.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO baselines (id, name, created_by_sub, created_by, created_at) "
                    "VALUES (:id, 'v1', 's', 'n', :at)"
                ),
                {"id": uuid4(), "at": AT},
            )

    async with engine_at_head.begin() as connection:
        await connection.execute(text("DELETE FROM baselines"))
    async with engine_at_head.connect() as connection:
        remaining = (
            await connection.execute(text("SELECT count(*) FROM baseline_elements"))
        ).scalar_one()
    assert remaining == 0


async def test_a_baseline_keeps_no_foreign_key_to_the_live_graph(
    engine_at_head: AsyncEngine,
) -> None:
    """The copy must outlive the elements it copied."""
    async with engine_at_head.connect() as connection:
        rows = await connection.execute(
            text(
                "SELECT conname FROM pg_constraint WHERE contype = 'f' "
                "AND conrelid::regclass::text LIKE 'baseline%'"
            )
        )
    assert {row[0] for row in rows} == {
        "fk_baseline_elements_baseline_id_baselines",
        "fk_baseline_relationships_baseline_id_baselines",
        "fk_baseline_boxes_baseline_id_baselines",
    }
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `make test-postgres` — Expected: FAIL — `relation "baselines" does not exist`.

- [ ] **Step 3: Modèles**

```python
# backend/src/ea/db/models/baseline.py
"""Baselines: a normalised copy of the graph — see docs/adr/BBBB.

The copy repeats the columns of `elements` and `relationships` under a
`baseline_id`, so a diff is a join on typed columns and a baseline stays
readable if the audit trail is purged. Nothing here references the live graph:
the copy must outlive what it copied.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Double, ForeignKey, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class BaselineRecord(Base):
    __tablename__ = "baselines"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by_sub: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BaselineElementRecord(Base):
    __tablename__ = "baseline_elements"

    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_type: Mapped[str] = mapped_column(Text, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    aspect: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    documentation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BaselineRelationshipRecord(Base):
    __tablename__ = "baseline_relationships"

    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    access_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BaselineBoxRecord(Base):
    """A box of a diagram at capture time. No foreign key to `diagrams`: the diagram may be gone."""

    __tablename__ = "baseline_boxes"

    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="CASCADE"), primary_key=True
    )
    diagram_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    x: Mapped[float] = mapped_column(Double, nullable=False)
    y: Mapped[float] = mapped_column(Double, nullable=False)
    width: Mapped[float] = mapped_column(Double, nullable=False)
    height: Mapped[float] = mapped_column(Double, nullable=False)
```

Dans `db/models/__init__.py`, importer les quatre classes et les ajouter à `__all__` (ordre alphabétique):

```python
from ea.db.models.baseline import (
    BaselineBoxRecord,
    BaselineElementRecord,
    BaselineRecord,
    BaselineRelationshipRecord,
)

__all__ = [
    "AuditEventRecord",
    "Base",
    "BaselineBoxRecord",
    "BaselineElementRecord",
    "BaselineRecord",
    "BaselineRelationshipRecord",
    # … les noms existants, inchangés
]
```

- [ ] **Step 4: Migration**

```python
# backend/migrations/versions/0010_baselines.py
"""les baselines : une copie normalisée du graphe

Revision ID: 0010
Revises: 0009
Date: 2026-10-10

Une baseline recopie les colonnes de `elements`, `relationships` et des boîtes
de diagramme sous un `baseline_id`. Aucune clé vers le graphe vivant : la copie
doit survivre à ce qu'elle a copié. Les documents ne sont pas capturés
(docs/adr/BBBB, risque R5).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def _baseline_id(table: str) -> sa.Column[object]:
    return sa.Column(
        "baseline_id",
        sa.Uuid(),
        sa.ForeignKey(
            "baselines.id",
            name=op.f(f"fk_{table}_baseline_id_baselines"),
            ondelete="CASCADE",
        ),
        nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "baselines",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("created_by_sub", sa.Text(), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_baselines")),
        sa.UniqueConstraint("name", name=op.f("uq_baselines_name")),
    )
    op.create_table(
        "baseline_elements",
        _baseline_id("baseline_elements"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("element_type", sa.Text(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("aspect", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("documentation", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("baseline_id", "id", name=op.f("pk_baseline_elements")),
    )
    op.create_table(
        "baseline_relationships",
        _baseline_id("baseline_relationships"),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("relationship_type", sa.Text(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), server_default="", nullable=False),
        sa.Column("access_type", sa.Text(), nullable=True),
        sa.Column("directed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("baseline_id", "id", name=op.f("pk_baseline_relationships")),
    )
    op.create_table(
        "baseline_boxes",
        _baseline_id("baseline_boxes"),
        sa.Column("diagram_id", sa.Uuid(), nullable=False),
        sa.Column("element_id", sa.Uuid(), nullable=False),
        sa.Column("x", sa.Double(), nullable=False),
        sa.Column("y", sa.Double(), nullable=False),
        sa.Column("width", sa.Double(), nullable=False),
        sa.Column("height", sa.Double(), nullable=False),
        sa.PrimaryKeyConstraint(
            "baseline_id", "diagram_id", "element_id", name=op.f("pk_baseline_boxes")
        ),
    )


def downgrade() -> None:
    op.drop_table("baseline_boxes")
    op.drop_table("baseline_relationships")
    op.drop_table("baseline_elements")
    op.drop_table("baselines")
```

- [ ] **Step 5: Relancer**

Run: `make test-postgres` — Expected: PASS (incluant `test_postgres.py` « chaque table mappée existe » et `test_migrations.py`). Run: `cd backend && uv run pytest tests/unit/test_migrations.py -x` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/db/models backend/migrations/versions/0010_baselines.py backend/tests/integration/test_baseline_schema.py
git commit -m "feat(baselines): normalised baseline tables (migration 0010)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 16: Dépôt des baselines — capture, lecture, suppression, instantanés

**Files:**
- Create: `backend/src/ea/repositories/baseline_store.py`, `backend/tests/integration/test_baseline_store.py`
- Modify: `backend/src/ea/domain/ports.py` (ajouter `BaselineRepository` après `AuditRepository`)

**Interfaces:**
- Consumes: Tasks 13, 15, `write_events`, `Attachments`.
- Produces: port et implémentation

```python
class BaselineRepository(Protocol):
    async def list_all(self) -> tuple[Baseline, ...]: ...
    async def get(self, baseline_id: UUID) -> Baseline | None: ...
    async def create(self, baseline: Baseline, *, audit: Sequence[AuditEvent] = ()) -> Baseline: ...
    async def delete(self, baseline_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool: ...
    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot: ...
    async def current_snapshot(self) -> ModelSnapshot: ...
    async def diagram_ids(self) -> frozenset[UUID]: ...
    async def attachments_of_many(self, element_ids: Sequence[UUID]) -> Attachments: ...
    async def apply_restore(self, plan: RestorePlan, *, now: datetime, audit: Sequence[AuditEvent] = ()) -> None: ...
```
(`apply_restore` est déclaré ici et implémenté Task 17.)

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

```python
# backend/tests/integration/test_baseline_store.py
"""Capturing the graph into a baseline, and reading it back."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.baselines import Baseline, diff_snapshots
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.errors import DuplicateBaselineError
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.repositories.diagram_store import PostgresDiagramRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_baseline(name: str = "v1") -> Baseline:
    return Baseline.create(name=name, description="", created_by_sub="s", created_by="n", now=NOW)


async def seeded(engine: AsyncEngine):  # type: ignore[no-untyped-def]
    sessions = create_session_factory(engine)
    graph = PostgresArchitectureRepository(sessions)
    left = await graph.add_element(
        Element.create(element_type=E.NODE, name="a", now=NOW, properties={"owner": "ops"})
    )
    right = await graph.add_element(Element.create(element_type=E.NODE, name="b", now=NOW))
    link = await graph.add_relationship(Relationship.between(R.ASSOCIATION, left, right, now=NOW))
    diagrams = PostgresDiagramRepository(sessions)
    diagram = await diagrams.add(Diagram.create(name="Vue", now=NOW))
    await diagrams.replace_layout(diagram.id, [DiagramNode(left.id, 5, 6)], now=NOW)
    return graph, diagrams, PostgresBaselineRepository(sessions), left, right, link, diagram


async def test_a_capture_copies_elements_links_and_boxes_with_their_counts(
    engine_at_head: AsyncEngine,
) -> None:
    graph, _, baselines, left, _, link, diagram = await seeded(engine_at_head)

    baseline = await baselines.create(a_baseline())

    assert (baseline.element_count, baseline.relationship_count, baseline.box_count) == (2, 1, 1)
    snapshot = await baselines.snapshot_of(baseline.id)
    assert {e.id for e in snapshot.elements} >= {left.id}
    assert [r.id for r in snapshot.relationships] == [link.id]
    assert [(b.diagram_id, b.x) for b in snapshot.boxes] == [(diagram.id, 5.0)]
    assert (await baselines.get(baseline.id)) == baseline
    assert [b.name for b in await baselines.list_all()] == ["v1"]
    assert not diff_snapshots(snapshot, await baselines.current_snapshot()).elements_modified


async def test_the_copy_is_frozen_while_the_graph_moves_on(engine_at_head: AsyncEngine) -> None:
    graph, _, baselines, left, _, _, _ = await seeded(engine_at_head)
    baseline = await baselines.create(a_baseline())

    await graph.save_element(left.rename("a2", now=NOW))
    await graph.delete_element(left.id)

    diff = diff_snapshots(await baselines.snapshot_of(baseline.id), await baselines.current_snapshot())
    assert [e.label for e in diff.elements_removed] == ["a"]
    assert diff.relationships_removed  # the link went with the element


async def test_a_name_can_be_used_once_and_a_delete_removes_the_copy(
    engine_at_head: AsyncEngine,
) -> None:
    _, _, baselines, *_ = await seeded(engine_at_head)
    first = await baselines.create(a_baseline())

    with pytest.raises(DuplicateBaselineError):
        await baselines.create(a_baseline())
    assert await baselines.delete(first.id)
    assert not await baselines.delete(first.id)
    assert await baselines.get(first.id) is None
    assert (await baselines.snapshot_of(first.id)).elements == ()


async def test_attachments_of_many_counts_documents_and_boxes(engine_at_head: AsyncEngine) -> None:
    _, _, baselines, left, right, *_ = await seeded(engine_at_head)

    attached = await baselines.attachments_of_many([left.id, right.id, uuid4()])

    assert len(attached.diagram_ids) == 1 and attached.document_ids == ()
    assert (await baselines.attachments_of_many([])).diagram_ids == ()
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `make test-postgres` — Expected: FAIL — `ModuleNotFoundError: No module named 'ea.repositories.baseline_store'`.

- [ ] **Step 3: Ajouter le port** (`domain/ports.py`; imports: `from ea.domain.baselines import Baseline, ModelSnapshot, RestorePlan` — `baselines` n'importe pas `ports`, pas de cycle)

```python
class BaselineRepository(Protocol):
    """Persistence for baselines — docs/adr/BBBB."""

    async def list_all(self) -> tuple[Baseline, ...]:
        """Every baseline by name, each with the counts of what it holds."""
        ...

    async def get(self, baseline_id: UUID) -> Baseline | None: ...

    async def create(self, baseline: Baseline, *, audit: Sequence[AuditEvent] = ()) -> Baseline:
        """Copy the whole graph under this baseline, in one transaction.

        Raises `DuplicateBaselineError` for a taken name; returns the baseline with its counts.
        """
        ...

    async def delete(self, baseline_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool: ...

    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot: ...

    async def current_snapshot(self) -> ModelSnapshot:
        """The live graph and the boxes of every diagram, as values."""
        ...

    async def diagram_ids(self) -> frozenset[UUID]: ...

    async def attachments_of_many(self, element_ids: Sequence[UUID]) -> Attachments:
        """The documents, and one entry per box, that deleting these elements would take."""
        ...

    async def apply_restore(
        self, plan: RestorePlan, *, now: datetime, audit: Sequence[AuditEvent] = ()
    ) -> None:
        """Apply a plan in one SERIALIZABLE transaction, or change nothing."""
        ...
```

- [ ] **Step 4: Implémenter** (`apply_restore` lève `NotImplementedError` ici; Task 17 le remplace)

```python
# backend/src/ea/repositories/baseline_store.py
"""The PostgreSQL implementation of `BaselineRepository` — see docs/adr/BBBB.

A capture is three `INSERT … SELECT` in one transaction: the copy never passes
through Python, so a large graph costs three statements, and a baseline is
either complete or absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Uuid, delete, func, literal, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import insert

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.db.models.baseline import (
    BaselineBoxRecord,
    BaselineElementRecord,
    BaselineRecord,
    BaselineRelationshipRecord,
)
from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.db.models.document import ElementDocument
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.baselines import Baseline, BoxSnapshot, ModelSnapshot
from ea.domain.errors import DuplicateBaselineError
from ea.domain.model import Element, Relationship
from ea.domain.ports import Attachments
from ea.repositories.architecture_store import element_from_row, relationship_from_row
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditEvent
    from ea.domain.baselines import RestorePlan

_ELEMENT_COLUMNS = (
    "id",
    "element_type",
    "layer",
    "aspect",
    "name",
    "description",
    "documentation",
    "properties",
    "created_at",
    "updated_at",
)
_RELATIONSHIP_COLUMNS = (
    "id",
    "relationship_type",
    "source_id",
    "target_id",
    "source_type",
    "target_type",
    "name",
    "access_type",
    "directed",
    "properties",
    "created_at",
)
_BOX_COLUMNS = ("diagram_id", "element_id", "x", "y", "width", "height")


def _counted() -> Any:
    """Baselines with the counts of their copy, computed by the server rather than stored."""

    def count(model: Any) -> Any:
        return (
            select(func.count()).where(model.baseline_id == BaselineRecord.id).scalar_subquery()
        )

    return select(
        BaselineRecord,
        count(BaselineElementRecord),
        count(BaselineRelationshipRecord),
        count(BaselineBoxRecord),
    )


def _baseline(row: BaselineRecord, elements: int, relationships: int, boxes: int) -> Baseline:
    return Baseline(
        id=row.id,
        name=row.name,
        description=row.description,
        created_by_sub=row.created_by_sub,
        created_by=row.created_by,
        created_at=row.created_at,
        element_count=elements,
        relationship_count=relationships,
        box_count=boxes,
    )


def _copied_element(row: BaselineElementRecord) -> Element:
    return Element(
        id=row.id,
        element_type=ElementType(row.element_type),
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        description=row.description,
        documentation=row.documentation,
        properties=dict(row.properties),
    )


def _copied_relationship(row: BaselineRelationshipRecord) -> Relationship:
    return Relationship(
        id=row.id,
        relationship_type=RelationshipType(row.relationship_type),
        source_id=row.source_id,
        target_id=row.target_id,
        source_type=ElementType(row.source_type),
        target_type=ElementType(row.target_type),
        created_at=row.created_at,
        name=row.name,
        access_type=AccessType(row.access_type) if row.access_type else None,
        directed=row.directed,
        properties=dict(row.properties),
    )


class PostgresBaselineRepository:
    """Baselines, stored beside the graph they copy."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> tuple[Baseline, ...]:
        statement = _counted().order_by(BaselineRecord.name, BaselineRecord.id)
        async with self._sessions() as session:
            return tuple(_baseline(*row) for row in await session.execute(statement))

    async def get(self, baseline_id: UUID) -> Baseline | None:
        statement = _counted().where(BaselineRecord.id == baseline_id)
        async with self._sessions() as session:
            found = (await session.execute(statement)).first()
            return _baseline(*found) if found is not None else None

    async def create(self, baseline: Baseline, *, audit: Sequence[AuditEvent] = ()) -> Baseline:
        id_column = literal(baseline.id, Uuid())
        async with self._sessions.begin() as session:
            session.add(
                BaselineRecord(
                    id=baseline.id,
                    name=baseline.name,
                    description=baseline.description,
                    created_by_sub=baseline.created_by_sub,
                    created_by=baseline.created_by,
                    created_at=baseline.created_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                msg = f"a baseline is already named {baseline.name!r}"
                raise DuplicateBaselineError(msg) from error
            for target, source, columns in (
                (BaselineElementRecord, ElementRecord, _ELEMENT_COLUMNS),
                (BaselineRelationshipRecord, RelationshipRecord, _RELATIONSHIP_COLUMNS),
                (BaselineBoxRecord, DiagramNodeRecord, _BOX_COLUMNS),
            ):
                await session.execute(
                    insert(target).from_select(
                        ["baseline_id", *columns],
                        select(id_column, *(getattr(source, name) for name in columns)),
                    )
                )
            counts = tuple(
                [
                    (
                        await session.execute(
                            select(func.count()).where(model.baseline_id == baseline.id)
                        )
                    ).scalar_one()
                    for model in (
                        BaselineElementRecord,
                        BaselineRelationshipRecord,
                        BaselineBoxRecord,
                    )
                ]
            )
            write_events(session, audit)
        return baseline.with_counts(*counts)

    async def delete(self, baseline_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(BaselineRecord).where(BaselineRecord.id == baseline_id)
            )
            if not cast("CursorResult[Any]", deleted).rowcount:
                return False
            write_events(session, audit)
        return True

    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot:
        async with self._sessions() as session:
            elements = await session.scalars(
                select(BaselineElementRecord).where(BaselineElementRecord.baseline_id == baseline_id)
            )
            links = await session.scalars(
                select(BaselineRelationshipRecord).where(
                    BaselineRelationshipRecord.baseline_id == baseline_id
                )
            )
            boxes = await session.scalars(
                select(BaselineBoxRecord).where(BaselineBoxRecord.baseline_id == baseline_id)
            )
            return ModelSnapshot(
                elements=tuple(_copied_element(row) for row in elements),
                relationships=tuple(_copied_relationship(row) for row in links),
                boxes=tuple(
                    BoxSnapshot(b.diagram_id, b.element_id, b.x, b.y, b.width, b.height)
                    for b in boxes
                ),
            )

    async def current_snapshot(self) -> ModelSnapshot:
        async with self._sessions() as session:
            elements = await session.scalars(select(ElementRecord))
            links = await session.scalars(select(RelationshipRecord))
            boxes = await session.scalars(select(DiagramNodeRecord))
            return ModelSnapshot(
                elements=tuple(element_from_row(row) for row in elements),
                relationships=tuple(relationship_from_row(row) for row in links),
                boxes=tuple(
                    BoxSnapshot(b.diagram_id, b.element_id, b.x, b.y, b.width, b.height)
                    for b in boxes
                ),
            )

    async def diagram_ids(self) -> frozenset[UUID]:
        async with self._sessions() as session:
            return frozenset(await session.scalars(select(DiagramRecord.id)))

    async def attachments_of_many(self, element_ids: Sequence[UUID]) -> Attachments:
        if not element_ids:
            return Attachments()
        ids = list(element_ids)
        async with self._sessions() as session:
            documents = await session.scalars(
                select(ElementDocument.id).where(ElementDocument.element_id.in_(ids))
            )
            boxes = await session.scalars(
                select(DiagramNodeRecord.diagram_id).where(DiagramNodeRecord.element_id.in_(ids))
            )
            return Attachments(tuple(documents), tuple(boxes))

    async def apply_restore(
        self, plan: RestorePlan, *, now: datetime, audit: Sequence[AuditEvent] = ()
    ) -> None:
        raise NotImplementedError  # Task 17
```

- [ ] **Step 5: Relancer**

Run: `make test-postgres` — Expected: PASS. Run: `cd backend && uv run mypy src migrations` — Expected: `Success`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain/ports.py backend/src/ea/repositories/baseline_store.py backend/tests/integration/test_baseline_store.py
git commit -m "feat(baselines): capture, read and delete baselines with INSERT ... SELECT" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 17: `apply_restore` — une transaction SERIALIZABLE, tout ou rien

**Files:**
- Modify: `backend/src/ea/repositories/baseline_store.py` (remplacer `apply_restore`; imports)
- Test: `backend/tests/integration/test_baseline_restore.py`

**Interfaces:**
- Consumes: `RestorePlan`, `rejected`, `constraint_of`, `element_row`, `relationship_row` (architecture_store), `RestoreConflictError`.
- Produces: `PostgresBaselineRepository.apply_restore(plan, *, now, audit=())` — supprime les liens à supprimer, puis les éléments à supprimer (cascade FK), met à jour, crée les éléments, crée les liens, restaure les boîtes (upsert), écrit `audit`, le tout `SERIALIZABLE`; un conflit d'unicité ou une cible disparue lève `RestoreConflictError` nommant le premier élément fautif et **annule tout**.
- **Point d'accroche sous-projet 3**: cette méthode ouvre sa propre `begin()` comme les 12 autres; une interface de session partagée devra aussi l'atteindre.

- [ ] **Step 1: Écrire le test d'intégration qui échoue**

```python
# backend/tests/integration/test_baseline_restore.py
"""Restoring a baseline against a real PostgreSQL: all of it, or none of it."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, AuditChannel, AuditEvent, AuditFilter
from ea.domain.baselines import Baseline, plan_restore
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.documents import Document
from ea.domain.errors import RestoreConflictError
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.document_store import PostgresDocumentRepository
from uuid import uuid4

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def an_event(action: AuditAction = AuditAction.BASELINE_RESTORED) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=LATER,
        actor_sub="s",
        actor_name="n",
        action=action,
        entity_kind=action.kind,
        entity_id=None,
        before=None,
        after={},
        request_id="req",
        channel=AuditChannel.HTTP,
        correlation="restore:x",
    )


async def test_a_restoration_recreates_reverts_deletes_and_leaves_one_trace(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    baselines = PostgresBaselineRepository(sessions)
    kept = await graph.add_element(Element.create(element_type=E.NODE, name="kept", now=NOW))
    edited = await graph.add_element(Element.create(element_type=E.NODE, name="billing", now=NOW))
    gone = await graph.add_element(Element.create(element_type=E.NODE, name="gone", now=NOW))
    link = await graph.add_relationship(Relationship.between(R.ASSOCIATION, kept, gone, now=NOW))
    baseline = await baselines.create(
        Baseline.create(name="v1", description="", created_by_sub="s", created_by="n", now=NOW)
    )

    await graph.delete_element(gone.id)
    await graph.save_element(edited.rename("invoicing", now=LATER))
    fresh = await graph.add_element(Element.create(element_type=E.NODE, name="fresh", now=LATER))
    await graph.add_relationship(Relationship.between(R.ASSOCIATION, kept, fresh, now=LATER))

    plan = plan_restore(
        await baselines.snapshot_of(baseline.id),
        await baselines.current_snapshot(),
        keep_new=False,
        diagrams_present=await baselines.diagram_ids(),
    )
    await baselines.apply_restore(plan, now=LATER, audit=(an_event(),))

    assert (await graph.get_element(gone.id)) is not None  # back, under its original id
    assert (await graph.get_element(edited.id)).name == "billing"  # type: ignore[union-attr]
    assert await graph.get_element(fresh.id) is None
    assert await graph.get_relationship(link.id) is not None
    trail = await PostgresAuditRepository(sessions).page(AuditFilter())
    assert [e.action for e in trail.events] == [AuditAction.BASELINE_RESTORED]


async def test_a_conflict_in_the_middle_rolls_everything_back_and_names_the_culprit(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    baselines = PostgresBaselineRepository(sessions)
    keep = await graph.add_element(Element.create(element_type=E.NODE, name="keep", now=NOW))
    victim = await graph.add_element(Element.create(element_type=E.NODE, name="victim", now=NOW))
    baseline = await baselines.create(
        Baseline.create(name="v1", description="", created_by_sub="s", created_by="n", now=NOW)
    )
    await graph.delete_element(victim.id)
    # Someone took the name while the baseline waited: restoring `victim` must fail whole.
    squatter = await graph.add_element(Element.create(element_type=E.NODE, name="victim", now=LATER))
    await graph.save_element(keep.rename("keep-renamed", now=LATER))

    plan = plan_restore(
        await baselines.snapshot_of(baseline.id),
        await baselines.current_snapshot(),
        keep_new=True,
        diagrams_present=await baselines.diagram_ids(),
    )
    with pytest.raises(RestoreConflictError, match="victim"):
        await baselines.apply_restore(plan, now=LATER, audit=(an_event(),))

    assert (await graph.get_element(keep.id)).name == "keep-renamed"  # type: ignore[union-attr]  # the update rolled back
    assert await graph.get_element(victim.id) is None
    assert await graph.get_element(squatter.id) is not None
    assert (await PostgresAuditRepository(sessions).page(AuditFilter())).events == ()


async def test_deleting_an_element_by_restoration_takes_its_documents_and_boxes_like_a_delete(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    baselines = PostgresBaselineRepository(sessions)
    base = await baselines.create(
        Baseline.create(name="empty", description="", created_by_sub="s", created_by="n", now=NOW)
    )
    doomed = await graph.add_element(Element.create(element_type=E.NODE, name="doomed", now=NOW))
    await PostgresDocumentRepository(sessions).add(
        Document.create(element_id=doomed.id, filename="a.md", content="# A\n", now=NOW)
    )
    diagrams = PostgresDiagramRepository(sessions)
    diagram = await diagrams.add(Diagram.create(name="Vue", now=NOW))
    await diagrams.replace_layout(diagram.id, [DiagramNode(doomed.id, 0, 0)], now=NOW)

    plan = plan_restore(
        await baselines.snapshot_of(base.id), await baselines.current_snapshot(), keep_new=False
    )
    plan = replace(plan, documents_lost=1)
    await baselines.apply_restore(plan, now=LATER)

    assert (await graph.attachments_of(doomed.id)).document_ids == ()
    assert await graph.get_element(doomed.id) is None
    assert (await diagrams.nodes_of(diagram.id)) == ()
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `make test-postgres` — Expected: FAIL — `NotImplementedError`.

- [ ] **Step 3: Implémenter** (remplace `apply_restore`; ajouter aux imports: `from sqlalchemy.dialects.postgresql import insert as upsert`, `from sqlalchemy.exc import DBAPIError`, `from ea.domain.errors import DuplicateBaselineError, RestoreConflictError`, `from ea.repositories.architecture_store import constraint_of, element_from_row, element_row, rejected, relationship_from_row, relationship_row`)

```python
def _rows_affected(result: object) -> int:
    return cast("CursorResult[Any]", result).rowcount


def _is_serialization_failure(error: DBAPIError) -> bool:
    cause = getattr(error.orig, "__cause__", None)
    return getattr(cause, "sqlstate", None) == "40001"


async def _flush_restoring(session: AsyncSession, element: Element) -> None:
    """Flush, and turn a uniqueness refusal into the sentence that names the element."""
    try:
        await session.flush()
    except IntegrityError as error:
        refusal = rejected(element, constraint_of(error))
        if refusal is None:
            raise
        msg = f"restoring {element.name!r} conflicts with the current catalogue: {refusal}"
        raise RestoreConflictError(msg) from error
```

et la méthode:

```python
    async def apply_restore(
        self, plan: RestorePlan, *, now: datetime, audit: Sequence[AuditEvent] = ()
    ) -> None:
        """One SERIALIZABLE transaction: the plan, its boxes and its events, or nothing.

        The plan was computed before this transaction opened, so every target is
        checked again here: an element already gone, or a serialization failure,
        is a `RestoreConflictError` — the caller reads the plan again and retries.
        """
        try:
            async with self._sessions.begin() as session:
                await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
                for link in plan.relationships_to_delete:
                    await session.execute(
                        delete(RelationshipRecord).where(RelationshipRecord.id == link.id)
                    )
                for element in plan.elements_to_delete:
                    deleted = await session.execute(
                        delete(ElementRecord).where(ElementRecord.id == element.id)
                    )
                    if not _rows_affected(deleted):
                        msg = f"{element.name!r} is already gone: read the plan again"
                        raise RestoreConflictError(msg)
                for update in plan.elements_to_update:
                    row = await session.get(ElementRecord, update.restored.id)
                    if row is None:
                        msg = f"{update.current.name!r} is already gone: read the plan again"
                        raise RestoreConflictError(msg)
                    row.element_type = update.restored.element_type.value
                    row.name = update.restored.name
                    row.description = update.restored.description
                    row.documentation = update.restored.documentation
                    row.properties = dict(update.restored.properties)
                    row.updated_at = now
                    await _flush_restoring(session, update.restored)
                for element in plan.elements_to_create:
                    session.add(ElementRecord(**element_row(element)))
                    await _flush_restoring(session, element)
                for link in plan.relationships_to_create:
                    session.add(RelationshipRecord(**relationship_row(link)))
                await session.flush()
                for box in plan.boxes_to_restore:
                    statement = upsert(DiagramNodeRecord).values(
                        diagram_id=box.diagram_id,
                        element_id=box.element_id,
                        x=box.x,
                        y=box.y,
                        width=box.width,
                        height=box.height,
                    )
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=[
                                DiagramNodeRecord.diagram_id,
                                DiagramNodeRecord.element_id,
                            ],
                            set_={
                                "x": statement.excluded.x,
                                "y": statement.excluded.y,
                                "width": statement.excluded.width,
                                "height": statement.excluded.height,
                            },
                        )
                    )
                write_events(session, audit)
        except DBAPIError as error:
            if _is_serialization_failure(error):
                msg = "the catalogue changed during the restoration: read the plan again"
                raise RestoreConflictError(msg) from error
            raise
```

(`IntegrityError` est une `DBAPIError`: `_flush_restoring` la traduit avant d'arriver ici; une `IntegrityError` non traduite est re-levée telle quelle par `raise`.)

- [ ] **Step 4: Relancer**

Run: `make test-postgres` — Expected: PASS. Run: `cd backend && uv run mypy src migrations` — Expected: `Success`.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/repositories/baseline_store.py backend/tests/integration/test_baseline_restore.py
git commit -m "feat(baselines): all-or-nothing SERIALIZABLE restoration" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 18: `BaselineService` (+ double mémoire)

**Files:**
- Create: `backend/src/ea/services/baselines.py`, `backend/tests/unit/test_baseline_service.py`
- Modify: `backend/tests/conftest.py` (nouvelle classe `InMemoryBaselines` après `InMemoryDiagrams`; fixtures `baseline_store`, `baseline_service`), `backend/tests/unit/test_service_guards.py` (`WRITES`)

**Interfaces:**
- Consumes: Tasks 13, 14, 16 (port), `AuditTrail`, `acting_as`.
- Produces:

```python
@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    plan: RestorePlan
    applied: bool

class BaselineService:
    def __init__(self, repository: BaselineRepository, *, clock: Clock = _utc_now) -> None
    async def list_baselines(self) -> tuple[Baseline, ...]                       # require_caller
    async def get(self, baseline_id: UUID) -> Baseline                           # require_caller; BaselineNotFoundError
    async def create(self, *, name: str, description: str = "") -> Baseline      # require_editor
    async def delete(self, baseline_id: UUID) -> None                            # require_editor
    async def diff(self, baseline_id: UUID, against: UUID | None = None) -> BaselineDiff   # require_caller; None = current model
    async def restore(self, baseline_id: UUID, *, dry_run: bool = True, confirm: bool = False, keep_new: bool | None = None) -> RestoreOutcome   # require_editor
```
Événements: `baseline.created` (`after=baseline_view`), `baseline.deleted` (`before=baseline_view`), à la restauration `baseline.restored` (`after` = résumé des comptes) puis `element.created|updated|deleted`, `relationship.created|deleted`, tous `correlation=f"restore:{baseline.id}"`.

- [ ] **Step 1: Double mémoire dans `tests/conftest.py`**

Ajouter aux imports: `from dataclasses import replace` (déjà présent), `from ea.domain.baselines import Baseline, BoxSnapshot, ModelSnapshot, RestorePlan`, `from ea.domain.errors import BaselineNotFoundError? (non), DuplicateBaselineError, RestoreConflictError`, `from ea.domain.ports import Attachments`. Après `InMemoryDiagrams`:

```python
class InMemoryBaselines:
    """The baseline tables over the graph and diagram doubles it copies.

    Capture copies the dicts; `apply_restore` mutates copies and swaps them in only
    if every step held, which is the all-or-nothing the real transaction gives —
    including the address uniqueness the graph double reproduces.
    """

    def __init__(
        self,
        graph: InMemoryRepository,
        diagrams: InMemoryDiagrams,
        audit_log: InMemoryAuditLog | None = None,
        documents: InMemoryDocuments | None = None,
    ) -> None:
        self.graph = graph
        self.diagrams = diagrams
        self.documents = documents
        self.audit_log = audit_log if audit_log is not None else graph.audit_log
        self.baselines: dict[UUID, Baseline] = {}
        self.copies: dict[UUID, ModelSnapshot] = {}

    def _counted(self, baseline: Baseline) -> Baseline:
        copy = self.copies.get(baseline.id, ModelSnapshot((), ()))
        return baseline.with_counts(len(copy.elements), len(copy.relationships), len(copy.boxes))

    async def list_all(self) -> tuple[Baseline, ...]:
        return tuple(self._counted(b) for b in sorted(self.baselines.values(), key=lambda b: b.name))

    async def get(self, baseline_id: UUID) -> Baseline | None:
        stored = self.baselines.get(baseline_id)
        return self._counted(stored) if stored is not None else None

    async def create(self, baseline: Baseline, *, audit: Sequence[AuditEvent] = ()) -> Baseline:
        if any(b.name == baseline.name for b in self.baselines.values()):
            raise DuplicateBaselineError(f"a baseline is already named {baseline.name!r}")
        self.baselines[baseline.id] = baseline
        self.copies[baseline.id] = await self.current_snapshot()
        self.audit_log.append(audit)
        return self._counted(baseline)

    async def delete(self, baseline_id: UUID, *, audit: Sequence[AuditEvent] = ()) -> bool:
        self.copies.pop(baseline_id, None)
        if self.baselines.pop(baseline_id, None) is None:
            return False
        self.audit_log.append(audit)
        return True

    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot:
        return self.copies.get(baseline_id, ModelSnapshot((), ()))

    async def current_snapshot(self) -> ModelSnapshot:
        return ModelSnapshot(
            elements=tuple(self.graph.elements.values()),
            relationships=tuple(self.graph.relationships.values()),
            boxes=tuple(
                BoxSnapshot(diagram_id, n.element_id, n.x, n.y, n.width, n.height)
                for diagram_id, nodes in self.diagrams.nodes.items()
                for n in nodes
            ),
        )

    async def diagram_ids(self) -> frozenset[UUID]:
        return frozenset(self.diagrams.diagrams)

    async def attachments_of_many(self, element_ids: Sequence[UUID]) -> Attachments:
        wanted = set(element_ids)
        documents = tuple(
            d.id
            for d in (self.documents.documents.values() if self.documents else ())
            if d.element_id in wanted
        )
        boxes = tuple(
            diagram_id
            for diagram_id, nodes in self.diagrams.nodes.items()
            for n in nodes
            if n.element_id in wanted
        )
        return Attachments(documents, boxes)

    async def apply_restore(
        self, plan: RestorePlan, *, now: datetime, audit: Sequence[AuditEvent] = ()
    ) -> None:
        elements, links = dict(self.graph.elements), dict(self.graph.relationships)
        nodes = dict(self.diagrams.nodes)
        try:
            for link in plan.relationships_to_delete:
                self.graph.relationships.pop(link.id, None)
            for element in plan.elements_to_delete:
                self.graph.elements.pop(element.id, None)
                for link_id in [
                    i
                    for i, r in self.graph.relationships.items()
                    if element.id in (r.source_id, r.target_id)
                ]:
                    del self.graph.relationships[link_id]
            for update in plan.elements_to_update:
                restored = replace(update.restored, updated_at=now)
                self.graph._refuse_a_taken_address(restored)
                self.graph.elements[restored.id] = restored
            for element in plan.elements_to_create:
                if any(
                    e.name == element.name and e.element_type == element.element_type
                    for e in self.graph.elements.values()
                ):
                    raise RestoreConflictError(f"restoring {element.name!r} conflicts: name taken")
                try:
                    self.graph._refuse_a_taken_address(element)
                except AddressAlreadyAssignedError as refusal:
                    raise RestoreConflictError(
                        f"restoring {element.name!r} conflicts: {refusal}"
                    ) from refusal
                self.graph.elements[element.id] = element
            for link in plan.relationships_to_create:
                self.graph.relationships[link.id] = link
            for box in plan.boxes_to_restore:
                current = [n for n in self.diagrams.nodes.get(box.diagram_id, ()) if n.element_id != box.element_id]
                current.append(DiagramNode(box.element_id, box.x, box.y, box.width, box.height))
                self.diagrams.nodes[box.diagram_id] = tuple(current)
        except Exception:
            self.graph.elements, self.graph.relationships = elements, links
            self.diagrams.nodes = nodes
            raise
        self.audit_log.append(audit)
```

Fixtures (près de `diagram_service`):

```python
@pytest.fixture
def baseline_store(
    repository: InMemoryRepository,
    diagrams: InMemoryDiagrams,
    documents: InMemoryDocuments,
    audit_log: InMemoryAuditLog,
) -> InMemoryBaselines:
    return InMemoryBaselines(repository, diagrams, audit_log, documents)


@pytest.fixture
def baseline_service(baseline_store: InMemoryBaselines) -> BaselineService:
    return BaselineService(baseline_store, clock=lambda: FIXED_NOW)
```

(`from ea.services.baselines import BaselineService` aux imports; `InMemoryDiagrams.nodes` est un `dict[UUID, tuple[DiagramNode, ...]]` — vérifier le nom de l'attribut dans la classe avant de coller.)

`test_service_guards.py`: importer `BaselineService`, ajouter `BaselineService: {"create", "delete", "restore"},` à `WRITES`.

- [ ] **Step 2: Écrire le test qui échoue**

```python
# backend/tests/unit/test_baseline_service.py
"""The baseline use cases against doubled stores."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.core.logging import request_id
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction
from ea.domain.errors import (
    BaselineNotFoundError,
    DuplicateBaselineError,
    NotAuthorisedError,
    RestoreConflictError,
)
from ea.services.architecture import ArchitectureService
from ea.services.baselines import BaselineService
from ea.services.caller import acting_as
from tests.conftest import InMemoryAuditLog, a_reader

pytestmark = pytest.mark.asyncio


class TestCapturingAndComparing:
    async def test_a_capture_is_listed_with_its_counts_and_audited(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        await service.create_element(element_type=E.NODE, name="a")
        audit_log.events.clear()

        baseline = await baseline_service.create(name="v1", description="before the move")

        assert baseline.element_count == 1
        assert [b.name for b in await baseline_service.list_baselines()] == ["v1"]
        (event,) = audit_log.events
        assert event.action is AuditAction.BASELINE_CREATED
        assert event.entity_id == baseline.id

    async def test_a_taken_name_a_missing_baseline_and_a_reader_are_refused_without_an_event(
        self,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        await baseline_service.create(name="v1")
        audit_log.events.clear()

        with pytest.raises(DuplicateBaselineError):
            await baseline_service.create(name="v1")
        with pytest.raises(BaselineNotFoundError):
            await baseline_service.get(uuid4())
        with pytest.raises(BaselineNotFoundError):
            await baseline_service.delete(uuid4())
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await baseline_service.create(name="nope")

        assert audit_log.events == []

    async def test_the_diff_against_the_current_model_shows_what_moved(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        kept = await service.create_element(element_type=E.NODE, name="kept")
        baseline = await baseline_service.create(name="v1")
        await service.update_element(kept.id, name="renamed")
        await service.create_element(element_type=E.NODE, name="new")

        diff = await baseline_service.diff(baseline.id)

        assert [e.label for e in diff.elements_added] == ["new"]
        assert [m.label for m in diff.elements_modified] == ["renamed"]

    async def test_two_baselines_compare_with_each_other(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        first = await baseline_service.create(name="v1")
        await service.create_element(element_type=E.NODE, name="later")
        second = await baseline_service.create(name="v2")

        diff = await baseline_service.diff(first.id, against=second.id)

        assert [e.label for e in diff.elements_added] == ["later"]


class TestRestoring:
    async def test_a_dry_run_reports_and_writes_nothing(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        gone = await service.create_element(element_type=E.NODE, name="gone")
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(gone.id)
        audit_log.events.clear()

        outcome = await baseline_service.restore(baseline.id)

        assert not outcome.applied
        assert [e.name for e in outcome.plan.elements_to_create] == ["gone"]
        assert audit_log.events == []
        with pytest.raises(Exception, match="not found|no element"):
            await service.get_element(gone.id)

    async def test_applying_without_confirm_is_refused_and_writes_nothing(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        gone = await service.create_element(element_type=E.NODE, name="gone")
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(gone.id)
        audit_log.events.clear()

        with pytest.raises(ValueError, match="confirm"):
            await baseline_service.restore(baseline.id, dry_run=False)

        assert audit_log.events == []

    async def test_a_restoration_puts_the_catalogue_back_and_audits_it_as_one_story(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        gone = await service.create_element(element_type=E.NODE, name="gone")
        edited = await service.create_element(element_type=E.NODE, name="billing")
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(gone.id)
        await service.update_element(edited.id, name="invoicing")
        fresh = await service.create_element(element_type=E.NODE, name="fresh")
        audit_log.events.clear()

        token = request_id.set("req-restore")
        try:
            outcome = await baseline_service.restore(baseline.id, dry_run=False, confirm=True)
        finally:
            request_id.reset(token)

        assert outcome.applied
        assert (await service.get_element(gone.id)).name == "gone"
        assert (await service.get_element(edited.id)).name == "billing"
        with pytest.raises(Exception, match="no element"):
            await service.get_element(fresh.id)
        assert {e.action for e in audit_log.events} == {
            AuditAction.BASELINE_RESTORED,
            AuditAction.ELEMENT_CREATED,
            AuditAction.ELEMENT_UPDATED,
            AuditAction.ELEMENT_DELETED,
        }
        assert {e.correlation for e in audit_log.events} == {f"restore:{baseline.id}"}
        assert {e.request_id for e in audit_log.events} == {"req-restore"}
        assert audit_log.events[0].action is AuditAction.BASELINE_RESTORED

    async def test_keep_new_keeps_what_was_created_since(
        self, service: ArchitectureService, baseline_service: BaselineService
    ) -> None:
        baseline = await baseline_service.create(name="empty")
        fresh = await service.create_element(element_type=E.NODE, name="fresh")

        await baseline_service.restore(baseline.id, dry_run=False, confirm=True, keep_new=True)

        assert (await service.get_element(fresh.id)).name == "fresh"

    async def test_a_conflict_changes_nothing_and_audits_nothing(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
    ) -> None:
        props = {"ip_address": "10.0.0.1", "vrf": "default"}
        host = await service.create_element(element_type=E.NODE, name="h1", properties=props)
        baseline = await baseline_service.create(name="v1")
        await service.delete_element(host.id)
        squatter = await service.create_element(element_type=E.NODE, name="h2", properties=props)
        audit_log.events.clear()

        with pytest.raises(RestoreConflictError, match="h1"):
            await baseline_service.restore(
                baseline.id, dry_run=False, confirm=True, keep_new=True
            )

        assert audit_log.events == []
        assert (await service.get_element(squatter.id)).name == "h2"

    async def test_a_reader_may_not_even_ask_for_a_plan(
        self, baseline_service: BaselineService
    ) -> None:
        baseline = await baseline_service.create(name="v1")

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await baseline_service.restore(baseline.id)
```

- [ ] **Step 3: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_baseline_service.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.services.baselines'`.

- [ ] **Step 4: Implémenter**

```python
# backend/src/ea/services/baselines.py
"""Use cases over baselines — docs/adr/BBBB.

The rules this layer owns: a baseline is read before it is used (a readable
404), restoring is a two-step gesture (a dry run to read, then `confirm`), and
a restoration is **one** story in the audit trail — a `baseline.restored` event
followed by one event per entity it touched, all under the same request id and
a `restore:<id>` correlation. The plan is decided in the domain
(`plan_restore`); the repository applies it atomically.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.audit import (
    AuditAction,
    AuditEvent,
    element_view,
    relationship_view,
)
from ea.domain.baselines import (
    Baseline,
    BaselineDiff,
    RestorePlan,
    baseline_view,
    diff_snapshots,
    plan_restore,
)
from ea.domain.errors import BaselineNotFoundError, RestoreBlockedError
from ea.services.audit import AuditTrail
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from ea.domain.ports import BaselineRepository

Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class RestoreOutcome:
    plan: RestorePlan
    applied: bool


class BaselineService:
    """The single entry point `api/` and `mcp/` use to capture, compare and restore."""

    def __init__(self, repository: BaselineRepository, *, clock: Clock = _utc_now) -> None:
        self._repository = repository
        self._now = clock
        self._trail = AuditTrail(clock)

    async def list_baselines(self) -> tuple[Baseline, ...]:
        require_caller()
        return await self._repository.list_all()

    async def get(self, baseline_id: UUID) -> Baseline:
        require_caller()
        baseline = await self._repository.get(baseline_id)
        if baseline is None:
            msg = f"no baseline with id {baseline_id}"
            raise BaselineNotFoundError(msg)
        return baseline

    async def create(self, *, name: str, description: str = "") -> Baseline:
        caller = require_editor()
        baseline = Baseline.create(
            name=name,
            description=description,
            created_by_sub=caller.subject,
            created_by=caller.username,
            now=self._now(),
        )
        event = self._trail.event(
            AuditAction.BASELINE_CREATED, entity_id=baseline.id, after=baseline_view(baseline)
        )
        stored = await self._repository.create(baseline, audit=(event,))
        logger.info(
            "baseline captured: %r",
            stored.name,
            extra={"action": "baseline_created", "baseline_id": str(stored.id)},
        )
        return stored

    async def delete(self, baseline_id: UUID) -> None:
        require_editor()
        current = await self.get(baseline_id)
        event = self._trail.event(
            AuditAction.BASELINE_DELETED, entity_id=baseline_id, before=baseline_view(current)
        )
        if not await self._repository.delete(baseline_id, audit=(event,)):
            msg = f"no baseline with id {baseline_id}"
            raise BaselineNotFoundError(msg)

    async def diff(self, baseline_id: UUID, against: UUID | None = None) -> BaselineDiff:
        """The baseline against another one, or against the model as it is now."""
        require_caller()
        await self.get(baseline_id)
        older = await self._repository.snapshot_of(baseline_id)
        if against is None:
            newer = await self._repository.current_snapshot()
        else:
            await self.get(against)
            newer = await self._repository.snapshot_of(against)
        return diff_snapshots(older, newer)

    async def restore(
        self,
        baseline_id: UUID,
        *,
        dry_run: bool = True,
        confirm: bool = False,
        keep_new: bool | None = None,
    ) -> RestoreOutcome:
        """Plan a restoration, and apply it only when told twice (`dry_run=False`, `confirm`).

        `keep_new` defaults to `False` for a global baseline: restoring is
        exact. A plan that touches a locked element is returned by a dry run and
        refused (`RestoreBlockedError`) when applied.
        """
        require_editor()
        if not dry_run and not confirm:
            msg = "restoring changes the catalogue: read the dry run, then pass confirm=true"
            raise ValueError(msg)
        baseline = await self.get(baseline_id)
        snapshot = await self._repository.snapshot_of(baseline_id)
        current = await self._repository.current_snapshot()
        plan = plan_restore(
            snapshot,
            current,
            keep_new=bool(keep_new),
            diagrams_present=await self._repository.diagram_ids(),
        )
        lost = await self._repository.attachments_of_many([e.id for e in plan.elements_to_delete])
        plan = replace(plan, documents_lost=len(lost.document_ids), boxes_lost=len(lost.diagram_ids))
        if dry_run:
            return RestoreOutcome(plan, applied=False)
        if plan.is_blocked:
            names = ", ".join(repr(e.name) for e in plan.blocked)
            msg = f"the restoration would change locked elements: {names}. Unlock them first"
            raise RestoreBlockedError(msg)
        now = self._now()
        await self._repository.apply_restore(plan, now=now, audit=self._events(baseline, plan, now))
        logger.info(
            "baseline %r restored",
            baseline.name,
            extra={"action": "baseline_restored", "baseline_id": str(baseline.id)},
        )
        return RestoreOutcome(plan, applied=True)

    def _events(self, baseline: Baseline, plan: RestorePlan, now: datetime) -> list[AuditEvent]:
        story = f"restore:{baseline.id}"
        events = [
            self._trail.event(
                AuditAction.BASELINE_RESTORED,
                entity_id=baseline.id,
                after={
                    **baseline_view(baseline),
                    "elements_created": len(plan.elements_to_create),
                    "elements_updated": len(plan.elements_to_update),
                    "elements_deleted": len(plan.elements_to_delete),
                    "relationships_created": len(plan.relationships_to_create),
                    "relationships_deleted": len(plan.relationships_to_delete),
                    "relationships_cascaded": plan.cascaded_relationships,
                    "documents_lost": plan.documents_lost,
                    "boxes_lost": plan.boxes_lost,
                    "warnings": list(plan.warnings),
                },
                correlation=story,
            )
        ]
        for element in plan.elements_to_create:
            events.append(
                self._trail.event(
                    AuditAction.ELEMENT_CREATED,
                    entity_id=element.id,
                    after=element_view(element),
                    correlation=story,
                )
            )
        for update in plan.elements_to_update:
            events.append(
                self._trail.event(
                    AuditAction.ELEMENT_UPDATED,
                    entity_id=update.current.id,
                    before=element_view(update.current),
                    after=element_view(replace(update.restored, updated_at=now)),
                    correlation=story,
                )
            )
        for element in plan.elements_to_delete:
            events.append(
                self._trail.event(
                    AuditAction.ELEMENT_DELETED,
                    entity_id=element.id,
                    before=element_view(element),
                    correlation=story,
                )
            )
        for link in plan.relationships_to_delete:
            events.append(
                self._trail.event(
                    AuditAction.RELATIONSHIP_DELETED,
                    entity_id=link.id,
                    before=relationship_view(link),
                    correlation=story,
                )
            )
        for link in plan.relationships_to_create:
            events.append(
                self._trail.event(
                    AuditAction.RELATIONSHIP_CREATED,
                    entity_id=link.id,
                    after=relationship_view(link),
                    correlation=story,
                )
            )
        return events
```

(Supprimer l'import inutilisé `ModelSnapshot` et `UUID` sous `TYPE_CHECKING` si `ruff` le signale; `UUID` est utilisé dans les annotations — garder.)

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_baseline_service.py tests/unit/test_service_guards.py -x` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/services/baselines.py backend/tests/conftest.py backend/tests/unit/test_baseline_service.py backend/tests/unit/test_service_guards.py
git commit -m "feat(baselines): capture, diff and two-step restoration service" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 19: API HTTP des baselines et câblage

**Files:**
- Create: `backend/src/ea/api/baselines.py`, `backend/tests/e2e/test_baseline_api.py`
- Modify: `backend/src/ea/api/schemas.py` (fin de fichier; bornes), `backend/src/ea/api/errors.py` (import + `_STATUS`), `backend/src/ea/api/dependencies.py`, `backend/src/ea/main.py`, `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés), `frontend/src/features/history/auditLabels.ts` (libellés des nouvelles actions et du nouveau type), `frontend/tests/HistorySection.spec.ts` (aucun changement attendu)

**Interfaces:**
- Consumes: `BaselineService`, `RestoreOutcome`, `BaselineDiff`.
- Produces:
  - `BaselineCreate(name: Name, description: Description = "")` (extra=forbid); `BaselineRead(id, name, description, created_by, created_at, element_count, relationship_count, box_count)`; `EntityRefRead(kind, id, label, type)`; `ModifiedRead(kind, id, label, type, changes)`; `BaselineDiffRead(elements_added, elements_removed, elements_modified, relationships_added, relationships_removed, relationships_modified)`; `RestoreRequest(dry_run: bool = True, confirm: bool = False, keep_new: bool | None = None)` (extra=forbid); `RestorePlanRead(elements_to_create, elements_to_update, elements_to_delete, relationships_to_create, relationships_to_delete, relationships_cascaded, boxes_to_restore, documents_lost, boxes_lost: int; warnings: list[str]; blocked: list[EntityRefRead]); RestoreRead(applied: bool, plan: RestorePlanRead)`; borne `Against = Annotated[str, Field(max_length=36)]`
  - routes: `GET/POST /baselines`, `GET/DELETE /baselines/{id}`, `GET /baselines/{id}/diff?against=`, `POST /baselines/{id}/restore`
  - `_STATUS`: `BaselineNotFoundError` 404 `not_found`, `DuplicateBaselineError` 409 `duplicate`, `RestoreBlockedError` 409 `restore_blocked`, `RestoreConflictError` 409 `restore_conflict`
  - `app.state.baseline_service`, `create_app(..., baselines: BaselineRepository | None = None)`, dépendance `Baselines`

- [ ] **Step 1: Écrire le test e2e qui échoue**

```python
# backend/tests/e2e/test_baseline_api.py
"""The baseline endpoints against doubled stores."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import InMemoryAuditLog, InMemoryBaselines, StaticVerifier, a_reader, an_editor

pytestmark = pytest.mark.asyncio

EDITOR, READER = "editor-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService,
    baseline_store: InMemoryBaselines,
    audit_log: InMemoryAuditLog,
    nobody_calling: None,
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier({EDITOR: an_editor(), READER: a_reader()})
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        baselines=baseline_store,
        audit=audit_log,
        verifier=verifier,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def as_(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def element(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": "node", "name": name}, headers=as_(EDITOR)
    )
    return dict(response.json())


async def capture(client: httpx.AsyncClient, name: str = "v1") -> dict[str, Any]:
    response = await client.post("/baselines", json={"name": name}, headers=as_(EDITOR))
    assert response.status_code == 201, response.text
    return dict(response.json())


class TestBaselines:
    async def test_capture_list_read_delete(self, client: httpx.AsyncClient) -> None:
        await element(client, "a")
        baseline = await capture(client)

        listed = (await client.get("/baselines", headers=as_(READER))).json()
        one = (await client.get(f"/baselines/{baseline['id']}", headers=as_(READER))).json()
        deleted = await client.delete(f"/baselines/{baseline['id']}", headers=as_(EDITOR))

        assert baseline["element_count"] == 1
        assert [b["name"] for b in listed] == ["v1"] and one["name"] == "v1"
        assert deleted.status_code == 204

    async def test_a_duplicate_name_is_a_409_a_reader_a_403_a_stranger_a_404(
        self, client: httpx.AsyncClient
    ) -> None:
        await capture(client)

        assert (
            await client.post("/baselines", json={"name": "v1"}, headers=as_(EDITOR))
        ).status_code == 409
        assert (
            await client.post("/baselines", json={"name": "x"}, headers=as_(READER))
        ).status_code == 403
        assert (await client.post("/baselines", json={"name": "x"})).status_code == 401
        missing = await client.get(
            "/baselines/00000000-0000-4000-8000-000000000000", headers=as_(READER)
        )
        assert missing.status_code == 404

    async def test_the_diff_defaults_to_the_current_model(self, client: httpx.AsyncClient) -> None:
        baseline = await capture(client)
        await element(client, "new")

        diff = (await client.get(f"/baselines/{baseline['id']}/diff", headers=as_(READER))).json()
        explicit = (
            await client.get(
                f"/baselines/{baseline['id']}/diff", params={"against": "current"}, headers=as_(READER)
            )
        ).json()
        bad = await client.get(
            f"/baselines/{baseline['id']}/diff", params={"against": "nonsense"}, headers=as_(READER)
        )

        assert [e["label"] for e in diff["elements_added"]] == ["new"]
        assert explicit == diff
        assert bad.status_code == 422


class TestRestoring:
    async def test_a_dry_run_is_the_default_and_writes_nothing(
        self, client: httpx.AsyncClient
    ) -> None:
        gone = await element(client, "gone")
        baseline = await capture(client)
        await client.delete(f"/elements/{gone['id']}", headers=as_(EDITOR))

        response = await client.post(
            f"/baselines/{baseline['id']}/restore", json={}, headers=as_(EDITOR)
        )

        assert response.status_code == 200
        assert response.json()["applied"] is False
        assert response.json()["plan"]["elements_to_create"] == 1
        assert (await client.get(f"/elements/{gone['id']}", headers=as_(READER))).status_code == 404

    async def test_applying_needs_both_dry_run_false_and_confirm(
        self, client: httpx.AsyncClient
    ) -> None:
        gone = await element(client, "gone")
        baseline = await capture(client)
        await client.delete(f"/elements/{gone['id']}", headers=as_(EDITOR))
        url = f"/baselines/{baseline['id']}/restore"

        unconfirmed = await client.post(url, json={"dry_run": False}, headers=as_(EDITOR))
        applied = await client.post(
            url, json={"dry_run": False, "confirm": True}, headers=as_(EDITOR)
        )

        assert unconfirmed.status_code == 422
        assert applied.json()["applied"] is True
        assert (await client.get(f"/elements/{gone['id']}", headers=as_(READER))).status_code == 200

    async def test_a_reader_cannot_restore_and_unknown_fields_are_refused(
        self, client: httpx.AsyncClient
    ) -> None:
        baseline = await capture(client)
        url = f"/baselines/{baseline['id']}/restore"

        assert (await client.post(url, json={}, headers=as_(READER))).status_code == 403
        assert (
            await client.post(url, json={"force": True}, headers=as_(EDITOR))
        ).status_code == 422
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/e2e/test_baseline_api.py -x`
Expected: FAIL — `TypeError: create_app() got an unexpected keyword argument 'baselines'`.

- [ ] **Step 3: Implémenter**

`api/errors.py`: importer `BaselineNotFoundError`, `DuplicateBaselineError`, `RestoreBlockedError`, `RestoreConflictError`; dans `_STATUS`:

```python
    # --- Baselines (docs/adr/BBBB) ---
    BaselineNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateBaselineError: (status.HTTP_409_CONFLICT, "duplicate"),
    RestoreBlockedError: (status.HTTP_409_CONFLICT, "restore_blocked"),
    RestoreConflictError: (status.HTTP_409_CONFLICT, "restore_conflict"),
```

`api/schemas.py` (imports: `from ea.domain.baselines import Baseline, BaselineDiff, EntityRef, Modified`; `from ea.services.baselines import RestoreOutcome`), bornes: `Against = Annotated[str, Field(max_length=36, description="A baseline id, or `current` for the model as it is now.")]`; en fin de fichier:

```python
# --- Baselines (docs/adr/BBBB) ----------------------------------------------


class BaselineCreate(_Input):
    name: Name
    description: Description = ""


class BaselineRead(BaseModel):
    id: UUID
    name: str
    description: str
    created_by: str
    created_at: datetime
    element_count: int
    relationship_count: int
    box_count: int

    @classmethod
    def of(cls, baseline: Baseline) -> BaselineRead:
        return cls(
            id=baseline.id,
            name=baseline.name,
            description=baseline.description,
            created_by=baseline.created_by,
            created_at=baseline.created_at,
            element_count=baseline.element_count,
            relationship_count=baseline.relationship_count,
            box_count=baseline.box_count,
        )


class EntityRefRead(BaseModel):
    kind: str
    id: UUID
    label: str
    type: str

    @classmethod
    def of(cls, ref: EntityRef) -> EntityRefRead:
        return cls(kind=ref.kind, id=ref.id, label=ref.label, type=ref.type)


class ModifiedRead(EntityRefRead):
    changes: list[FieldChangeRead]

    @classmethod
    def of_modified(cls, modified: Modified) -> ModifiedRead:
        return cls(
            kind=modified.kind,
            id=modified.id,
            label=modified.label,
            type=modified.type,
            changes=[_change(change) for change in modified.changes],
        )


class BaselineDiffRead(BaseModel):
    elements_added: list[EntityRefRead]
    elements_removed: list[EntityRefRead]
    elements_modified: list[ModifiedRead]
    relationships_added: list[EntityRefRead]
    relationships_removed: list[EntityRefRead]
    relationships_modified: list[ModifiedRead]

    @classmethod
    def of(cls, diff: BaselineDiff) -> BaselineDiffRead:
        return cls(
            elements_added=[EntityRefRead.of(r) for r in diff.elements_added],
            elements_removed=[EntityRefRead.of(r) for r in diff.elements_removed],
            elements_modified=[ModifiedRead.of_modified(m) for m in diff.elements_modified],
            relationships_added=[EntityRefRead.of(r) for r in diff.relationships_added],
            relationships_removed=[EntityRefRead.of(r) for r in diff.relationships_removed],
            relationships_modified=[ModifiedRead.of_modified(m) for m in diff.relationships_modified],
        )


class RestoreRequest(_Input):
    dry_run: bool = Field(default=True, description="Report what would change; write nothing.")
    confirm: bool = Field(default=False, description="Required to apply, i.e. with dry_run false.")
    keep_new: bool | None = Field(
        default=None, description="Keep what was created since; false (exact) when omitted."
    )


class RestorePlanRead(BaseModel):
    elements_to_create: int
    elements_to_update: int
    elements_to_delete: int
    relationships_to_create: int
    relationships_to_delete: int
    relationships_cascaded: int
    boxes_to_restore: int
    documents_lost: int
    boxes_lost: int
    warnings: list[str]
    blocked: list[EntityRefRead]


class RestoreRead(BaseModel):
    applied: bool
    plan: RestorePlanRead

    @classmethod
    def of(cls, outcome: RestoreOutcome) -> RestoreRead:
        plan = outcome.plan
        return cls(
            applied=outcome.applied,
            plan=RestorePlanRead(
                elements_to_create=len(plan.elements_to_create),
                elements_to_update=len(plan.elements_to_update),
                elements_to_delete=len(plan.elements_to_delete),
                relationships_to_create=len(plan.relationships_to_create),
                relationships_to_delete=len(plan.relationships_to_delete),
                relationships_cascaded=plan.cascaded_relationships,
                boxes_to_restore=len(plan.boxes_to_restore),
                documents_lost=plan.documents_lost,
                boxes_lost=plan.boxes_lost,
                warnings=list(plan.warnings),
                blocked=[
                    EntityRefRead(
                        kind="element", id=e.id, label=e.name, type=e.element_type.value
                    )
                    for e in plan.blocked
                ],
            ),
        )
```

`api/dependencies.py`: `baseline_service_of(app)`, `get_baseline_service`, `Baselines = Annotated[BaselineService, Depends(...)]` sur le modèle de `audit_service_of` (message: « no baseline service on the application — is postgres_enabled on? »).

```python
# backend/src/ea/api/baselines.py
"""Baselines over HTTP: capture, compare, restore (docs/adr/BBBB)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Baselines
from ea.api.schemas import (
    Against,
    BaselineCreate,
    BaselineDiffRead,
    BaselineRead,
    ErrorResponse,
    RestoreRead,
    RestoreRequest,
)

router = APIRouter(tags=["baselines"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}


def against_of(raw: str | None) -> UUID | None:
    """`current` and nothing mean the live model; anything else must be a baseline id."""
    return None if raw in (None, "", "current") else UUID(raw)  # ValueError -> 422 invalid_input


@router.get("/baselines", response_model=list[BaselineRead])
async def list_baselines(service: Baselines) -> list[BaselineRead]:
    return [BaselineRead.of(b) for b in await service.list_baselines()]


@router.post(
    "/baselines",
    response_model=BaselineRead,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def create_baseline(payload: BaselineCreate, service: Baselines) -> BaselineRead:
    """Freeze a copy of the whole model under a name."""
    return BaselineRead.of(
        await service.create(name=payload.name, description=payload.description)
    )


@router.get("/baselines/{baseline_id}", response_model=BaselineRead, responses=NOT_FOUND)
async def read_baseline(baseline_id: UUID, service: Baselines) -> BaselineRead:
    return BaselineRead.of(await service.get(baseline_id))


@router.delete(
    "/baselines/{baseline_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_baseline(baseline_id: UUID, service: Baselines) -> Response:
    await service.delete(baseline_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/baselines/{baseline_id}/diff", response_model=BaselineDiffRead, responses=NOT_FOUND)
async def read_baseline_diff(
    baseline_id: UUID,
    service: Baselines,
    against: Annotated[Against | None, Query()] = None,
) -> BaselineDiffRead:
    """What separates a baseline from another one, or from the model as it is now."""
    return BaselineDiffRead.of(await service.diff(baseline_id, against_of(against)))


@router.post(
    "/baselines/{baseline_id}/restore",
    response_model=RestoreRead,
    responses={
        **NOT_FOUND,
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def restore_baseline(
    baseline_id: UUID, payload: RestoreRequest, service: Baselines
) -> RestoreRead:
    """Put the model back as the baseline had it — a dry run unless told otherwise.

    Applying needs `dry_run=false` **and** `confirm=true`. One transaction: a
    conflict or a locked element changes nothing.
    """
    return RestoreRead.of(
        await service.restore(
            baseline_id,
            dry_run=payload.dry_run,
            confirm=payload.confirm,
            keep_new=payload.keep_new,
        )
    )
```

`main.py` — imports (`from ea.api.baselines import router as baselines_router`, `BaselineRepository` dans le bloc `ea.domain.ports`, `from ea.repositories.baseline_store import PostgresBaselineRepository`, `from ea.services.baselines import BaselineService`), puis, sur le modèle exact de l'audit (Task 8):

```python
# _lifespan(..., baselines: BaselineRepository | None = None)
            audit_store = audit
            baseline_store = baselines
            if settings.postgres_enabled:
                ...
                if baseline_store is None:
                    baseline_store = PostgresBaselineRepository(app.state.db_sessions)
            ...
            if baseline_store is not None:
                app.state.baseline_service = BaselineService(baseline_store)

# create_app(..., baselines: BaselineRepository | None = None)  → _lifespan(..., baselines=baselines)
    if baselines is not None:
        app.state.baseline_service = BaselineService(baselines)
    app.include_router(baselines_router, dependencies=[Authenticated], responses=AUTH_RESPONSES)
```

`frontend/src/features/history/auditLabels.ts`: ajouter `'baseline.created': 'Baseline capturée'`, `'baseline.deleted': 'Baseline supprimée'`, `'baseline.restored': 'Baseline restaurée'` à `ACTION_LABELS` et `baseline: 'Baseline'` à `KIND_LABELS`.

- [ ] **Step 4: Relancer et régénérer**

Run: `cd backend && uv run pytest tests/e2e tests/unit -x -q` — Expected: PASS (sauf `test_openapi` tant que le schéma n'est pas régénéré).
Run: `make openapi && make openapi-check` — Expected: « Client généré à jour. »
Run: `cd frontend && npm run typecheck && npm test -- --run tests/HistorySection.spec.ts` — Expected: sans erreur (les `Record` exhaustifs sont satisfaits).

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests/e2e/test_baseline_api.py backend/openapi.json frontend/src/api/schema.d.ts frontend/src/features/history/auditLabels.ts
git commit -m "feat(baselines): baseline endpoints with a confirmed, dry-run-first restore" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 20: Outils MCP des baselines

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports; `INSTRUCTIONS`; `BaselineProvider`; signature de `build_mcp_server`; outils), `backend/src/ea/main.py` (`_mount_mcp`), `backend/tests/unit/test_mcp_server.py` (fixtures, liste complète, bornes)

**Interfaces:**
- Consumes: `BaselineService`, schémas de Task 19.
- Produces: `build_mcp_server(get_service, get_documents, get_ipam, get_files, get_audit, get_baselines, *, ...)`; outils `list_baselines() -> list[BaselineRead]`, `create_baseline(name, description="") -> BaselineRead` (ADDS), `diff_baselines(baseline_id, against=None) -> BaselineDiffRead` (READS), `restore_baseline(baseline_id, dry_run=True, keep_new=None) -> RestoreRead` (**REMOVES**, la docstring le dit). Appliquer = `dry_run=false` (l'appel explicite vaut confirmation: `confirm=not dry_run`).

- [ ] **Step 1: Écrire les tests qui échouent** (dans `tests/unit/test_mcp_server.py`)

Fixtures: ajouter `baseline_service: BaselineService` et `lambda: baseline_service` comme sixième argument dans les deux fixtures (`server`, `server_without_an_index`); import `from ea.services.baselines import BaselineService`. Ajouter `"list_baselines", "create_baseline", "diff_baselines", "restore_baseline"` à la liste complète. Bornes (second `parametrize`): `("diff_baselines", "against", "/baselines/{baseline_id}/diff", "against")`; (premier `parametrize`) `("create_baseline", "name", BaselineCreate, "name")` et `("create_baseline", "description", BaselineCreate, "description")` avec `BaselineCreate` importé de `ea.api.schemas`. Classe:

```python
@pytest.mark.asyncio
class TestTheBaselineTools:
    async def test_restoring_is_a_dry_run_until_the_agent_says_otherwise(
        self, server: MCPServer[Any]
    ) -> None:
        gone = await an_element(server, "node", "gone")
        baseline = await call(server, "create_baseline", name="v1")
        await call(server, "delete_element", element_id=gone["id"])

        planned = await call(server, "restore_baseline", baseline_id=baseline["id"])
        still_gone = await server.call_tool("get_element", {"element_id": gone["id"]})
        applied = await call(
            server, "restore_baseline", baseline_id=baseline["id"], dry_run=False
        )
        back = await call(server, "get_element", element_id=gone["id"])

        assert planned["applied"] is False and planned["plan"]["elements_to_create"] == 1
        assert still_gone.is_error
        assert applied["applied"] is True
        assert back["name"] == "gone"

    async def test_the_destructive_tool_says_so_to_the_client(
        self, server: MCPServer[Any]
    ) -> None:
        tools = {t.name: t for t in await server.list_tools()}

        restore = tools["restore_baseline"]
        assert restore.annotations is not None and restore.annotations.destructive_hint is True
        assert "dry_run" in (restore.description or "")
        assert tools["list_baselines"].annotations is not None
        assert tools["list_baselines"].annotations.read_only_hint is True

    async def test_listing_and_diffing(self, server: MCPServer[Any]) -> None:
        baseline = await call(server, "create_baseline", name="v1")
        await an_element(server, "node", "new")

        listed = await call(server, "list_baselines")
        diff = await call(server, "diff_baselines", baseline_id=baseline["id"])

        assert [b["name"] for b in listed["result"]] == ["v1"]
        assert [e["label"] for e in diff["elements_added"]] == ["new"]
```

(`list_baselines` renvoie une liste: le contenu structuré FastMCP d'une liste est enveloppé sous `result` — si le SDK l'enveloppe autrement, adapter l'assertion à ce que renvoie `call`, jamais le contrat de l'outil.)

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: FAIL — `TypeError: build_mcp_server() takes 5 positional arguments but 6 were given`.

- [ ] **Step 3: Implémenter**

Imports `server.py`: `BaselineCreate`? (non — bornes via `Name`, `Description`), `Against, BaselineDiffRead, BaselineRead, RestoreRead`; `from ea.api.baselines import against_of`; `from ea.services.baselines import BaselineService`. `BaselineProvider = Callable[[], BaselineService]`. `INSTRUCTIONS` — ajouter:

```
A baseline is a frozen copy of the whole model. `create_baseline` captures one
before a risky change; `diff_baselines` compares it with the model as it is now
(or with another baseline); `restore_baseline` puts the model back. Restoring
**deletes what was created since** and overwrites what changed, so it always
starts as a dry run: read the plan, show it to the person you work for, and
only then call it again with `dry_run=false`.
```

Signature: ajouter `get_baselines: BaselineProvider` après `get_audit`. Outils:

```python
    # --- Baselines (docs/adr/BBBB) ----------------------------------------

    @server.tool(annotations=READS)
    @speaking_plainly
    async def list_baselines() -> list[BaselineRead]:
        """The baselines, by name, each with the counts of what it holds."""
        return [BaselineRead.of(b) for b in await get_baselines().list_baselines()]

    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def create_baseline(name: Name, description: Description = "") -> BaselineRead:
        """Freeze a copy of the whole model under a name.

        Cheap and non-destructive: do it before a large or risky change, so there
        is something to compare against and to go back to.
        """
        return BaselineRead.of(await get_baselines().create(name=name, description=description))

    @server.tool(annotations=READS)
    @speaking_plainly
    async def diff_baselines(
        baseline_id: UUID,
        against: Annotated[
            Against | None,
            Field(description="Another baseline id; omit it to compare with the model as it is now."),
        ] = None,
    ) -> BaselineDiffRead:
        """What was added, removed or modified between a baseline and another (or the live model)."""
        return BaselineDiffRead.of(await get_baselines().diff(baseline_id, against_of(against)))

    @server.tool(annotations=REMOVES)
    @speaking_plainly
    async def restore_baseline(
        baseline_id: UUID,
        dry_run: Annotated[
            bool, Field(description="True reports the plan and writes nothing. Set it to false to apply.")
        ] = True,
        keep_new: Annotated[
            bool | None, Field(description="Keep what was created since the baseline. Default: delete it.")
        ] = None,
    ) -> RestoreRead:
        """Put the model back as a baseline had it. **Destructive when `dry_run` is false.**

        With `dry_run=true` (the default) nothing is written: the answer counts
        what would be created, overwritten and deleted — elements, relationships,
        and the documents and diagram boxes that go with a deleted element. Read it
        to the person you work for, then call again with `dry_run=false`. A plan that
        touches a locked element is refused; unlock it first. All or nothing.
        """
        return RestoreRead.of(
            await get_baselines().restore(
                baseline_id, dry_run=dry_run, confirm=not dry_run, keep_new=keep_new
            )
        )
```

`main.py` `_mount_mcp` — ajouter le provider après celui de l'audit (import `baseline_service_of` depuis `ea.api.dependencies`):

```python
        lambda: audit_service_of(app),
        lambda: baseline_service_of(app),
        version=app.version,
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit tests/e2e -x -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(mcp): baseline tools, restore as a dry run unless told otherwise" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 21: SPA — section « Baselines » (liste, capture, diff)

**Files:**
- Create: `frontend/src/features/baselines/useBaselines.ts`, `frontend/src/features/baselines/BaselinesSection.vue`, `frontend/tests/useBaselines.spec.ts`, `frontend/tests/BaselinesSection.spec.ts`
- Modify: `frontend/src/router/sections.ts` (une entrée après `history`), `frontend/tests/support/api.ts` (fin)

**Interfaces:**
- Consumes: types générés `BaselineRead`, `BaselineDiffRead`, `RestoreRead`, `RestoreRequest`; `FieldDiff`; `useMe().canWrite`.
- Produces:
  - `useBaselines()` → `{ baselines, status, error, diff, diffStatus, diffError, load(), capture(name, description): Promise<void>, remove(id): Promise<void>, compare(a: string, b?: string): Promise<void>, restore(id, request): Promise<RestoreRead> }`
  - route `baselines` (`/baselines`), état `?a=` `?b=` (b absent = modèle courant)
  - `aBaseline(overrides)`, `aDiff(overrides)` dans `tests/support/api.ts`

- [ ] **Step 1: Écrire les tests qui échouent**

Ajouter à `frontend/tests/support/api.ts`:

```ts
type BaselineRead = components['schemas']['BaselineRead']
type BaselineDiffRead = components['schemas']['BaselineDiffRead']

export function aBaseline(overrides: Partial<BaselineRead> = {}): BaselineRead {
  return {
    id: '33333333-3333-4333-8333-333333333333',
    name: 'Avant migration',
    description: '',
    created_by: 'editor',
    created_at: '2026-10-10T12:00:00Z',
    element_count: 12,
    relationship_count: 20,
    box_count: 5,
    ...overrides,
  }
}

export function aDiff(overrides: Partial<BaselineDiffRead> = {}): BaselineDiffRead {
  return {
    elements_added: [],
    elements_removed: [],
    elements_modified: [],
    relationships_added: [],
    relationships_removed: [],
    relationships_modified: [],
    ...overrides,
  }
}
```

```ts
// frontend/tests/useBaselines.spec.ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useBaselines } from '../src/features/baselines/useBaselines'
import { aBaseline, aDiff, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useBaselines', () => {
  it('lists, captures and removes', async () => {
    const calls = stubApi([
      { path: '/baselines', body: [aBaseline()] },
      { method: 'POST', path: '/baselines', status: 201, body: aBaseline({ name: 'v2' }) },
      { method: 'DELETE', path: `/baselines/${aBaseline().id}`, status: 204 },
    ])
    const baselines = useBaselines()

    await baselines.load()
    await baselines.capture('v2', 'après')
    await baselines.remove(aBaseline().id)

    expect(baselines.baselines.value).toHaveLength(1)
    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ name: 'v2', description: 'après' })
    expect(calls.some((c) => c.method === 'DELETE')).toBe(true)
  })

  it('compares with the current model when no second baseline is named', async () => {
    const calls = stubApi([{ path: `/baselines/${aBaseline().id}/diff`, body: aDiff() }])
    const baselines = useBaselines()

    await baselines.compare(aBaseline().id)

    expect(calls[0]?.url.searchParams.has('against')).toBe(false)
    expect(baselines.diff.value).toEqual(aDiff())
  })

  it('compares two baselines under the name the backend reads', async () => {
    const calls = stubApi([{ path: `/baselines/${aBaseline().id}/diff`, body: aDiff() }])

    await useBaselines().compare(aBaseline().id, 'other-id')

    expect(calls[0]?.url.searchParams.get('against')).toBe('other-id')
  })
})
```

```ts
// frontend/tests/BaselinesSection.spec.ts
import { fireEvent, render, screen } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory } from 'vue-router'

import BaselinesSection from '../src/features/baselines/BaselinesSection.vue'
import { createAppRouter } from '../src/router'
import { aBaseline, aDiff, stubApi, type Route } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const LIST: Route = { path: '/baselines', body: [aBaseline()] }

async function open(query = '', routes: Route[] = [LIST]) {
  const calls = stubApi(routes)
  const router = createAppRouter(createMemoryHistory())
  await router.push(`/baselines${query}`)
  await router.isReady()
  render(BaselinesSection, { global: { plugins: [router] } })
  return { calls, router }
}

describe('BaselinesSection', () => {
  it('lists the baselines with what each holds', async () => {
    await open()

    expect(await screen.findByText('Avant migration')).toBeTruthy()
    expect(screen.getByText(/12 éléments/)).toBeTruthy()
  })

  it('captures a baseline under the name typed', async () => {
    const { calls } = await open('', [
      LIST,
      { method: 'POST', path: '/baselines', status: 201, body: aBaseline({ name: 'v2' }) },
    ])

    await fireEvent.update(await screen.findByLabelText('Nom'), 'v2')
    await fireEvent.click(screen.getByRole('button', { name: 'Capturer' }))

    expect(calls.find((c) => c.method === 'POST')?.body).toEqual({ name: 'v2', description: '' })
  })

  it('shows the three columns of a diff read from the URL', async () => {
    const { calls } = await open(`?a=${aBaseline().id}`, [
      LIST,
      {
        path: `/baselines/${aBaseline().id}/diff`,
        body: aDiff({
          elements_added: [{ kind: 'element', id: 'x', label: 'new', type: 'node' }],
          elements_removed: [{ kind: 'element', id: 'y', label: 'gone', type: 'node' }],
          elements_modified: [
            {
              kind: 'element',
              id: 'z',
              label: 'billing',
              type: 'node',
              changes: [{ field: 'name', before: 'a', after: 'billing' }],
            },
          ],
        }),
      },
    ])

    expect(await screen.findByText('new')).toBeTruthy()
    expect(screen.getByText('gone')).toBeTruthy()
    expect(screen.getByText('billing')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Ajoutés' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Supprimés' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Modifiés' })).toBeTruthy()
    expect(calls.some((c) => c.url.pathname.endsWith('/diff'))).toBe(true)
  })

  it('writes the chosen pair in the URL', async () => {
    const { router } = await open()

    await fireEvent.click(await screen.findByRole('button', { name: 'Comparer au modèle courant' }))

    expect(router.currentRoute.value.query.a).toBe(aBaseline().id)
  })
})
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd frontend && npm test -- --run tests/useBaselines.spec.ts tests/BaselinesSection.spec.ts`
Expected: FAIL — « Failed to resolve import "../src/features/baselines/useBaselines" ».

- [ ] **Step 3: Implémenter**

```ts
// frontend/src/features/baselines/useBaselines.ts
// Baselines (docs/adr/BBBB): the list, the capture, the diff, the restoration.
//
// The diff and the list are two independent questions, so they do not cancel
// each other (`useLatestRequest`, one instance each). A restoration is a write:
// its refusal is the screen's to show, not this composable's state.
import { ref } from 'vue'

import type { components } from '../../api/schema'
import { api, unwrap } from '../../lib/api'
import { useLatestRequest } from '../../lib/latest'

export type Baseline = components['schemas']['BaselineRead']
export type BaselineDiff = components['schemas']['BaselineDiffRead']
export type RestoreRequest = components['schemas']['RestoreRequest']
export type RestoreResult = components['schemas']['RestoreRead']

export function useBaselines() {
  const baselines = ref<Baseline[]>([])
  const diff = ref<BaselineDiff | null>(null)
  const listing = useLatestRequest()
  const comparing = useLatestRequest()

  async function load(): Promise<void> {
    await listing.run(
      async (signal) => unwrap(await api.GET('/baselines', { signal })),
      (answer) => {
        baselines.value = answer
      },
    )
  }

  async function capture(name: string, description: string): Promise<void> {
    unwrap(await api.POST('/baselines', { body: { name, description } }))
    await load()
  }

  async function remove(id: string): Promise<void> {
    unwrap(await api.DELETE('/baselines/{baseline_id}', { params: { path: { baseline_id: id } } }))
    await load()
  }

  /** `against` absent = the model as it is now. */
  async function compare(id: string, against?: string): Promise<void> {
    diff.value = null
    await comparing.run(
      async (signal) =>
        unwrap(
          await api.GET('/baselines/{baseline_id}/diff', {
            params: { path: { baseline_id: id }, query: { against } },
            signal,
          }),
        ),
      (answer) => {
        diff.value = answer
      },
    )
  }

  async function restore(id: string, request: RestoreRequest): Promise<RestoreResult> {
    return unwrap(
      await api.POST('/baselines/{baseline_id}/restore', {
        params: { path: { baseline_id: id } },
        body: request,
      }),
    )
  }

  return {
    baselines,
    status: listing.status,
    error: listing.error,
    diff,
    diffStatus: comparing.status,
    diffError: comparing.error,
    load,
    capture,
    remove,
    compare,
    restore,
  }
}
```

```vue
<!-- frontend/src/features/baselines/BaselinesSection.vue -->
<script setup lang="ts">
// Les baselines (docs/adr/BBBB) : des copies figées du modèle, comparables.
//
// La paire comparée est dans l'URL (`?a=` la baseline, `?b=` l'autre ; `b` absent
// = le modèle courant) : une question qu'on partage. L'API décide qui écrit ;
// ici on cache seulement ce qu'un lecteur ne peut pas faire.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { messageOf } from '../../lib/api'
import { useMe } from '../../lib/me'
import FieldDiff from '../history/FieldDiff.vue'
import RestoreDialog from './RestoreDialog.vue'
import { useBaselines, type Baseline } from './useBaselines'

const route = useRoute()
const router = useRouter()
const { canWrite } = useMe()
const store = useBaselines()

const name = ref('')
const description = ref('')
const failure = ref('')
const restoring = ref<Baseline | null>(null)

const a = computed(() => (typeof route.query.a === 'string' ? route.query.a : ''))
const b = computed(() => (typeof route.query.b === 'string' ? route.query.b : ''))
const subject = computed(() => store.baselines.value.find((candidate) => candidate.id === a.value))

void store.load()
watch(
  [a, b],
  ([first, second]) => {
    if (first) {
      void store.compare(first, second || undefined)
    } else {
      store.diff.value = null
    }
  },
  { immediate: true },
)

function compareWith(id: string, other?: string): void {
  void router.push({ query: { a: id, b: other } })
}

async function capture(): Promise<void> {
  failure.value = ''
  try {
    await store.capture(name.value, description.value)
    name.value = ''
    description.value = ''
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

async function remove(id: string): Promise<void> {
  failure.value = ''
  try {
    await store.remove(id)
  } catch (caught) {
    failure.value = messageOf(caught)
  }
}

function onRestored(): void {
  restoring.value = null
  void store.load()
  if (a.value) {
    void store.compare(a.value, b.value || undefined)
  }
}
</script>

<template>
  <section aria-labelledby="baselines-title">
    <h2 id="baselines-title">Baselines</h2>
    <p v-if="failure" role="alert">{{ failure }}</p>

    <form v-if="canWrite" class="capture" @submit.prevent="capture">
      <label>Nom <input v-model="name" type="text" required maxlength="200" /></label>
      <label>Description <input v-model="description" type="text" maxlength="2000" /></label>
      <button type="submit">Capturer</button>
    </form>

    <p v-if="store.status.value === 'error'" role="alert">{{ store.error.value }}</p>
    <ul class="baselines">
      <li v-for="baseline in store.baselines.value" :key="baseline.id">
        <strong>{{ baseline.name }}</strong>
        <span>
          {{ baseline.element_count }} éléments, {{ baseline.relationship_count }} relations,
          {{ baseline.box_count }} boîtes — {{ baseline.created_by }},
          {{ baseline.created_at.slice(0, 10) }}
        </span>
        <button type="button" @click="compareWith(baseline.id)">Comparer au modèle courant</button>
        <button v-if="a && a !== baseline.id" type="button" @click="compareWith(a, baseline.id)">
          Comparer à {{ subject?.name }}
        </button>
        <template v-if="canWrite">
          <button type="button" @click="restoring = baseline">Restaurer</button>
          <button type="button" class="secondary" @click="remove(baseline.id)">Supprimer</button>
        </template>
      </li>
    </ul>

    <section v-if="a" class="diff" aria-label="Différences">
      <h3>{{ subject?.name }} → {{ b ? 'autre baseline' : 'modèle courant' }}</h3>
      <p v-if="store.diffStatus.value === 'error'" role="alert">{{ store.diffError.value }}</p>
      <div v-if="store.diff.value" class="columns">
        <div>
          <h4>Ajoutés</h4>
          <ul>
            <li v-for="entity in [...store.diff.value.elements_added, ...store.diff.value.relationships_added]" :key="entity.id">{{ entity.label }}</li>
          </ul>
        </div>
        <div>
          <h4>Supprimés</h4>
          <ul>
            <li v-for="entity in [...store.diff.value.elements_removed, ...store.diff.value.relationships_removed]" :key="entity.id">{{ entity.label }}</li>
          </ul>
        </div>
        <div>
          <h4>Modifiés</h4>
          <ul>
            <li v-for="entity in [...store.diff.value.elements_modified, ...store.diff.value.relationships_modified]" :key="entity.id">
              {{ entity.label }}
              <FieldDiff :changes="entity.changes" />
            </li>
          </ul>
        </div>
      </div>
    </section>

    <RestoreDialog
      v-if="restoring"
      :baseline="restoring"
      @close="restoring = null"
      @restored="onRestored"
    />
  </section>
</template>

<style scoped>
.capture {
  display: flex;
  flex-wrap: wrap;
  gap: 0.75rem;
  align-items: end;
  margin-bottom: 1rem;
}
.baselines {
  list-style: none;
  padding: 0;
  display: grid;
  gap: 0.5rem;
}
.baselines li {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: baseline;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0.5rem 0.8rem;
}
.columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
  gap: 1rem;
}
</style>
```

(Les titres de colonnes `h4` s'exposent au rôle `heading` avec leur nom: les tests `getByRole('heading', { name: 'Ajoutés' })` passent. Le composant `RestoreDialog.vue` est créé à la Task 22; pour que ce commit reste vert, créer d'abord un stub `RestoreDialog.vue` de 6 lignes — `<script setup lang="ts">defineProps<{ baseline: { id: string; name: string } }>(); defineEmits<{ close: []; restored: [] }>()</script><template><div /></template>` — que la Task 22 remplace.)

`sections.ts`: entrée après `history`:

```ts
  {
    path: '/baselines',
    name: 'baselines',
    label: 'Baselines',
    summary: 'Figer le modèle, comparer deux états, restaurer.',
    group: 'governance',
    view: () => import('../features/baselines/BaselinesSection.vue'),
  },
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/useBaselines.spec.ts tests/BaselinesSection.spec.ts tests/router.spec.ts` — Expected: PASS. Run: `cd frontend && npm run lint && npm run typecheck` — Expected: sans erreur.

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/tests
git commit -m "feat(spa): baselines section with capture and a three-column diff in the URL" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 22: SPA — boîte de dialogue de restauration

**Files:**
- Modify: `frontend/src/features/baselines/RestoreDialog.vue` (remplace le stub)
- Create: `frontend/tests/RestoreDialog.spec.ts`

**Interfaces:**
- Consumes: `useBaselines().restore`, type `RestoreResult`.
- Produces: `<RestoreDialog :baseline="Baseline" @close @restored />` — au montage, appelle `restore(id, { dry_run: true })` et affiche les comptes; le bouton « Restaurer » est inactif tant que la saisie ≠ nom exact de la baseline, ou tant que `plan.blocked` n'est pas vide; il envoie `{ dry_run: false, confirm: true }`.

- [ ] **Step 1: Écrire le test qui échoue**

```ts
// frontend/tests/RestoreDialog.spec.ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import RestoreDialog from '../src/features/baselines/RestoreDialog.vue'
import { aBaseline, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

const URL_OF = `/baselines/${aBaseline().id}/restore`

function plan(overrides: Record<string, unknown> = {}) {
  return {
    applied: false,
    plan: {
      elements_to_create: 1,
      elements_to_update: 2,
      elements_to_delete: 3,
      relationships_to_create: 0,
      relationships_to_delete: 0,
      relationships_cascaded: 7,
      boxes_to_restore: 0,
      documents_lost: 2,
      boxes_lost: 5,
      warnings: [],
      blocked: [],
      ...overrides,
    },
  }
}

describe('RestoreDialog', () => {
  it('asks for a dry run first and shows what would be lost', async () => {
    const calls = stubApi([{ method: 'POST', path: URL_OF, body: plan() }])
    render(RestoreDialog, { props: { baseline: aBaseline() } })

    expect(await screen.findByText(/3 éléments, 7 relations, 2 documents, 5 boîtes/)).toBeTruthy()
    expect(calls[0]?.body).toMatchObject({ dry_run: true })
  })

  it('applies only once the name of the baseline has been typed', async () => {
    stubApi([{ method: 'POST', path: URL_OF, body: plan() }])
    const { emitted } = render(RestoreDialog, { props: { baseline: aBaseline() } })
    const button = await screen.findByRole('button', { name: 'Restaurer' })

    expect((button as HTMLButtonElement).disabled).toBe(true)
    await fireEvent.update(screen.getByLabelText(/Saisir le nom/), 'Avant migration')
    expect((button as HTMLButtonElement).disabled).toBe(false)
    stubApi([{ method: 'POST', path: URL_OF, body: { ...plan(), applied: true } }])
    await fireEvent.click(button)

    await waitFor(() => expect(emitted().restored).toBeTruthy())
  })

  it('refuses to apply while a locked element is in the way', async () => {
    stubApi([
      {
        method: 'POST',
        path: URL_OF,
        body: plan({ blocked: [{ kind: 'element', id: 'x', label: 'billing', type: 'node' }] }),
      },
    ])
    render(RestoreDialog, { props: { baseline: aBaseline() } })

    expect(await screen.findByText(/billing/)).toBeTruthy()
    await fireEvent.update(screen.getByLabelText(/Saisir le nom/), 'Avant migration')
    expect(
      (screen.getByRole('button', { name: 'Restaurer' }) as HTMLButtonElement).disabled,
    ).toBe(true)
  })
})
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd frontend && npm test -- --run tests/RestoreDialog.spec.ts`
Expected: FAIL — pas de comptes affichés (le stub rend `<div />`).

- [ ] **Step 3: Implémenter**

```vue
<!-- frontend/src/features/baselines/RestoreDialog.vue -->
<script setup lang="ts">
// Restaurer une baseline : un essai à blanc d'abord, la saisie du nom ensuite.
//
// Le plan vient de l'API (`dry_run`) ; ce composant l'affiche, il ne décide de
// rien. Le bouton reste inactif tant que le nom exact n'est pas saisi, et tant
// qu'un élément verrouillé bloque : l'API refuserait de toute façon (409).
import { computed, onMounted, ref } from 'vue'

import { messageOf } from '../../lib/api'
import { useBaselines, type Baseline, type RestoreResult } from './useBaselines'

const props = defineProps<{ baseline: Baseline | { id: string; name: string } }>()
const emit = defineEmits<{ close: []; restored: [] }>()

const store = useBaselines()
const result = ref<RestoreResult | null>(null)
const typed = ref('')
const failure = ref('')
const busy = ref(false)

const plan = computed(() => result.value?.plan ?? null)
const canApply = computed(
  () => typed.value === props.baseline.name && plan.value !== null && plan.value.blocked.length === 0,
)

onMounted(async () => {
  try {
    result.value = await store.restore(props.baseline.id, { dry_run: true })
  } catch (caught) {
    failure.value = messageOf(caught)
  }
})

async function apply(): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    await store.restore(props.baseline.id, { dry_run: false, confirm: true })
    emit('restored')
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section role="dialog" aria-modal="true" :aria-label="`Restaurer ${baseline.name}`" class="dialog">
    <h3>Restaurer « {{ baseline.name }} »</h3>
    <p v-if="failure" role="alert">{{ failure }}</p>
    <p v-if="!plan && !failure">Calcul du plan…</p>

    <template v-if="plan">
      <p>
        Seront créés : {{ plan.elements_to_create }} éléments, {{ plan.relationships_to_create }}
        relations. Seront remis à l'état de la baseline : {{ plan.elements_to_update }} éléments.
      </p>
      <p class="loss">
        Seront supprimés : {{ plan.elements_to_delete }} éléments, {{ plan.relationships_cascaded }}
        relations, {{ plan.documents_lost }} documents, {{ plan.boxes_lost }} boîtes.
      </p>
      <ul v-if="plan.warnings.length">
        <li v-for="warning in plan.warnings" :key="warning">{{ warning }}</li>
      </ul>
      <div v-if="plan.blocked.length" role="alert">
        Éléments verrouillés à déverrouiller d'abord :
        <ul>
          <li v-for="entity in plan.blocked" :key="entity.id">{{ entity.label }}</li>
        </ul>
      </div>
      <label>
        Saisir le nom de la baseline pour confirmer
        <input v-model="typed" type="text" autocomplete="off" />
      </label>
    </template>

    <p class="actions">
      <button type="button" class="secondary" @click="emit('close')">Annuler</button>
      <button type="button" :disabled="!canApply || busy" @click="apply">Restaurer</button>
    </p>
  </section>
</template>

<style scoped>
.dialog {
  position: fixed;
  inset: 10% 5% auto;
  max-width: 40rem;
  margin: 0 auto;
  padding: 1rem 1.2rem;
  background: var(--bg, canvas);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: 0 8px 30px rgb(0 0 0 / 25%);
}
.loss {
  font-weight: 600;
}
.actions {
  display: flex;
  gap: 0.6rem;
  justify-content: end;
}
</style>
```

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run tests/RestoreDialog.spec.ts tests/BaselinesSection.spec.ts` — Expected: PASS. Run: `cd frontend && npm run lint && npm run typecheck` — Expected: sans erreur.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/baselines/RestoreDialog.vue frontend/tests/RestoreDialog.spec.ts
git commit -m "feat(spa): restore dialog with dry run and typed confirmation" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 23: Vérification du lot 2 et ADR des baselines

**Files:**
- Create: `docs/adr/N2-baselines-en-copie-normalisee.md` (N2 = numéro suivant `N1`)
- Modify: occurrences `docs/adr/BBBB` → `docs/adr/N2`

- [ ] **Step 1: Fixer le numéro**

Run: `ls docs/adr | tail -3`, puis `grep -rln "docs/adr/BBBB" backend frontend` et `sed -i '' 's#docs/adr/BBBB#docs/adr/N2#g'` sur la liste relue. Expected: `grep -rn "BBBB" backend frontend` ne retourne rien.

- [ ] **Step 2: Écrire l'ADR**

```markdown
---
titre: Baselines en copie normalisée du graphe
date: 2026-10-10
statut: Proposition
affects: domain/baselines.py, services/baselines.py, repositories/baseline_store.py, api/baselines.py, mcp/server.py, migrations 0010
---

# Baselines en copie normalisée du graphe

## Contexte

Il faut pouvoir figer le modèle avant un changement risqué, comparer deux états
et revenir en arrière. Le modèle existant n'a pas d'historique avant l'ADR du
journal d'audit (N1). Volume visé: centaines à quelques milliers d'éléments.

## Décision

- Une baseline est une **copie normalisée** (`baselines`, `baseline_elements`,
  `baseline_relationships`, `baseline_boxes`, migration 0010), capturée par
  trois `INSERT … SELECT` dans une transaction: complète ou absente, sans que
  les données passent par Python. Aucune clé vers le graphe vivant.
- Le diff est une comparaison de deux ensembles de valeurs (`diff_snapshots`),
  insensible aux horodatages. `GET /baselines/{id}/diff?against=<id|current>`.
- La restauration est décidée par une fonction pure (`plan_restore`) et
  appliquée par `apply_restore` en une transaction `SERIALIZABLE`: éléments
  recréés sous leur **id d'origine**, éléments modifiés remis à la valeur de la
  baseline, éléments créés depuis supprimés (`keep_new=false`, par défaut) avec
  leurs liens, documents et boîtes par cascade; une relation devenue illégale
  est un avertissement. Un conflit d'unicité (nom, adresse IP) annule tout:
  `409 restore_conflict`.
- Deux gestes: `dry_run=true` par défaut (le plan, sans écriture), puis
  `dry_run=false` **et** `confirm=true`. L'écran exige de saisir le nom.
- Audit: un `baseline.restored` puis un événement par entité touchée, même
  `request_id`, `correlation='restore:<id>'`.
- Pas de `package_id` tant que le sous-projet 1 n'existe pas.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| JSONB unique par baseline | Écriture simple | Diff et recherche dans du JSON, ligne qui grossit sans borne | Écartée |
| Reconstruction depuis l'audit | Aucun stockage dupliqué | Exacte seulement depuis le début du journal (le modèle existant n'a pas d'historique), cassée par une purge | Écartée |
| Capturer aussi les documents | Restauration complète | Textes lourds dupliqués à chaque baseline (R5) | Reportée: le `dry_run` annonce les documents perdus |

## Conséquences

- Espace: de l'ordre de la taille du graphe par baseline.
- Les documents d'un élément supprimé puis restauré sont perdus; les boîtes ne
  reviennent que si leur diagramme existe encore.
- Le plan est calculé avant la transaction (l'exactitude est celle de l'instant
  du calcul); `apply_restore` revérifie ses cibles et passe en `SERIALIZABLE`;
  un conflit concurrent est un `409` rejouable. À reconsidérer si les
  écritures concurrentes deviennent fréquentes.
- Le lot « approbation » apprend à la restauration à refuser un élément verrouillé.

## Références

- ADR liés: N1 (journal d'audit), [0033](0033-postgresql-seul-pour-le-graphe.md), [0031](0031-diagrammes-enregistres.md)
- Code: `backend/src/ea/domain/baselines.py`, `backend/src/ea/repositories/baseline_store.py`
```

- [ ] **Step 3: Vérifier le lot**

Run: `make check` — Expected: PASS.
Run: `make test-integration` — Expected: PASS (0010 appliquée puis annulée, restauration, atomicité).

- [ ] **Step 4: Commit**

```bash
git add docs/adr backend frontend
git commit -m "docs(adr): baselines as a normalised copy of the graph" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 3 — Approbation et verrouillage (B6)

> `docs/adr/CCCC` désigne l'ADR de l'approbation, fixé à la Task 32. Le rôle de royaume `ea-approver` s'ajoute à l'import du realm **hors de ce repo** (dépôt Infra): aucune tâche de ce plan ne le crée; tout ce qui suit se teste avec des `Caller` portant le rôle.

### Task 24: Domaine `approval`, rôle `ea-approver`, champs de l'élément

**Files:**
- Create: `backend/src/ea/domain/approval.py`, `backend/tests/unit/test_approval.py`
- Modify: `backend/src/ea/domain/model.py` (imports lignes 14-26; `Element`, lignes 64-80), `backend/src/ea/domain/auth.py`, `backend/src/ea/domain/errors.py` (fin), `backend/src/ea/services/caller.py` (imports + `require_approver`), `backend/tests/conftest.py` (`an_approver`, import de `APPROVER_ROLE`)

**Interfaces:**
- Consumes: `Caller`, `Element`.
- Produces:
  - `domain/auth.py`: `APPROVER_ROLE = "ea-approver"`, `Caller.can_approve -> bool`; `LOCAL_DEVELOPER` et `SYSTEM` portent `{EDITOR_ROLE, APPROVER_ROLE}`
  - `domain/approval.py`: `ApprovalState` (`DRAFT="draft"`, `IN_REVIEW="in_review"`, `APPROVED="approved"`), `Transition` (`SUBMIT`, `APPROVE`, `REJECT`, `REOPEN`, `LOCK`, `UNLOCK` = `"submit"`…), `APPROVER_TRANSITIONS: frozenset[Transition]`, `allowed_transitions(state, locked, caller) -> tuple[Transition, ...]`, `apply_transition(element, transition, caller, *, now) -> Element` (403 `NotAuthorisedError` puis 409 `InvalidTransitionError`), `check_modifiable(element, caller) -> None` (`ElementLockedError`), `reviewed_after_change(element) -> Element`
  - `Element` gagne `approval_state: ApprovalState = DRAFT`, `locked: bool = False`, `approved_by_sub: str | None = None`, `approved_at: datetime | None = None` (en dernier, avec défauts)
  - erreurs `ElementLockedError`, `InvalidTransitionError`
  - `services/caller.py`: `require_approver() -> Caller` (échoue fermé: `NotAuthorisedError`)
  - `tests/conftest.py`: `an_approver(**overrides) -> Caller` (sujet `approver-1`, rôles éditeur **et** approbateur: les rôles se cumulent)

- [ ] **Step 1: Écrire le test qui échoue**

```python
# backend/tests/unit/test_approval.py
"""Who may move an element through review, and what review forbids (docs/adr/CCCC)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.approval import Transition as T
from ea.domain.approval import (
    allowed_transitions,
    apply_transition,
    check_modifiable,
    reviewed_after_change,
)
from ea.domain.archimate import ElementType as E
from ea.domain.auth import APPROVER_ROLE, LOCAL_DEVELOPER, SYSTEM, Caller
from ea.domain.errors import ElementLockedError, InvalidTransitionError, NotAuthorisedError
from ea.domain.model import Element
from tests.conftest import a_reader, an_approver, an_editor

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
LATER = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


def an_element(state: S = S.DRAFT, locked: bool = False) -> Element:
    element = Element.create(element_type=E.NODE, name="db-01", now=NOW)
    approved = state is S.APPROVED
    return replace(
        element,
        approval_state=state,
        locked=locked,
        approved_by_sub="approver-1" if approved else None,
        approved_at=NOW if approved else None,
    )


class TestTheRoles:
    def test_the_approver_role_is_its_own_and_cumulates_with_the_editor_role(self) -> None:
        assert APPROVER_ROLE == "ea-approver"
        assert an_approver().can_write and an_approver().can_approve
        assert not an_editor().can_approve
        assert Caller("s", "n", frozenset({APPROVER_ROLE})).can_approve
        assert not Caller("s", "n", frozenset({APPROVER_ROLE})).can_write  # not implied
        assert LOCAL_DEVELOPER.can_approve and SYSTEM.can_approve

    def test_a_new_element_is_a_draft_nobody_has_approved(self) -> None:
        element = an_element()

        assert (element.approval_state, element.locked) == (S.DRAFT, False)
        assert element.approved_by_sub is None and element.approved_at is None
        assert element.rename("other", now=LATER).approval_state is S.DRAFT


class TestWhatEachRoleMayDo:
    @pytest.mark.parametrize(
        ("state", "locked", "editor", "approver"),
        [
            (S.DRAFT, False, (T.SUBMIT,), (T.SUBMIT,)),
            (S.IN_REVIEW, False, (), (T.APPROVE, T.REJECT)),
            (S.APPROVED, False, (), (T.REOPEN, T.LOCK)),
            (S.APPROVED, True, (), (T.UNLOCK,)),
        ],
    )
    def test_the_table_of_transitions(
        self,
        state: S,
        locked: bool,
        editor: tuple[T, ...],
        approver: tuple[T, ...],
    ) -> None:
        assert allowed_transitions(state, locked, an_editor()) == editor
        assert allowed_transitions(state, locked, an_approver()) == approver
        assert allowed_transitions(state, locked, a_reader()) == ()


class TestApplyingATransition:
    def test_submit_then_approve_records_who_and_when(self) -> None:
        submitted = apply_transition(an_element(), T.SUBMIT, an_editor(), now=LATER)
        approved = apply_transition(submitted, T.APPROVE, an_approver(), now=LATER)

        assert submitted.approval_state is S.IN_REVIEW
        assert approved.approval_state is S.APPROVED
        assert (approved.approved_by_sub, approved.approved_at) == ("approver-1", LATER)

    def test_reopening_forgets_the_approval_and_the_lock(self) -> None:
        reopened = apply_transition(an_element(S.APPROVED, locked=True), T.UNLOCK, an_approver(), now=LATER)
        draft = apply_transition(reopened, T.REOPEN, an_approver(), now=LATER)

        assert reopened.locked is False and reopened.approval_state is S.APPROVED
        assert draft.approval_state is S.DRAFT
        assert draft.approved_by_sub is None and draft.approved_at is None

    def test_lock_and_reject(self) -> None:
        locked = apply_transition(an_element(S.APPROVED), T.LOCK, an_approver(), now=LATER)
        rejected = apply_transition(an_element(S.IN_REVIEW), T.REJECT, an_approver(), now=LATER)

        assert locked.locked is True
        assert rejected.approval_state is S.DRAFT

    def test_a_missing_role_is_a_403_before_the_state_is_even_looked_at(self) -> None:
        with pytest.raises(NotAuthorisedError):
            apply_transition(an_element(S.IN_REVIEW), T.APPROVE, an_editor(), now=LATER)
        with pytest.raises(NotAuthorisedError):
            apply_transition(an_element(), T.SUBMIT, a_reader(), now=LATER)

    def test_a_transition_the_state_forbids_is_a_409(self) -> None:
        for state, transition in [
            (S.DRAFT, T.APPROVE),
            (S.IN_REVIEW, T.SUBMIT),
            (S.IN_REVIEW, T.LOCK),
            (S.APPROVED, T.REJECT),
        ]:
            with pytest.raises(InvalidTransitionError):
                apply_transition(an_element(state), transition, an_approver(), now=LATER)
        with pytest.raises(InvalidTransitionError):  # a locked element only unlocks
            apply_transition(an_element(S.APPROVED, locked=True), T.REOPEN, an_approver(), now=LATER)


class TestWhatReviewForbids:
    def test_a_draft_and_an_element_in_review_stay_editable(self) -> None:
        check_modifiable(an_element(S.DRAFT), an_editor())
        check_modifiable(an_element(S.IN_REVIEW), an_editor())

    def test_an_approved_element_is_for_approvers_only(self) -> None:
        with pytest.raises(ElementLockedError, match="approved"):
            check_modifiable(an_element(S.APPROVED), an_editor())
        check_modifiable(an_element(S.APPROVED), an_approver())

    def test_a_locked_element_is_for_nobody(self) -> None:
        with pytest.raises(ElementLockedError, match="locked"):
            check_modifiable(an_element(S.APPROVED, locked=True), an_approver())

    def test_changing_an_approved_element_sends_it_back_to_review(self) -> None:
        back = reviewed_after_change(an_element(S.APPROVED))

        assert back.approval_state is S.IN_REVIEW
        assert back.approved_by_sub is None and back.approved_at is None
        draft = an_element(S.DRAFT)
        assert reviewed_after_change(draft) is draft
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_approval.py -x`
Expected: FAIL — `ImportError: cannot import name 'APPROVER_ROLE'` (ou `an_approver`).

- [ ] **Step 3: Rôle, erreurs, garde**

`domain/auth.py`:

```python
#: The realm role that approves, rejects, reopens and locks an element. It does
#: not imply `EDITOR_ROLE`: roles accumulate (docs/adr/CCCC).
APPROVER_ROLE: Final = "ea-approver"
```

dans `Caller`:

```python
    @property
    def can_approve(self) -> bool:
        return APPROVER_ROLE in self.roles
```

et `LOCAL_DEVELOPER`/`SYSTEM` reçoivent `frozenset({EDITOR_ROLE, APPROVER_ROLE})`.

`domain/errors.py` (fin):

```python


class ElementLockedError(DomainError):
    """An element in review's last stage — approved, or locked — refuses this change."""


class InvalidTransitionError(DomainError):
    """A review transition the element's current state does not offer."""
```

`services/caller.py`: importer `APPROVER_ROLE`; ajouter

```python
def require_approver() -> Caller:
    caller = require_caller()
    if not caller.can_approve:
        msg = f"approving or locking an element needs the {APPROVER_ROLE} role"
        raise NotAuthorisedError(msg)
    return caller
```

`tests/conftest.py`: `from ea.domain.auth import APPROVER_ROLE, EDITOR_ROLE, Caller` et

```python
def an_approver(**overrides: object) -> Caller:
    """An approver who is also an editor: the roles accumulate."""
    return an_editor(
        **{
            "subject": "approver-1",
            "username": "approver",
            "roles": frozenset({EDITOR_ROLE, APPROVER_ROLE}),
            **overrides,
        }
    )
```

- [ ] **Step 4: Le domaine**

```python
# backend/src/ea/domain/approval.py
"""Review and locking of an element — see docs/adr/CCCC.

Pure: the table of transitions, who may take each, and what each state forbids
are decided here. `ArchitectureService` applies them in the transaction that
writes the element. The SPA restates none of it: it is handed
`allowed_transitions` by the API and draws a button for each.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from ea.domain.errors import ElementLockedError, InvalidTransitionError, NotAuthorisedError

if TYPE_CHECKING:
    from ea.domain.auth import Caller
    from ea.domain.model import Element


class ApprovalState(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"


class Transition(StrEnum):
    SUBMIT = "submit"
    APPROVE = "approve"
    REJECT = "reject"
    REOPEN = "reopen"
    LOCK = "lock"
    UNLOCK = "unlock"


#: Everything but `submit` is the approver's.
APPROVER_TRANSITIONS: Final = frozenset(
    {Transition.APPROVE, Transition.REJECT, Transition.REOPEN, Transition.LOCK, Transition.UNLOCK}
)

#: (state, locked) -> the transitions the state offers, before any role is asked.
_OFFERED: Final[dict[tuple[ApprovalState, bool], tuple[Transition, ...]]] = {
    (ApprovalState.DRAFT, False): (Transition.SUBMIT,),
    (ApprovalState.IN_REVIEW, False): (Transition.APPROVE, Transition.REJECT),
    (ApprovalState.APPROVED, False): (Transition.REOPEN, Transition.LOCK),
    (ApprovalState.APPROVED, True): (Transition.UNLOCK,),
}


def _may(transition: Transition, caller: Caller) -> bool:
    return caller.can_approve if transition in APPROVER_TRANSITIONS else caller.can_write


def allowed_transitions(
    state: ApprovalState, locked: bool, caller: Caller
) -> tuple[Transition, ...]:
    return tuple(t for t in _OFFERED.get((state, locked), ()) if _may(t, caller))


def apply_transition(
    element: Element, transition: Transition, caller: Caller, *, now: datetime
) -> Element:
    """The element after the transition; 403 if the role is missing, 409 if the state forbids it.

    The role is checked first: whether a given state offers a transition is not
    something a caller without the role needs to learn.
    """
    if not _may(transition, caller):
        needed = "ea-approver" if transition in APPROVER_TRANSITIONS else "ea-editor"
        msg = f"{transition.value} needs the {needed} role"
        raise NotAuthorisedError(msg)
    if transition not in _OFFERED.get((element.approval_state, element.locked), ()):
        state = "locked" if element.locked else element.approval_state.value
        msg = f"{element.name!r} is {state}: it cannot be {transition.value}ed"
        raise InvalidTransitionError(msg)
    match transition:
        case Transition.SUBMIT:
            return replace(element, approval_state=ApprovalState.IN_REVIEW)
        case Transition.APPROVE:
            return replace(
                element,
                approval_state=ApprovalState.APPROVED,
                approved_by_sub=caller.subject,
                approved_at=now,
            )
        case Transition.REJECT:
            return replace(element, approval_state=ApprovalState.DRAFT)
        case Transition.REOPEN:
            return replace(
                element,
                approval_state=ApprovalState.DRAFT,
                approved_by_sub=None,
                approved_at=None,
                locked=False,
            )
        case Transition.LOCK:
            return replace(element, locked=True)
        case Transition.UNLOCK:
            return replace(element, locked=False)


def check_modifiable(element: Element, caller: Caller) -> None:
    """Refuse a change the element's state forbids this caller."""
    if element.locked:
        msg = f"{element.name!r} is locked: an approver must unlock it first"
        raise ElementLockedError(msg)
    if element.approval_state is ApprovalState.APPROVED and not caller.can_approve:
        msg = f"{element.name!r} is approved: only an approver may change it"
        raise ElementLockedError(msg)


def reviewed_after_change(element: Element) -> Element:
    """An approved element that has been changed is in review again — never approved in silence."""
    if element.approval_state is not ApprovalState.APPROVED:
        return element
    return replace(
        element, approval_state=ApprovalState.IN_REVIEW, approved_by_sub=None, approved_at=None
    )
```

`domain/model.py`: ajouter `from ea.domain.approval import ApprovalState` (avec les imports `ea.domain.*`) et, dans `Element`, après `properties`:

```python
    approval_state: ApprovalState = ApprovalState.DRAFT
    locked: bool = False
    approved_by_sub: str | None = None
    approved_at: datetime | None = None
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_approval.py tests/unit -x -q` — Expected: PASS. Run: `cd backend && uv run mypy src` — Expected: `Success`.

- [ ] **Step 6: Commit**

```bash
git add backend/src/ea/domain backend/src/ea/services/caller.py backend/tests/conftest.py backend/tests/unit/test_approval.py
git commit -m "feat(approval): pure review domain and the ea-approver role" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 25: Colonnes d'approbation, migration 0011, correspondance du dépôt

**Files:**
- Create: `backend/migrations/versions/0011_element_approval.py`, `backend/tests/integration/test_element_approval_schema.py`
- Modify: `backend/src/ea/db/models/architecture.py` (imports lignes 17-26; `__table_args__` lignes 40-58; colonnes après `updated_at`, ligne 78), `backend/src/ea/repositories/architecture_store.py` (`element_row`, `element_from_row`, lignes 63-100), `backend/src/ea/domain/audit.py` (`element_view`), `backend/tests/unit/test_audit_domain.py` (assertions)

**Interfaces:**
- Produces: `ElementRecord.approval_state|locked|approved_by_sub|approved_at`; `element_row`/`element_from_row` les portent; `element_view` gagne `approval_state`, `locked`, `approved_by_sub`, `approved_at` (iso ou `None`).

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `tests/unit/test_audit_domain.py`, compléter `test_an_element_view_is_the_read_representation_and_json_safe`:

```python
        assert view["approval_state"] == "draft"
        assert view["locked"] is False
        assert view["approved_by_sub"] is None and view["approved_at"] is None
```

```python
# backend/tests/integration/test_element_approval_schema.py
"""The approval columns: born `draft` on a populated database, and constrained."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.model import Element
from ea.repositories.architecture_store import PostgresArchitectureRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


async def test_what_already_existed_becomes_a_draft_nobody_approved(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    existing = uuid4()
    await asyncio.to_thread(command.upgrade, alembic_config, "0010")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, "
                    "updated_at) VALUES (:id, 'node', 'technology', 'active_structure', 'old', "
                    ":at, :at)"
                ),
                {"id": existing, "at": AT},
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "0011")

        async with postgres_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT approval_state, locked, approved_by_sub, approved_at "
                        "FROM elements WHERE id = :id"
                    ),
                    {"id": existing},
                )
            ).one()
        assert tuple(row) == ("draft", False, None, None)

        await asyncio.to_thread(command.downgrade, alembic_config, "0010")
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


async def test_a_lock_without_an_approval_and_an_unknown_state_are_refused(
    engine_at_head: AsyncEngine,
) -> None:
    graph = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    element = await graph.add_element(Element.create(element_type=E.NODE, name="a", now=AT))

    for statement in (
        "UPDATE elements SET locked = true WHERE id = :id",
        "UPDATE elements SET approval_state = 'weird' WHERE id = :id",
    ):
        with pytest.raises(IntegrityError):
            async with engine_at_head.begin() as connection:
                # Static statements; the id is bound.
                await connection.execute(text(statement), {"id": element.id})


async def test_the_review_fields_survive_a_save_and_a_read(engine_at_head: AsyncEngine) -> None:
    graph = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    element = await graph.add_element(Element.create(element_type=E.NODE, name="a", now=AT))
    approved = replace(
        element,
        approval_state=ApprovalState.APPROVED,
        locked=True,
        approved_by_sub="approver-1",
        approved_at=AT,
    )

    await graph.save_element(approved)

    assert await graph.get_element(element.id) == approved
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_audit_domain.py -x` — Expected: FAIL — `KeyError: 'approval_state'`.

- [ ] **Step 3: Modèle**

`db/models/architecture.py`: importer `CheckConstraint`; ajouter à `__table_args__` (avant les deux `Index`):

```python
        CheckConstraint(
            "approval_state IN ('draft', 'in_review', 'approved')", name="approval_state_known"
        ),
        # An element can only be locked once approved (docs/adr/CCCC).
        CheckConstraint("NOT locked OR approval_state = 'approved'", name="locked_implies_approved"),
```

et, après `updated_at`:

```python
    # Review state lives beside `properties`, not in it: the (type, name) and IPAM
    # indexes are untouched, and a `PATCH` that replaces `properties` cannot erase it.
    approval_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    approved_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

- [ ] **Step 4: Migration**

```python
# backend/migrations/versions/0011_element_approval.py
"""l'état d'approbation et le verrou d'un élément

Revision ID: 0011
Revises: 0010
Date: 2026-10-10

Quatre colonnes sur `elements`, hors de `properties` : ni l'unicité (type, nom)
ni les index IPAM ne sont touchés, et un `PATCH` qui remplace `properties` ne
peut pas effacer l'état. Les éléments existants deviennent des brouillons non
verrouillés — aucune approbation rétroactive n'est inventée (docs/adr/CCCC).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "elements",
        sa.Column("approval_state", sa.Text(), server_default="draft", nullable=False),
    )
    op.add_column(
        "elements",
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("elements", sa.Column("approved_by_sub", sa.Text(), nullable=True))
    op.add_column(
        "elements", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_check_constraint(
        op.f("ck_elements_approval_state_known"),
        "elements",
        "approval_state IN ('draft', 'in_review', 'approved')",
    )
    op.create_check_constraint(
        op.f("ck_elements_locked_implies_approved"),
        "elements",
        "NOT locked OR approval_state = 'approved'",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_elements_locked_implies_approved"), "elements", type_="check")
    op.drop_constraint(op.f("ck_elements_approval_state_known"), "elements", type_="check")
    op.drop_column("elements", "approved_at")
    op.drop_column("elements", "approved_by_sub")
    op.drop_column("elements", "locked")
    op.drop_column("elements", "approval_state")
```

- [ ] **Step 5: Correspondance et vue d'audit**

`architecture_store.py` — `element_row`: ajouter les clés

```python
        "approval_state": element.approval_state.value,
        "locked": element.locked,
        "approved_by_sub": element.approved_by_sub,
        "approved_at": element.approved_at,
```

`element_from_row` (import `from ea.domain.approval import ApprovalState`):

```python
def element_from_row(row: ElementRecord) -> Element:
    return Element(
        id=row.id,
        element_type=ElementType(row.element_type),
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        description=row.description,
        documentation=row.documentation,
        properties=dict(row.properties),
        approval_state=ApprovalState(row.approval_state),
        locked=row.locked,
        approved_by_sub=row.approved_by_sub,
        approved_at=row.approved_at,
    )
```

`domain/audit.py` — `element_view`: ajouter

```python
        "approval_state": element.approval_state.value,
        "locked": element.locked,
        "approved_by_sub": element.approved_by_sub,
        "approved_at": element.approved_at.isoformat() if element.approved_at else None,
```

- [ ] **Step 6: Relancer**

Run: `cd backend && uv run pytest tests/unit -x -q` — Expected: PASS. Run: `make test-postgres` — Expected: PASS (0011 appliquée sur base peuplée, CHECK, aller-retour). Run: `cd backend && uv run mypy src migrations` — Expected: `Success`.

- [ ] **Step 7: Commit**

```bash
git add backend/src backend/migrations/versions/0011_element_approval.py backend/tests
git commit -m "feat(approval): approval columns on elements, constrained (migration 0011)" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 26: Règles de verrouillage dans `update`, `delete`, `connect`, `disconnect`

**Files:**
- Modify: `backend/src/ea/domain/ports.py` (`add_relationship`, `delete_relationship`), `backend/src/ea/repositories/architecture_store.py` (méthodes de liens), `backend/tests/conftest.py` (`InMemoryRepository`), `backend/src/ea/services/architecture.py` (`update_element`, `delete_element`, `connect`, `disconnect`; imports)
- Test: `backend/tests/unit/test_element_lock.py`, `backend/tests/integration/test_element_approval_schema.py` (compléter)

**Interfaces:**
- Consumes: Task 24 (`check_modifiable`, `reviewed_after_change`), Task 25.
- Produces: `ArchitectureRepository.add_relationship(relationship, *, audit=(), touched: Sequence[Element] = ())` et `delete_relationship(relationship_id, *, audit=(), touched=())` — les `touched` (éléments dont l'état d'approbation change avec le lien) sont enregistrés **dans la même `begin()`** (colonnes `approval_state`, `approved_by_sub`, `approved_at` seulement). Comportement: voir les tests.

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# backend/tests/unit/test_element_lock.py
"""What approval and locking forbid, and how a change sends an element back to review."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction
from ea.domain.errors import ElementLockedError
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import FIXED_NOW, InMemoryAuditLog, InMemoryRepository, an_approver

pytestmark = pytest.mark.asyncio


def put_in_state(
    repository: InMemoryRepository, element_id: UUID, state: S, *, locked: bool = False
) -> None:
    """Set the review state directly: the transitions themselves are tested elsewhere."""
    approved = state is S.APPROVED
    repository.elements[element_id] = replace(
        repository.elements[element_id],
        approval_state=state,
        locked=locked,
        approved_by_sub="approver-1" if approved else None,
        approved_at=FIXED_NOW if approved else None,
    )


class TestUpdating:
    async def test_an_editor_cannot_change_an_approved_element(
        self, service: ArchitectureService, repository: InMemoryRepository, audit_log: InMemoryAuditLog
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED)
        audit_log.events.clear()

        with pytest.raises(ElementLockedError):
            await service.update_element(element.id, name="db-02")

        assert audit_log.events == []
        assert repository.elements[element.id].name == "db-01"

    async def test_an_approver_may_and_the_element_goes_back_to_review(
        self, service: ArchitectureService, repository: InMemoryRepository, audit_log: InMemoryAuditLog
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED)
        audit_log.events.clear()

        with acting_as(an_approver()):
            updated = await service.update_element(element.id, description="moved")

        assert updated.approval_state is S.IN_REVIEW
        assert updated.approved_by_sub is None
        (event,) = audit_log.events
        assert "approval_state" in {c.field for c in event.changes}

    async def test_a_locked_element_refuses_even_the_approver(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.APPROVED, locked=True)

        with acting_as(an_approver()), pytest.raises(ElementLockedError, match="locked"):
            await service.update_element(element.id, name="x")

    async def test_an_element_in_review_stays_editable_and_in_review(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="db-01")
        put_in_state(repository, element.id, S.IN_REVIEW)

        updated = await service.update_element(element.id, name="db-02")

        assert updated.approval_state is S.IN_REVIEW


class TestDeleting:
    async def test_approved_and_locked_elements_resist(
        self, service: ArchitectureService, repository: InMemoryRepository, audit_log: InMemoryAuditLog
    ) -> None:
        approved = await service.create_element(element_type=E.NODE, name="a")
        locked = await service.create_element(element_type=E.NODE, name="b")
        put_in_state(repository, approved.id, S.APPROVED)
        put_in_state(repository, locked.id, S.APPROVED, locked=True)
        audit_log.events.clear()

        with pytest.raises(ElementLockedError):
            await service.delete_element(approved.id)
        with acting_as(an_approver()), pytest.raises(ElementLockedError):
            await service.delete_element(locked.id)
        with acting_as(an_approver()):
            await service.delete_element(approved.id)

        assert [e.action for e in audit_log.events] == [AuditAction.ELEMENT_DELETED]


class TestLinking:
    async def test_linking_an_approved_element_is_allowed_and_sends_it_back_to_review(
        self, service: ArchitectureService, repository: InMemoryRepository, audit_log: InMemoryAuditLog
    ) -> None:
        approved = await service.create_element(element_type=E.NODE, name="a")
        other = await service.create_element(element_type=E.NODE, name="b")
        put_in_state(repository, approved.id, S.APPROVED)
        audit_log.events.clear()

        await service.connect(
            relationship_type=R.ASSOCIATION, source_id=approved.id, target_id=other.id
        )

        assert repository.elements[approved.id].approval_state is S.IN_REVIEW
        assert [e.action for e in audit_log.events] == [
            AuditAction.RELATIONSHIP_CREATED,
            AuditAction.ELEMENT_UPDATED,
        ]

    async def test_linking_or_unlinking_a_locked_element_is_refused_without_a_trace(
        self, service: ArchitectureService, repository: InMemoryRepository, audit_log: InMemoryAuditLog
    ) -> None:
        locked = await service.create_element(element_type=E.NODE, name="a")
        other = await service.create_element(element_type=E.NODE, name="b")
        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=locked.id, target_id=other.id
        )
        put_in_state(repository, locked.id, S.APPROVED, locked=True)
        audit_log.events.clear()

        with pytest.raises(ElementLockedError):
            await service.connect(
                relationship_type=R.ASSOCIATION, source_id=other.id, target_id=locked.id
            )
        with pytest.raises(ElementLockedError):
            await service.disconnect(link.id)

        assert audit_log.events == []
        assert link.id in repository.relationships

    async def test_unlinking_an_approved_element_also_sends_it_back_to_review(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        approved = await service.create_element(element_type=E.NODE, name="a")
        other = await service.create_element(element_type=E.NODE, name="b")
        link = await service.connect(
            relationship_type=R.ASSOCIATION, source_id=approved.id, target_id=other.id
        )
        put_in_state(repository, approved.id, S.APPROVED)

        await service.disconnect(link.id)

        assert repository.elements[approved.id].approval_state is S.IN_REVIEW
```

Compléter `tests/integration/test_element_approval_schema.py`:

```python
async def test_a_link_and_the_review_it_causes_commit_or_fail_together(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.archimate import RelationshipType as R
    from ea.domain.errors import ElementNotFoundError
    from ea.domain.model import Relationship

    graph = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    approved = await graph.add_element(Element.create(element_type=E.NODE, name="a", now=AT))
    other = await graph.add_element(Element.create(element_type=E.NODE, name="b", now=AT))
    approved = await graph.save_element(
        replace(approved, approval_state=ApprovalState.APPROVED, approved_by_sub="p", approved_at=AT)
    )
    in_review = replace(
        approved, approval_state=ApprovalState.IN_REVIEW, approved_by_sub=None, approved_at=None
    )

    ghost = Relationship.between(R.ASSOCIATION, approved, Element.create(element_type=E.NODE, name="x", now=AT), now=AT)
    with pytest.raises(ElementNotFoundError):
        await graph.add_relationship(ghost, touched=(in_review,))
    assert (await graph.get_element(approved.id)).approval_state is ApprovalState.APPROVED  # type: ignore[union-attr]

    link = Relationship.between(R.ASSOCIATION, approved, other, now=AT)
    await graph.add_relationship(link, touched=(in_review,))
    assert (await graph.get_element(approved.id)).approval_state is ApprovalState.IN_REVIEW  # type: ignore[union-attr]

    await graph.save_element(replace(in_review, approval_state=ApprovalState.APPROVED, approved_by_sub="p", approved_at=AT))
    assert await graph.delete_relationship(link.id, touched=(in_review,))
    assert (await graph.get_element(approved.id)).approval_state is ApprovalState.IN_REVIEW  # type: ignore[union-attr]
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_element_lock.py -x`
Expected: FAIL — pas d'`ElementLockedError` (le service ne contrôle rien encore).

- [ ] **Step 3: Port, dépôt PostgreSQL, double**

`domain/ports.py`:

```python
    async def add_relationship(
        self,
        relationship: Relationship,
        *,
        audit: Sequence[AuditEvent] = (),
        touched: Sequence[Element] = (),
    ) -> Relationship:
        """`touched` are elements whose review state changes with the link; they are
        saved in the same transaction."""
        ...

    async def delete_relationship(
        self,
        relationship_id: UUID,
        *,
        audit: Sequence[AuditEvent] = (),
        touched: Sequence[Element] = (),
    ) -> bool: ...
```

`architecture_store.py` — ajouter au-dessus de la classe (et les imports `ElementNotFoundError` déjà présents):

```python
async def _put_back_in_review(session: AsyncSession, touched: Sequence[Element]) -> None:
    """Save the review fields of the elements a link change touched, in the caller's transaction.

    Only the review columns: the link change must not overwrite a name or a
    property another request has just written.
    """
    for element in touched:
        row = await session.get(ElementRecord, element.id)
        if row is None:
            msg = f"no element with id {element.id}"
            raise ElementNotFoundError(msg)
        row.approval_state = element.approval_state.value
        row.approved_by_sub = element.approved_by_sub
        row.approved_at = element.approved_at
    await session.flush()
```

Les deux méthodes de liens, avec `touched` écrit dans la même transaction:

```python
    async def add_relationship(
        self,
        relationship: Relationship,
        *,
        audit: Sequence[AuditEvent] = (),
        touched: Sequence[Element] = (),
    ) -> Relationship:
        async with self._sessions.begin() as session:
            session.add(RelationshipRecord(**relationship_row(relationship)))
            try:
                await session.flush()
            except IntegrityError as error:
                if not (constraint_of(error) or "").startswith(_MISSING_END_PREFIX):
                    raise
                msg = (
                    f"cannot link {relationship.source_id} to {relationship.target_id}: "
                    "one of them does not exist"
                )
                raise ElementNotFoundError(msg) from error
            await _put_back_in_review(session, touched)
            write_events(session, audit)
        return relationship

    async def delete_relationship(
        self,
        relationship_id: UUID,
        *,
        audit: Sequence[AuditEvent] = (),
        touched: Sequence[Element] = (),
    ) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(RelationshipRecord).where(RelationshipRecord.id == relationship_id)
            )
            if not _rows_affected(deleted):
                return False
            await _put_back_in_review(session, touched)
            write_events(session, audit)
        return True
```

Double `InMemoryRepository`:

```python
    async def add_relationship(
        self,
        relationship: Relationship,
        *,
        audit: Sequence[AuditEvent] = (),
        touched: Sequence[Element] = (),
    ) -> Relationship:
        self.relationships[relationship.id] = relationship
        for element in touched:
            self.elements[element.id] = element
        self.audit_log.append(audit)
        return relationship

    async def delete_relationship(
        self,
        relationship_id: UUID,
        *,
        audit: Sequence[AuditEvent] = (),
        touched: Sequence[Element] = (),
    ) -> bool:
        if self.relationships.pop(relationship_id, None) is None:
            return False
        for element in touched:
            self.elements[element.id] = element
        self.audit_log.append(audit)
        return True
```

- [ ] **Step 4: Le service**

`services/architecture.py`: imports `from dataclasses import replace`, `from ea.domain.approval import check_modifiable, reviewed_after_change`, `from ea.domain.errors import CyclicContainmentError, ElementLockedError, ElementNotFoundError`, `from ea.domain.audit import AuditEvent` (TYPE_CHECKING non nécessaire: annotation seulement → mettre sous `TYPE_CHECKING`). Ajouter dans la classe:

```python
    def _put_back_in_review(
        self, *ends: Element
    ) -> tuple[list[Element], list[AuditEvent]]:
        """The ends a link change sends back to review, and the events that say so.

        A locked end refuses the change outright; an approved one is allowed and
        goes back to `in_review` — the element is saved in the same transaction
        as the link, never approved in silence.
        """
        changed: dict[UUID, Element] = {}
        events: list[AuditEvent] = []
        for end in ends:
            if end.locked:
                msg = f"{end.name!r} is locked: its relationships cannot change"
                raise ElementLockedError(msg)
            back = reviewed_after_change(end)
            if back is not end and end.id not in changed:
                changed[end.id] = back
                events.append(
                    self._trail.event(
                        AuditAction.ELEMENT_UPDATED,
                        entity_id=end.id,
                        before=element_view(end),
                        after=element_view(back),
                    )
                )
        return list(changed.values()), events
```

`update_element` (remplacer le début et la construction explicite):

```python
        caller = require_editor()
        current = await self.get_element(element_id)
        check_modifiable(current, caller)
        properties = validate_ipam_properties(current.element_type, properties)
        now = self._now()
        updated = current
        if name is not None:
            updated = updated.rename(name, now=now)
        if properties is not None:
            updated = updated.with_properties(properties, now=now)
        if description is not None or documentation is not None:
            updated = replace(
                updated,
                updated_at=now,
                description=(
                    description.strip() if description is not None else updated.description
                ),
                documentation=(
                    documentation.strip() if documentation is not None else updated.documentation
                ),
            )
        updated = reviewed_after_change(updated)
```

(le reste — l'événement, `save_element(..., audit=(event,))`, le log — inchangé.)

`delete_element`: première instruction `caller = require_editor()`; après `current = await self.get_element(element_id)` ajouter `check_modifiable(current, caller)`.

`connect`: avant `relationship = Relationship.between(...)`, après la lecture des deux extrémités:

```python
        touched, review_events = self._put_back_in_review(source, target)
```
et remplacer l'appel `add_relationship` par

```python
        stored = await self._repository.add_relationship(
            relationship, audit=(event, *review_events), touched=touched
        )
```
(`connect` garde `require_editor()` en première instruction.) Si `source` et `target` sont le même élément, `_put_back_in_review` le traite une fois.

`disconnect`:

```python
        require_editor()
        current = await self.get_relationship(relationship_id)
        touched, review_events = self._put_back_in_review(
            await self.get_element(current.source_id), await self.get_element(current.target_id)
        )
        event = self._trail.event(
            AuditAction.RELATIONSHIP_DELETED,
            entity_id=relationship_id,
            before=relationship_view(current),
        )
        if not await self._repository.delete_relationship(
            relationship_id, audit=(event, *review_events), touched=touched
        ):
            msg = f"no relationship with id {relationship_id}"
            raise ElementNotFoundError(msg)
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit -x -q` — Expected: PASS (dont `test_element_lock.py`, `test_audit_writes.py`, `test_service_guards.py`). Run: `make test-postgres` — Expected: PASS. Run: `cd backend && uv run mypy src` — Expected: `Success`.

- [ ] **Step 6: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(approval): approved and locked elements resist change; links send them back to review" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 27: `transition_element`, séparation des tâches, réglage

**Files:**
- Modify: `backend/src/ea/domain/audit.py` (`AuditAction`), `backend/src/ea/domain/ports.py` (`AuditRepository.last_actor_of`), `backend/src/ea/repositories/audit_store.py` (`last_actor_of`), `backend/src/ea/services/architecture.py` (`__init__`, nouvelle méthode), `backend/src/ea/core/config.py` (après `auth_ca_cert`, ligne ~188), `backend/.env.example` (après la ligne 153), `backend/src/ea/main.py` (construction de `ArchitectureService`, ligne ~241), `backend/tests/conftest.py` (`InMemoryAuditLog.last_actor_of`)
- Test: `backend/tests/unit/test_element_approval.py`, `backend/tests/integration/test_audit_store.py` (compléter)

**Interfaces:**
- Produces:
  - `AuditAction.ELEMENT_SUBMITTED|APPROVED|REJECTED|REOPENED|LOCKED|UNLOCKED` = `element.submitted|approved|rejected|reopened|locked|unlocked`
  - `AuditRepository.last_actor_of(entity_id: UUID, actions: Sequence[AuditAction]) -> str | None` (le `actor_sub` du plus récent)
  - `ArchitectureService(repository, *, clock, audit_reader: AuditRepository | None = None, four_eyes: bool = True)`
  - `ArchitectureService.transition_element(element_id: UUID, transition: Transition) -> Element` — commence par `caller = require_caller()`, puis `require_approver()` pour les transitions de `APPROVER_TRANSITIONS` et `require_editor()` pour `submit`; un seul événement; `approve` refusé (`NotAuthorisedError`) à l'auteur du dernier `element.updated` quand `four_eyes`
  - `Settings.approval_four_eyes: bool = True` (`EA_APPROVAL_FOUR_EYES`)

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# backend/tests/unit/test_element_approval.py
"""Moving an element through review: roles, states, four eyes, one event each."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.approval import ApprovalState as S
from ea.domain.approval import Transition as T
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction
from ea.domain.errors import InvalidTransitionError, NotAuthenticatedError, NotAuthorisedError
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import FIXED_NOW, InMemoryAuditLog, InMemoryRepository, a_reader, an_approver

pytestmark = pytest.mark.asyncio


def reviewing(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog, *, four_eyes: bool = True
) -> ArchitectureService:
    return ArchitectureService(
        repository, clock=lambda: FIXED_NOW, audit_reader=audit_log, four_eyes=four_eyes
    )


async def test_the_whole_life_of_an_element_leaves_one_event_per_step(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    service = reviewing(repository, audit_log)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    audit_log.events.clear()

    await service.transition_element(element.id, T.SUBMIT)  # the editor
    with acting_as(an_approver()):
        approved = await service.transition_element(element.id, T.APPROVE)
        await service.transition_element(element.id, T.LOCK)
        await service.transition_element(element.id, T.UNLOCK)
        reopened = await service.transition_element(element.id, T.REOPEN)

    assert approved.approved_by_sub == "approver-1"
    assert reopened.approval_state is S.DRAFT and reopened.approved_at is None
    assert [e.action for e in audit_log.events] == [
        AuditAction.ELEMENT_SUBMITTED,
        AuditAction.ELEMENT_APPROVED,
        AuditAction.ELEMENT_LOCKED,
        AuditAction.ELEMENT_UNLOCKED,
        AuditAction.ELEMENT_REOPENED,
    ]
    assert audit_log.events[1].before is not None and audit_log.events[1].after is not None
    assert audit_log.events[1].after["approval_state"] == "approved"


async def test_an_editor_cannot_approve_and_a_reader_cannot_even_submit(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    service = reviewing(repository, audit_log)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    await service.transition_element(element.id, T.SUBMIT)
    audit_log.events.clear()

    with pytest.raises(NotAuthorisedError):
        await service.transition_element(element.id, T.APPROVE)
    with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
        await service.transition_element(element.id, T.SUBMIT)

    assert audit_log.events == []


async def test_a_transition_the_state_forbids_is_refused_without_an_event(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    service = reviewing(repository, audit_log)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    audit_log.events.clear()

    with acting_as(an_approver()):
        with pytest.raises(InvalidTransitionError):
            await service.transition_element(element.id, T.APPROVE)  # still a draft
        with pytest.raises(InvalidTransitionError):
            await service.transition_element(element.id, T.LOCK)
    await service.transition_element(element.id, T.SUBMIT)
    with pytest.raises(InvalidTransitionError):
        await service.transition_element(element.id, T.SUBMIT)  # already submitted

    assert [e.action for e in audit_log.events] == [AuditAction.ELEMENT_SUBMITTED]


async def test_the_one_who_changed_an_element_last_cannot_approve_it(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    service = reviewing(repository, audit_log)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    with acting_as(an_approver()):  # approver-1 makes the last change
        await service.update_element(element.id, description="tuned")
        await service.transition_element(element.id, T.SUBMIT)
        with pytest.raises(NotAuthorisedError, match="separation"):
            await service.transition_element(element.id, T.APPROVE)
    with acting_as(an_approver(subject="approver-2", username="other")):
        approved = await service.transition_element(element.id, T.APPROVE)

    assert approved.approved_by_sub == "approver-2"


async def test_four_eyes_can_be_switched_off_for_a_one_person_repository(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    service = reviewing(repository, audit_log, four_eyes=False)
    element = await service.create_element(element_type=E.NODE, name="db-01")
    with acting_as(an_approver()):
        await service.update_element(element.id, description="tuned")
        await service.transition_element(element.id, T.SUBMIT)

        approved = await service.transition_element(element.id, T.APPROVE)

    assert approved.approval_state is S.APPROVED


@pytest.mark.usefixtures("nobody_calling")
async def test_nobody_calling_is_refused(
    repository: InMemoryRepository, audit_log: InMemoryAuditLog
) -> None:
    with pytest.raises(NotAuthenticatedError):
        await reviewing(repository, audit_log).transition_element(uuid4(), T.SUBMIT)
```

Ajouter à `tests/integration/test_audit_store.py`:

```python
async def test_the_last_actor_of_an_entity_is_the_newest_event_of_the_asked_actions(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresAuditRepository(sessions)
    subject = uuid4()
    async with sessions.begin() as session:
        write_events(
            session,
            [
                an_event(entity_id=subject, actor_sub="first", action=AuditAction.ELEMENT_UPDATED),
                an_event(
                    entity_id=subject,
                    actor_sub="second",
                    action=AuditAction.ELEMENT_UPDATED,
                    at=T0 + timedelta(minutes=1),
                ),
                an_event(
                    entity_id=subject,
                    actor_sub="reviewer",
                    action=AuditAction.ELEMENT_CREATED,
                    at=T0 + timedelta(minutes=2),
                ),
            ],
        )

    assert await store.last_actor_of(subject, [AuditAction.ELEMENT_UPDATED]) == "second"
    assert await store.last_actor_of(uuid4(), [AuditAction.ELEMENT_UPDATED]) is None
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_element_approval.py -x`
Expected: FAIL — `TypeError: ArchitectureService.__init__() got an unexpected keyword argument 'audit_reader'`.

- [ ] **Step 3: Implémenter**

`domain/audit.py` — `AuditAction`, après `ELEMENT_DELETED`:

```python
    ELEMENT_SUBMITTED = "element.submitted"
    ELEMENT_APPROVED = "element.approved"
    ELEMENT_REJECTED = "element.rejected"
    ELEMENT_REOPENED = "element.reopened"
    ELEMENT_LOCKED = "element.locked"
    ELEMENT_UNLOCKED = "element.unlocked"
```

`ports.py` — dans `AuditRepository`:

```python
    async def last_actor_of(self, entity_id: UUID, actions: Sequence[AuditAction]) -> str | None:
        """The `actor_sub` of the newest event of these actions about this entity, if any."""
        ...
```
(import `AuditAction`).

`audit_store.py` — dans `PostgresAuditRepository`:

```python
    async def last_actor_of(self, entity_id: UUID, actions: Sequence[AuditAction]) -> str | None:
        statement = (
            select(AuditEventRecord.actor_sub)
            .where(
                AuditEventRecord.entity_id == entity_id,
                AuditEventRecord.action.in_([action.value for action in actions]),
            )
            .order_by(AuditEventRecord.at.desc(), AuditEventRecord.id.desc())
            .limit(1)
        )
        async with self._sessions() as session:
            return (await session.scalars(statement)).first()
```
(import `UUID` sous TYPE_CHECKING.)

`tests/conftest.py` — dans `InMemoryAuditLog`:

```python
    async def last_actor_of(self, entity_id: UUID, actions: Sequence[AuditAction]) -> str | None:
        found = [e for e in self.events if e.entity_id == entity_id and e.action in actions]
        return found[-1].actor_sub if found else None
```
(import `AuditAction`; l'ordre d'ajout fait foi: avec une horloge figée, deux événements ont le même `at`.)

`core/config.py`, après `auth_ca_cert`:

```python
    #: An approver cannot approve what they changed last (separation of duties,
    #: docs/adr/CCCC). Off for a repository with a single user.
    approval_four_eyes: bool = True
```
`backend/.env.example`, après `EA_AUTH_CA_CERT=`: `EA_APPROVAL_FOUR_EYES=true`.

`services/architecture.py`:

```python
    def __init__(
        self,
        repository: ArchitectureRepository,
        *,
        clock: Clock = _utc_now,
        audit_reader: AuditRepository | None = None,
        four_eyes: bool = True,
    ) -> None:
        self._repository = repository
        self._now = clock
        self._trail = AuditTrail(clock)
        self._audit_reader = audit_reader
        self._four_eyes = four_eyes
```
(import `APPROVER_TRANSITIONS, Transition, apply_transition`, `NotAuthorisedError`, `require_approver`; `AuditRepository` sous TYPE_CHECKING), constante de module

```python
_ACTION_OF = {
    Transition.SUBMIT: AuditAction.ELEMENT_SUBMITTED,
    Transition.APPROVE: AuditAction.ELEMENT_APPROVED,
    Transition.REJECT: AuditAction.ELEMENT_REJECTED,
    Transition.REOPEN: AuditAction.ELEMENT_REOPENED,
    Transition.LOCK: AuditAction.ELEMENT_LOCKED,
    Transition.UNLOCK: AuditAction.ELEMENT_UNLOCKED,
}
```
et la méthode (après `delete_element`):

```python
    async def transition_element(self, element_id: UUID, transition: Transition) -> Element:
        """Move an element through review: submit, approve, reject, reopen, lock, unlock.

        The role is checked by the guard here and again by the pure rule; the
        state by the rule. Approving is refused to whoever made the last change
        (`EA_APPROVAL_FOUR_EYES`). The element and its single event are written together.
        """
        caller = require_caller()
        if transition in APPROVER_TRANSITIONS:
            require_approver()
        else:
            require_editor()
        current = await self.get_element(element_id)
        updated = apply_transition(current, transition, caller, now=self._now())
        if transition is Transition.APPROVE and self._four_eyes and self._audit_reader is not None:
            last = await self._audit_reader.last_actor_of(element_id, (AuditAction.ELEMENT_UPDATED,))
            if last == caller.subject:
                msg = (
                    "separation of duties: you made the last change to this element, "
                    "someone else must approve it"
                )
                raise NotAuthorisedError(msg)
        event = self._trail.event(
            _ACTION_OF[transition],
            entity_id=element_id,
            before=element_view(current),
            after=element_view(updated),
        )
        stored = await self._repository.save_element(updated, audit=(event,))
        logger.info(
            "element %r: %s",
            stored.name,
            transition.value,
            extra={
                "action": f"element_{transition.value}",
                "element_id": str(element_id),
                "transition": transition.value,
            },
        )
        return stored
```

`main.py` (lifespan, là où `ArchitectureService(repository)` est construit). `audit_store` doit exister **avant** `if open_graph:` — la Task 8 le crée juste après `metadata_store`, c'est-à-dire avant; vérifier l'ordre:

```python
                if audit_store is None:
                    audit_store = PostgresAuditRepository(app.state.db_sessions)
                ...
                if open_graph:
                    repository = PostgresArchitectureRepository(app.state.db_sessions)
                    app.state.architecture_service = ArchitectureService(
                        repository,
                        audit_reader=audit_store,
                        four_eyes=settings.approval_four_eyes,
                    )
```

Dans `create_app`, le `ArchitectureService` injecté par un test reste tel quel (sans lecteur: la séparation des tâches y est inactive, ce que les tests unitaires couvrent).

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit -x -q` — Expected: PASS (dont `test_config.py::test_the_committed_env_example_builds_settings` et `test_service_guards.py`). Run: `make test-postgres` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests backend/.env.example
git commit -m "feat(approval): review transitions with separation of duties" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 28: API — `ElementRead`, `/me`, `POST /elements/{id}/approval`

**Files:**
- Create: `backend/tests/e2e/test_approval_api.py`
- Modify: `backend/src/ea/api/schemas.py` (`ElementRead` lignes 140-167; `MeRead` ligne 553; `ApprovalRequest`), `backend/src/ea/api/me.py`, `backend/src/ea/api/architecture.py` (fin de la section « Elements », après `delete_element`, ligne ~123), `backend/src/ea/api/errors.py`, `backend/openapi.json`, `frontend/src/api/schema.d.ts`, `frontend/src/features/history/auditLabels.ts`, `frontend/src/lib/me.ts`, `frontend/tests/setup.ts`, `frontend/tests/support/api.ts` (`anElement`)

**Interfaces:**
- Produces: `ElementRead` + `approval_state: ApprovalState`, `locked: bool`, `approved_by: str | None`, `approved_at: datetime | None`, `allowed_transitions: list[Transition]` (calculé depuis `current_caller` dans `ElementRead.of(element)`); `MeRead.can_approve`; `ApprovalRequest(transition: Transition)` (`extra=forbid`); `POST /elements/{element_id}/approval -> ElementRead`; `_STATUS[ElementLockedError] = (409, "element_locked")`, `_STATUS[InvalidTransitionError] = (409, "invalid_transition")`; `useMe().canApprove`.

- [ ] **Step 1: Écrire le test e2e qui échoue**

```python
# backend/tests/e2e/test_approval_api.py
"""Review over HTTP: 403 for the wrong role, 409 for the wrong state, and what the SPA is told."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import StaticVerifier, a_reader, an_approver, an_editor

pytestmark = pytest.mark.asyncio

EDITOR, APPROVER, READER = "editor-token", "approver-token", "reader-token"


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, nobody_calling: None
) -> AsyncIterator[httpx.AsyncClient]:
    verifier = StaticVerifier(
        {EDITOR: an_editor(), APPROVER: an_approver(), READER: a_reader()}
    )
    app = create_app(
        Settings(debug=True, auth_enabled=True), architecture_service=service, verifier=verifier
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def as_(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def make(client: httpx.AsyncClient) -> dict[str, Any]:
    response = await client.post(
        "/elements", json={"element_type": "node", "name": "db-01"}, headers=as_(EDITOR)
    )
    return dict(response.json())


async def move(client: httpx.AsyncClient, element: dict[str, Any], transition: str, token: str):  # type: ignore[no-untyped-def]
    return await client.post(
        f"/elements/{element['id']}/approval", json={"transition": transition}, headers=as_(token)
    )


async def test_an_element_is_born_a_draft_and_tells_each_caller_what_they_may_do(
    client: httpx.AsyncClient,
) -> None:
    element = await make(client)

    as_editor = (await client.get(f"/elements/{element['id']}", headers=as_(EDITOR))).json()
    as_reader = (await client.get(f"/elements/{element['id']}", headers=as_(READER))).json()

    assert (as_editor["approval_state"], as_editor["locked"]) == ("draft", False)
    assert as_editor["approved_by"] is None
    assert as_editor["allowed_transitions"] == ["submit"]
    assert as_reader["allowed_transitions"] == []


async def test_the_review_flow_over_http(client: httpx.AsyncClient) -> None:
    element = await make(client)

    submitted = await move(client, element, "submit", EDITOR)
    approved = await move(client, element, "approve", APPROVER)
    locked = await move(client, element, "lock", APPROVER)

    assert submitted.json()["approval_state"] == "in_review"
    assert submitted.json()["allowed_transitions"] == []  # an editor cannot approve
    assert approved.json()["approved_by"] == "approver-1"
    # `/approval` answers for the caller of that call: the approver, who may reopen or lock.
    assert approved.json()["allowed_transitions"] == ["reopen", "lock"]
    assert locked.json()["locked"] is True


async def test_403_for_the_wrong_role_409_for_the_wrong_state_401_without_a_token(
    client: httpx.AsyncClient,
) -> None:
    element = await make(client)
    await move(client, element, "submit", EDITOR)

    assert (await move(client, element, "approve", EDITOR)).status_code == 403
    assert (await move(client, element, "submit", READER)).status_code == 403
    wrong_state = await move(client, element, "lock", APPROVER)
    assert wrong_state.status_code == 409
    assert wrong_state.json()["error"] == "invalid_transition"
    anonymous = await client.post(
        f"/elements/{element['id']}/approval", json={"transition": "submit"}
    )
    assert anonymous.status_code == 401
    unknown = await move(client, element, "teleport", APPROVER)
    assert unknown.status_code == 422


async def test_an_approved_element_refuses_an_editor_with_a_409(
    client: httpx.AsyncClient,
) -> None:
    element = await make(client)
    await move(client, element, "submit", EDITOR)
    await move(client, element, "approve", APPROVER)

    refused = await client.patch(
        f"/elements/{element['id']}", json={"name": "db-02"}, headers=as_(EDITOR)
    )
    deleted = await client.delete(f"/elements/{element['id']}", headers=as_(EDITOR))
    allowed = await client.patch(
        f"/elements/{element['id']}", json={"name": "db-02"}, headers=as_(APPROVER)
    )

    assert refused.status_code == 409 and refused.json()["error"] == "element_locked"
    assert deleted.status_code == 409
    assert allowed.status_code == 200
    assert allowed.json()["approval_state"] == "in_review"


async def test_me_says_whether_the_caller_may_approve(client: httpx.AsyncClient) -> None:
    approver = (await client.get("/me", headers=as_(APPROVER))).json()
    editor = (await client.get("/me", headers=as_(EDITOR))).json()

    assert (approver["can_write"], approver["can_approve"]) == (True, True)
    assert (editor["can_write"], editor["can_approve"]) == (True, False)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/e2e/test_approval_api.py -x`
Expected: FAIL — 404 sur `/elements/{id}/approval`.

- [ ] **Step 3: Backend**

`api/errors.py`: importer `ElementLockedError`, `InvalidTransitionError`; `_STATUS`:

```python
    # --- Review and locking (docs/adr/CCCC) ---
    ElementLockedError: (status.HTTP_409_CONFLICT, "element_locked"),
    InvalidTransitionError: (status.HTTP_409_CONFLICT, "invalid_transition"),
```

`api/schemas.py`: imports `from ea.domain.approval import ApprovalState, Transition, allowed_transitions` et `from ea.services.caller import current_caller`; dans `ElementRead` ajouter les champs

```python
    approval_state: ApprovalState
    locked: bool
    approved_by: str | None = Field(description="The Keycloak subject that approved it.")
    approved_at: datetime | None
    allowed_transitions: list[Transition] = Field(
        description="What the caller may do next with this element: the SPA draws a button per entry."
    )
```
et, dans `ElementRead.of`, avant le `return`:

```python
        caller = current_caller.get()
        offered = (
            allowed_transitions(element.approval_state, element.locked, caller)
            if caller is not None
            else ()
        )
```
puis, dans le `cls(...)` retourné, les cinq nouveaux arguments:

```python
            approval_state=element.approval_state,
            locked=element.locked,
            approved_by=element.approved_by_sub,
            approved_at=element.approved_at,
            allowed_transitions=list(offered),
``` Après `ElementUpdate`:

```python
class ApprovalRequest(_Input):
    transition: Transition
```
`MeRead`: ajouter `can_approve: bool`; `api/me.py`: `MeRead(username=caller.username, can_write=caller.can_write, can_approve=caller.can_approve)`.

`api/architecture.py` (importer `ApprovalRequest`), après `delete_element`:

```python
@router.post(
    "/elements/{element_id}/approval",
    response_model=ElementRead,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def transition_element(
    element_id: UUID, payload: ApprovalRequest, service: Architecture
) -> ElementRead:
    """Move an element through review. 403 without the role, 409 if its state forbids it."""
    return ElementRead.of(await service.transition_element(element_id, payload.transition))
```

- [ ] **Step 4: Relancer, régénérer, adapter le SPA**

Run: `cd backend && uv run pytest tests/e2e tests/unit -x -q` — Expected: PASS, sauf les tests qui énumèrent les champs d'`ElementRead` ou de `MeRead` (`grep -rn "can_write" backend/tests` en liste trois: `tests/e2e/test_auth_api.py`, `tests/unit/test_auth_contract.py`, `tests/unit/test_mcp_auth.py`) — les adapter d'un champ de plus, rien d'autre — et `test_openapi` tant que le schéma n'est pas régénéré.
Run: `make openapi && make openapi-check`.

Frontend: `auditLabels.ts` — ajouter à `ACTION_LABELS`:

```ts
  'element.submitted': 'Élément soumis à revue',
  'element.approved': 'Élément approuvé',
  'element.rejected': 'Élément rejeté',
  'element.reopened': 'Élément rouvert',
  'element.locked': 'Élément verrouillé',
  'element.unlocked': 'Élément déverrouillé',
```
`lib/me.ts`: dans `useMe()`, ajouter `canApprove: computed(() => me.value?.can_approve === true),`. `tests/setup.ts`: dans le mock, `me` devient `ref<{ username: string; can_write: boolean; can_approve: boolean } | null>({ username: 'editor', can_write: true, can_approve: false })` et `canApprove: computed(() => false)`. `tests/support/api.ts` — `anElement`: ajouter par défaut `approval_state: 'draft', locked: false, approved_by: null, approved_at: null, allowed_transitions: []`.
Run: `cd frontend && npm run typecheck && npm test -- --run` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend frontend
git commit -m "feat(approval): approval endpoint, allowed transitions on elements and can_approve on /me" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 29: Outil MCP `transition_element`

**Files:**
- Modify: `backend/src/ea/mcp/server.py` (imports; `INSTRUCTIONS`; outil), `backend/tests/unit/test_mcp_server.py` (liste complète; classe de test)

**Interfaces:**
- Consumes: `ArchitectureService.transition_element`, `Transition`.
- Produces: outil `transition_element(element_id, transition) -> ElementRead` (annotation `ADDS`: écrit, non destructif, non idempotent); les refus du service (`ElementLockedError`, `NotAuthorisedError`, `InvalidTransitionError`) arrivent en `ToolError`.

- [ ] **Step 1: Écrire les tests qui échouent** (dans `tests/unit/test_mcp_server.py`; ajouter `"transition_element"` à la liste complète; import `from tests.conftest import an_approver`)

```python
@pytest.mark.asyncio
class TestTheReviewTool:
    async def test_the_roles_of_the_token_decide(self, server: MCPServer[Any]) -> None:
        element = await an_element(server, "node", "db-01")
        submitted = await call(
            server, "transition_element", element_id=element["id"], transition="submit"
        )

        refused = await server.call_tool(
            "transition_element", {"element_id": element["id"], "transition": "approve"}
        )
        with acting_as(an_approver()):
            approved = await call(
                server, "transition_element", element_id=element["id"], transition="approve"
            )

        assert submitted["approval_state"] == "in_review"
        assert refused.is_error and "ea-approver" in str(refused.content)
        assert approved["approval_state"] == "approved"

    async def test_an_approved_element_tells_the_agent_why_an_update_is_refused(
        self, server: MCPServer[Any]
    ) -> None:
        element = await an_element(server, "node", "db-01")
        await call(server, "transition_element", element_id=element["id"], transition="submit")
        with acting_as(an_approver()):
            await call(server, "transition_element", element_id=element["id"], transition="approve")

        refused = await server.call_tool(
            "update_element", {"element_id": element["id"], "name": "db-02"}
        )

        assert refused.is_error and "approved" in str(refused.content)
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_mcp_server.py -x`
Expected: FAIL — la liste complète diffère (`transition_element` absent).

- [ ] **Step 3: Implémenter**

`INSTRUCTIONS` — ajouter:

```
An element can be put through review. `transition_element` moves it:
`submit` (any editor), then `approve`, `reject`, `reopen`, `lock`, `unlock` (they
need the approver role of your token — a refusal says so). Once an element is
approved, `update_element` and `delete_element` are refused unless you are an
approver, and any change sends it back to review; a locked element refuses
everything until it is unlocked. Read `allowed_transitions` on the element: it
lists exactly what your token may do next.
```

Outil (après `delete_element`, dans la section des éléments; importer `Transition` depuis `ea.domain.approval`):

```python
    @server.tool(annotations=ADDS)
    @speaking_plainly
    async def transition_element(
        element_id: ElementId,
        transition: Annotated[
            Transition,
            Field(description="submit, approve, reject, reopen, lock or unlock."),
        ],
    ) -> ElementRead:
        """Move an element through review: draft → in review → approved → locked.

        `submit` needs the editor role; every other transition needs the approver
        role of the token you were given, and `approve` is refused to whoever made
        the last change to the element. The answer lists `allowed_transitions`:
        what you may do next.
        """
        return ElementRead.of(await get_service().transition_element(element_id, transition))
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit tests/e2e -x -q` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/mcp/server.py backend/tests/unit/test_mcp_server.py
git commit -m "feat(mcp): transition_element, review gated by the token's roles" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 30: La restauration apprend le verrou et l'approbation

**Files:**
- Modify: `backend/src/ea/domain/baselines.py` (`plan_restore`, construction de `updates`; imports), `backend/src/ea/domain/ports.py` (`BaselineRepository.locked_element_ids`), `backend/src/ea/repositories/baseline_store.py` (`locked_element_ids`; `apply_restore`, branche `elements_to_update`), `backend/src/ea/services/baselines.py` (`restore`), `backend/tests/conftest.py` (`InMemoryBaselines.locked_element_ids`)
- Test: `backend/tests/unit/test_restore_plan.py`, `backend/tests/unit/test_baseline_service.py`, `backend/tests/integration/test_baseline_restore.py` (compléter)

**Interfaces:**
- Produces: `BaselineRepository.locked_element_ids() -> frozenset[UUID]`; `plan_restore` : `ElementUpdate.restored` reprend les champs d'approbation de l'élément **courant** puis `reviewed_after_change` (un `approved` modifié par la restauration repasse `in_review`); `apply_restore` écrit ces colonnes; `BaselineService.restore` passe `locked=` (un plan qui touche un verrouillé: le `dry_run` le rapporte dans `blocked`, l'application lève `RestoreBlockedError`, rien n'est écrit).

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `tests/unit/test_restore_plan.py`:

```python
class TestReview:
    def test_an_approved_element_the_restoration_changes_goes_back_to_review(self) -> None:
        from ea.domain.approval import ApprovalState

        original = node("billing")
        approved = replace(
            original,
            name="invoicing",
            approval_state=ApprovalState.APPROVED,
            approved_by_sub="approver-1",
            approved_at=NOW,
        )

        plan = plan_restore(
            ModelSnapshot((original,), ()), ModelSnapshot((approved,), ()), keep_new=False
        )

        (update,) = plan.elements_to_update
        assert update.restored.name == "billing"
        assert update.restored.approval_state is ApprovalState.IN_REVIEW
        assert update.restored.approved_by_sub is None

    def test_a_recreated_element_is_a_draft(self) -> None:
        from ea.domain.approval import ApprovalState

        gone = node("gone")

        plan = plan_restore(ModelSnapshot((gone,), ()), ModelSnapshot((), ()), keep_new=False)

        assert plan.elements_to_create[0].approval_state is ApprovalState.DRAFT
```

Dans `tests/unit/test_baseline_service.py` (ajouter `RestoreBlockedError` aux imports d'erreurs, `replace`, `ApprovalState`, `FIXED_NOW`):

```python
class TestLockedElements:
    async def test_a_locked_element_blocks_the_application_but_not_the_dry_run(
        self,
        service: ArchitectureService,
        baseline_service: BaselineService,
        repository: InMemoryRepository,
        audit_log: InMemoryAuditLog,
    ) -> None:
        element = await service.create_element(element_type=E.NODE, name="billing")
        baseline = await baseline_service.create(name="v1")
        await service.update_element(element.id, name="invoicing")
        repository.elements[element.id] = replace(
            repository.elements[element.id],
            approval_state=ApprovalState.APPROVED,
            locked=True,
            approved_by_sub="approver-1",
            approved_at=FIXED_NOW,
        )
        audit_log.events.clear()

        planned = await baseline_service.restore(baseline.id)
        with pytest.raises(RestoreBlockedError, match="invoicing"):
            await baseline_service.restore(baseline.id, dry_run=False, confirm=True)

        assert [e.name for e in planned.plan.blocked] == ["invoicing"]
        assert repository.elements[element.id].name == "invoicing"
        assert audit_log.events == []
```

(imports: `from ea.domain.approval import ApprovalState`, `from dataclasses import replace`, `from tests.conftest import FIXED_NOW, InMemoryRepository`.)

Dans `tests/integration/test_baseline_restore.py`:

```python
async def test_restoring_over_an_approved_element_sends_it_back_to_review_in_the_database(
    engine_at_head: AsyncEngine,
) -> None:
    from ea.domain.approval import ApprovalState

    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    baselines = PostgresBaselineRepository(sessions)
    element = await graph.add_element(Element.create(element_type=E.NODE, name="billing", now=NOW))
    baseline = await baselines.create(
        Baseline.create(name="v1", description="", created_by_sub="s", created_by="n", now=NOW)
    )
    await graph.save_element(
        replace(
            element.rename("invoicing", now=LATER),
            approval_state=ApprovalState.APPROVED,
            approved_by_sub="approver-1",
            approved_at=LATER,
        )
    )

    assert await baselines.locked_element_ids() == frozenset()
    plan = plan_restore(
        await baselines.snapshot_of(baseline.id), await baselines.current_snapshot(), keep_new=False
    )
    await baselines.apply_restore(plan, now=LATER)

    stored = await graph.get_element(element.id)
    assert stored is not None
    assert (stored.name, stored.approval_state, stored.approved_by_sub) == (
        "billing",
        ApprovalState.IN_REVIEW,
        None,
    )
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_restore_plan.py tests/unit/test_baseline_service.py -x`
Expected: FAIL — `assert update.restored.approval_state is IN_REVIEW` (la restauration reprend l'état `draft` de la baseline).

- [ ] **Step 3: Implémenter**

`domain/baselines.py` — importer `from dataclasses import field, replace`, `from ea.domain.approval import reviewed_after_change`; dans la boucle de `updates`:

```python
        if changes:
            current_version = cur_el[identifier]
            restored = reviewed_after_change(
                replace(
                    snap_el[identifier],
                    approval_state=current_version.approval_state,
                    locked=current_version.locked,
                    approved_by_sub=current_version.approved_by_sub,
                    approved_at=current_version.approved_at,
                )
            )
            updates.append(ElementUpdate(current_version, restored, changes))
```
(remplace l'appel `updates.append(ElementUpdate(cur_el[identifier], snap_el[identifier], changes))`).

`ports.py` — `BaselineRepository`:

```python
    async def locked_element_ids(self) -> frozenset[UUID]:
        """The elements that are locked now: a restoration may not touch them."""
        ...
```

`baseline_store.py`:

```python
    async def locked_element_ids(self) -> frozenset[UUID]:
        async with self._sessions() as session:
            return frozenset(
                await session.scalars(select(ElementRecord.id).where(ElementRecord.locked.is_(True)))
            )
```
et dans `apply_restore`, branche `elements_to_update`, après `row.updated_at = now`:

```python
                    row.approval_state = update.restored.approval_state.value
                    row.approved_by_sub = update.restored.approved_by_sub
                    row.approved_at = update.restored.approved_at
```

`services/baselines.py` — dans `restore`, l'appel `plan_restore(...)` reçoit le verrou:

```python
        plan = plan_restore(
            snapshot,
            current,
            keep_new=bool(keep_new),
            locked=await self._repository.locked_element_ids(),
            diagrams_present=await self._repository.diagram_ids(),
        )
```

`tests/conftest.py` — `InMemoryBaselines` (l'état d'approbation d'une mise à jour vient déjà de `update.restored`, que `apply_restore` du double copie):

```python
    async def locked_element_ids(self) -> frozenset[UUID]:
        return frozenset(e.id for e in self.graph.elements.values() if e.locked)
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit -x -q` — Expected: PASS. Run: `make test-postgres` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(approval): restoration refuses locked elements and re-reviews approved ones" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 31: SPA — bandeau d'état sur la fiche élément

**Files:**
- Create: `frontend/src/features/elements/approvalLabels.ts`, `frontend/src/features/elements/useApproval.ts`, `frontend/src/features/elements/ApprovalBanner.vue`, `frontend/tests/useApproval.spec.ts`, `frontend/tests/ApprovalBanner.spec.ts`
- Modify: `frontend/src/features/elements/ElementDetail.vue` (script lignes 9-20; template, juste après `</header>` ligne ~28)

**Interfaces:**
- Consumes: `ElementRead` généré (avec `approval_state`, `locked`, `approved_by`, `allowed_transitions`), `api.POST('/elements/{element_id}/approval')`.
- Produces: `STATE_LABELS: Record<ApprovalState, string>`, `TRANSITION_LABELS: Record<Transition, string>`; `useApproval().transition(id, t) -> Promise<ElementRead>`; `<ApprovalBanner :element @changed />` (un bouton par entrée de `element.allowed_transitions`, aucune règle d'approbation en TypeScript); `ElementDetail` garde une copie locale de l'élément affiché pour refléter la transition.

- [ ] **Step 1: Écrire les tests qui échouent**

```ts
// frontend/tests/useApproval.spec.ts
import { afterEach, describe, expect, it, vi } from 'vitest'

import { useApproval } from '../src/features/elements/useApproval'
import { anElement, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useApproval', () => {
  it('posts the transition under the name the backend reads and returns the element', async () => {
    const element = anElement({ approval_state: 'in_review', allowed_transitions: [] })
    const calls = stubApi([
      { method: 'POST', path: `/elements/${element.id}/approval`, body: element },
    ])

    const answer = await useApproval().transition(element.id, 'submit')

    expect(calls[0]?.body).toEqual({ transition: 'submit' })
    expect(answer.approval_state).toBe('in_review')
  })
})
```

```ts
// frontend/tests/ApprovalBanner.spec.ts
import { fireEvent, render, screen, waitFor } from '@testing-library/vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import ApprovalBanner from '../src/features/elements/ApprovalBanner.vue'
import { anElement, stubApi } from './support/api'

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('ApprovalBanner', () => {
  it('names the state and offers exactly the transitions the API listed', () => {
    render(ApprovalBanner, {
      props: {
        element: anElement({
          approval_state: 'in_review',
          allowed_transitions: ['approve', 'reject'],
        }),
      },
    })

    expect(screen.getByText('En revue')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Approuver' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Rejeter' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Verrouiller' })).toBeNull()
  })

  it('shows no button at all when the caller may do nothing', () => {
    render(ApprovalBanner, { props: { element: anElement({ allowed_transitions: [] }) } })

    expect(screen.queryAllByRole('button')).toHaveLength(0)
  })

  it('shows a locked element as locked, and who approved it', () => {
    render(ApprovalBanner, {
      props: {
        element: anElement({
          approval_state: 'approved',
          locked: true,
          approved_by: 'approver-1',
          allowed_transitions: ['unlock'],
        }),
      },
    })

    expect(screen.getByText('Verrouillé')).toBeTruthy()
    expect(screen.getByText(/approver-1/)).toBeTruthy()
  })

  it('posts the transition and announces the element it comes back with', async () => {
    const before = anElement({ allowed_transitions: ['submit'] })
    const after = anElement({ approval_state: 'in_review', allowed_transitions: [] })
    stubApi([{ method: 'POST', path: `/elements/${before.id}/approval`, body: after }])
    const { emitted } = render(ApprovalBanner, { props: { element: before } })

    await fireEvent.click(screen.getByRole('button', { name: 'Soumettre à revue' }))

    await waitFor(() => expect(emitted().changed).toBeTruthy())
    expect(emitted().changed?.[0]).toEqual([after])
  })

  it('shows the API refusal instead of swallowing it', async () => {
    const before = anElement({ approval_state: 'in_review', allowed_transitions: ['approve'] })
    stubApi([
      {
        method: 'POST',
        path: `/elements/${before.id}/approval`,
        status: 403,
        body: { error: 'forbidden', detail: 'separation of duties' },
      },
    ])
    render(ApprovalBanner, { props: { element: before } })

    await fireEvent.click(screen.getByRole('button', { name: 'Approuver' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(/separation of duties/)
  })
})
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd frontend && npm test -- --run tests/useApproval.spec.ts tests/ApprovalBanner.spec.ts`
Expected: FAIL — « Failed to resolve import "../src/features/elements/useApproval" ».

- [ ] **Step 3: Implémenter**

```ts
// frontend/src/features/elements/approvalLabels.ts
// Translation tables, and not one rule: which transitions exist and who may take
// them is the API's (`allowed_transitions`). A new state or transition on the
// backend is a TypeScript error here, since both records are exhaustive.
import type { components } from '../../api/schema'

export type ApprovalState = components['schemas']['ApprovalState']
export type Transition = components['schemas']['Transition']

export const STATE_LABELS: Record<ApprovalState, string> = {
  draft: 'Brouillon',
  in_review: 'En revue',
  approved: 'Approuvé',
}

export const TRANSITION_LABELS: Record<Transition, string> = {
  submit: 'Soumettre à revue',
  approve: 'Approuver',
  reject: 'Rejeter',
  reopen: 'Rouvrir',
  lock: 'Verrouiller',
  unlock: 'Déverrouiller',
}
```

```ts
// frontend/src/features/elements/useApproval.ts
// One write: move an element through review. Its refusal (403, 409) is the
// banner's to show, so it is thrown rather than kept in a state.
import { api, unwrap } from '../../lib/api'
import type { ElementRead } from './useElementCatalogue'
import type { Transition } from './approvalLabels'

export function useApproval() {
  async function transition(id: string, next: Transition): Promise<ElementRead> {
    return unwrap(
      await api.POST('/elements/{element_id}/approval', {
        params: { path: { element_id: id } },
        body: { transition: next },
      }),
    )
  }
  return { transition }
}
```

```vue
<!-- frontend/src/features/elements/ApprovalBanner.vue -->
<script setup lang="ts">
// L'état d'approbation d'un élément, et les seuls boutons que l'API offre à cet
// appelant (`allowed_transitions`). Le SPA masque ; l'API décide.
import { computed, ref } from 'vue'

import { messageOf } from '../../lib/api'
import { STATE_LABELS, TRANSITION_LABELS, type Transition } from './approvalLabels'
import { useApproval } from './useApproval'
import type { ElementRead } from './useElementCatalogue'

const props = defineProps<{ element: ElementRead }>()
const emit = defineEmits<{ changed: [element: ElementRead] }>()

const approval = useApproval()
const failure = ref('')
const busy = ref(false)

const label = computed(() =>
  props.element.locked ? 'Verrouillé' : STATE_LABELS[props.element.approval_state],
)

async function take(next: Transition): Promise<void> {
  busy.value = true
  failure.value = ''
  try {
    emit('changed', await approval.transition(props.element.id, next))
  } catch (caught) {
    failure.value = messageOf(caught)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <section
    class="banner"
    :data-state="element.locked ? 'locked' : element.approval_state"
    aria-label="État d'approbation"
  >
    <strong>{{ label }}</strong>
    <span v-if="element.approved_by">approuvé par {{ element.approved_by }}</span>
    <button
      v-for="next in element.allowed_transitions"
      :key="next"
      type="button"
      :disabled="busy"
      @click="take(next)"
    >
      {{ TRANSITION_LABELS[next] }}
    </button>
    <p v-if="failure" role="alert">{{ failure }}</p>
  </section>
</template>

<style scoped>
.banner {
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  align-items: baseline;
  padding: 0.5rem 0.8rem;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.banner[data-state='approved'],
.banner[data-state='locked'] {
  border-color: var(--success, #1b6e3a);
}
.banner[data-state='in_review'] {
  border-color: var(--warning, #9a6700);
}
</style>
```

`ElementDetail.vue` — script: `import { computed, ref, watch } from 'vue'` et `import ApprovalBanner from './ApprovalBanner.vue'`; après `defineEmits`:

```ts
// The transition answers with the element as it is now; the rest of the sheet
// is read-only, so a local copy is all that has to follow.
const shown = ref(props.element)
watch(
  () => props.element,
  (next) => {
    shown.value = next
  },
)
```
Template: juste après `</header>`: `<ApprovalBanner :element="shown" @changed="(next) => (shown = next)" />`.

- [ ] **Step 4: Relancer**

Run: `cd frontend && npm test -- --run` — Expected: PASS (dont `ElementDetail.spec.ts`, `ElementCatalogue.spec.ts`, `DiagramsSection.spec.ts`). Run: `cd frontend && npm run lint && npm run typecheck` — Expected: sans erreur.

- [ ] **Step 5: Commit**

```bash
git add frontend/src frontend/tests
git commit -m "feat(spa): approval banner on the element sheet, buttons from allowed_transitions" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 32: Vérification du lot 3 et ADR de l'approbation

**Files:**
- Create: `docs/adr/N3-approbation-et-verrouillage.md` (N3 = numéro suivant `N2`)
- Modify: occurrences `docs/adr/CCCC` → `docs/adr/N3`

- [ ] **Step 1: Fixer le numéro**

Run: `grep -rln "docs/adr/CCCC" backend frontend` puis `sed -i '' 's#docs/adr/CCCC#docs/adr/N3#g'` sur la liste relue. Expected: `grep -rn "CCCC" backend frontend` ne retourne rien.

- [ ] **Step 2: Écrire l'ADR**

```markdown
---
titre: Approbation et verrouillage des éléments, rôle ea-approver
date: 2026-10-10
statut: Proposition
affects: domain/approval.py, domain/auth.py, services/architecture.py, api/architecture.py, mcp/server.py, repositories/architecture_store.py, migrations 0011
---

# Approbation et verrouillage des éléments, rôle ea-approver

## Contexte

Le catalogue sert de référence: certains éléments doivent être relus puis
figés. Le rôle `ea-editor` écrit (ADR 0032) et n'a aucun moyen de dire « cet
élément a été revu ». Les agents MCP et le pipeline écrivent par les mêmes
services que le SPA: la règle doit être dans `services/`, pas dans un écran.

## Décision

- Un état par élément: `draft` → `in_review` → `approved`, plus un drapeau
  `locked` (permis seulement sur un élément `approved`). Quatre colonnes de
  `elements` (`approval_state`, `locked`, `approved_by_sub`, `approved_at`),
  **hors de `properties`**: ni l'unicité (type, nom) ni les index IPAM ne sont
  touchés, et un `PATCH` qui remplace `properties` ne peut pas effacer l'état.
  `CHECK` sur les trois valeurs et sur `locked ⇒ approved`. Les éléments
  existants deviennent des brouillons non verrouillés (migration 0011).
- Nouveau rôle de royaume `ea-approver` (`APPROVER_ROLE`), **qui ne vaut pas
  `ea-editor`** (les rôles se cumulent). `submit` est à l'éditeur; `approve`,
  `reject`, `reopen`, `lock`, `unlock` sont à l'approbateur. `Caller.can_approve`,
  `GET /me` l'expose; `LOCAL_DEVELOPER` et `SYSTEM` le portent (debug seulement).
- Les règles sont pures (`domain/approval.py`): table des transitions, rôle
  requis (403) puis état (409), et ce que chaque état interdit.
  `ArchitectureService.transition_element` les applique et écrit l'élément et
  son événement d'audit ensemble.
- Un élément `approved` refuse `update_element` et `delete_element` à un éditeur
  simple (`ElementLockedError`, 409); un approbateur peut, et l'élément repasse
  en `in_review` — jamais `approved` en silence. Un élément `locked` refuse
  tout, à tous, jusqu'à `unlock`. Un lien ajouté ou retiré sur un élément
  `approved` est permis et le repasse en `in_review` dans la même transaction;
  sur un `locked`, refusé. Un élément `in_review` reste modifiable.
- Séparation des tâches: l'approbateur ne peut pas approuver ce qu'il a modifié
  en dernier (dernier événement `element.updated`), désactivable par
  `EA_APPROVAL_FOUR_EYES=false`.
- La restauration d'une baseline refuse un plan qui touche un élément
  verrouillé et remet `in_review` un élément `approved` qu'elle modifie.
- Le SPA ne duplique aucune règle: `ElementRead.allowed_transitions` liste ce
  que l'appelant peut faire, un bouton par entrée.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| État dans `properties` | Aucune migration | Un `PATCH` l'efface; interfère avec l'unicité et l'IPAM | Écartée |
| `ea-approver` implique `ea-editor` | Un rôle de moins à donner | Rôles non cumulables, contraire à Keycloak | Écartée |
| Approuver aussi relations, diagrammes, documents | Couverture totale | Hors périmètre: l'élément seul porte l'état | Écartée |

## Conséquences

- ADR 0032 est complété, pas réécrit: le rôle `ea-approver` doit entrer dans
  l'import du realm `ea` — **à faire hors de ce repo** (dépôt Infra). Tant qu'il
  n'y est pas, personne ne peut approuver en production (0032 est encore une
  Proposition: le realm n'est pas importé).
- Un élément approuvé modifié par erreur par un approbateur revient en revue:
  c'est voulu, et visible dans l'historique.
- La séparation des tâches ne voit pas un approbateur qui n'a jamais modifié
  l'élément (seul `element.updated` compte): un créateur peut l'approuver.

## Références

- ADR liés: [0032](0032-authentification-par-keycloak.md), N1 (journal d'audit), N2 (baselines)
- Code: `backend/src/ea/domain/approval.py`, `backend/migrations/versions/0011_element_approval.py`
```

- [ ] **Step 3: Vérifier le lot**

Run: `make check` — Expected: PASS.
Run: `make test-integration` — Expected: PASS (0009 → 0011 appliquées et annulées, CHECK, atomicité des liens et de la revue).

- [ ] **Step 4: Commit**

```bash
git add docs/adr backend frontend
git commit -m "docs(adr): approval and locking of elements, the ea-approver role" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

# Lot 4 — Finition

### Task 33: `make baseline-capture` — la baseline avant la mise en service

**Files:**
- Create: `backend/src/ea/baseline_capture.py`, `backend/tests/unit/test_baseline_capture.py`
- Modify: `Makefile` (ligne `.PHONY` 197: ajouter `baseline-capture`; nouvelle cible après `files-reconcile`, ligne ~499)

**Interfaces:**
- Consumes: `BaselineService.create`, `SYSTEM`, `PostgresBaselineRepository`.
- Produces: `capture(service, *, name, description) -> Baseline` (agit comme `SYSTEM`), `run(settings, name, description) -> Baseline` (ouvre et ferme son moteur), `parse_arguments(argv) -> argparse.Namespace`, `main(argv=None) -> int`; cible `make baseline-capture NAME='…' [DESCRIPTION='…']`. La spec (§4, rétro-remplissage c) veut qu'une baseline existe avant la mise en service: l'opérateur la fait ici, jamais la migration.

- [ ] **Step 1: Écrire le test qui échoue**

```python
# backend/tests/unit/test_baseline_capture.py
"""The operator's capture, wired against doubles rather than servers."""

from __future__ import annotations

from typing import Any

import pytest

from ea import baseline_capture as script
from ea.core.config import Settings
from ea.domain.audit import AuditChannel
from ea.domain.auth import SYSTEM
from ea.services.baselines import BaselineService
from tests.conftest import InMemoryAuditLog, InMemoryBaselines


@pytest.mark.asyncio
class TestTheCapture:
    async def test_it_captures_as_the_system_with_no_request_behind_it(
        self,
        baseline_service: BaselineService,
        audit_log: InMemoryAuditLog,
        nobody_calling: None,
    ) -> None:
        baseline = await script.capture(
            baseline_service, name="avant mise en service", description=""
        )

        (event,) = audit_log.events
        assert baseline.created_by_sub == SYSTEM.subject
        assert event.actor_sub == SYSTEM.subject
        assert event.channel is AuditChannel.SYSTEM
        assert event.request_id == "-"

    async def test_it_closes_the_engine_it_opened(
        self,
        baseline_store: InMemoryBaselines,
        monkeypatch: pytest.MonkeyPatch,
        nobody_calling: None,
    ) -> None:
        log: list[str] = []

        class Engine:
            async def dispose(self) -> None:
                log.append("engine.closed")

        async def check(_: Any) -> None:
            log.append("engine.checked")

        monkeypatch.setattr(script, "create_engine", lambda _: Engine())
        monkeypatch.setattr(script, "check_connectivity", check)
        monkeypatch.setattr(script, "create_session_factory", lambda _: object())
        monkeypatch.setattr(script, "PostgresBaselineRepository", lambda _: baseline_store)

        baseline = await script.run(Settings(debug=True), "v1", "")

        assert baseline.name == "v1"
        assert log == ["engine.checked", "engine.closed"]


class TestTheArguments:
    def test_a_name_is_required(self) -> None:
        with pytest.raises(SystemExit):
            script.parse_arguments([])

    def test_the_description_is_optional(self) -> None:
        arguments = script.parse_arguments(["--name", "v1"])

        assert (arguments.name, arguments.description) == ("v1", "")
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_baseline_capture.py -x`
Expected: FAIL — `ImportError: cannot import name 'baseline_capture' from 'ea'`.

- [ ] **Step 3: Implémenter**

```python
# backend/src/ea/baseline_capture.py
"""Freeze the whole model into a baseline, from a terminal.

    uv run python -m ea.baseline_capture --name "avant mise en service"
    # or `make baseline-capture NAME='avant mise en service'` from the repo root

The backfill of revision 0009 gives the history a first line, but it makes no
baseline (docs/adr/N2): the operator takes one before the service goes live, so
there is something to compare against and to go back to. It is a script and
not an endpoint because it is an operator's job with no request behind it; it
acts as SYSTEM, which the audit trail then shows.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence

from ea.core.config import Settings, get_settings
from ea.core.logging import configure_logging
from ea.db.postgres import check_connectivity, create_engine, create_session_factory
from ea.domain.auth import SYSTEM
from ea.domain.baselines import Baseline
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.services.baselines import BaselineService
from ea.services.caller import acting_as


async def capture(service: BaselineService, *, name: str, description: str) -> Baseline:
    with acting_as(SYSTEM):
        return await service.create(name=name, description=description)


async def run(settings: Settings, name: str, description: str) -> Baseline:
    """Check the store answers before capturing, and let go of the pool whatever happens."""
    engine = create_engine(settings)
    try:
        await check_connectivity(engine)
        service = BaselineService(PostgresBaselineRepository(create_session_factory(engine)))
        return await capture(service, name=name, description=description)
    finally:
        await engine.dispose()


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m ea.baseline_capture", description=__doc__)
    parser.add_argument("--name", required=True, help="the name of the baseline (unique)")
    parser.add_argument("--description", default="", help="what this baseline is for")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    settings = get_settings()
    configure_logging(settings)
    baseline = asyncio.run(run(settings, arguments.name, arguments.description))
    print(
        f"baseline {baseline.name!r} captured: {baseline.element_count} element(s), "
        f"{baseline.relationship_count} relationship(s), {baseline.box_count} box(es)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - the entry point itself
    raise SystemExit(main())
```

`Makefile` — dans `.PHONY` ajouter `baseline-capture` après `files-reconcile`; après la cible `files-reconcile`:

```make
# La baseline d'avant la mise en service (docs/adr/N2) : la migration du journal
# donne un premier événement à chaque ligne existante mais ne capture rien. Un
# opérateur prend l'instantané ici, avant que quiconque n'écrive.
baseline-capture: | $(VENV_STAMP) ## Capture une baseline du modèle : make baseline-capture NAME='avant mise en service'
	@test -n "$(NAME)" || { printf "$(RED)NAME est requis : make baseline-capture NAME='avant mise en service'$(NC)\n"; exit 1; }
	@printf "$(RED)Cible : $(POSTGRES_HOST)/$(POSTGRES_DB), la base PARTAGÉE.$(NC)\n"
	cd $(BACKEND) && uv run python -m ea.baseline_capture --name "$(NAME)" --description "$(DESCRIPTION)"
```

- [ ] **Step 4: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_baseline_capture.py tests/unit/test_makefile_env.py -x` — Expected: PASS. Run: `make -n baseline-capture NAME=v1` — Expected: affiche la bannière puis la commande `uv run python -m ea.baseline_capture --name "v1"`, sans rien exécuter. Run: `make help | grep baseline` — Expected: la cible apparaît.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/baseline_capture.py backend/tests/unit/test_baseline_capture.py Makefile
git commit -m "feat(baselines): make baseline-capture, the snapshot before go-live" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 34: Rétention documentée, historique d'une baseline dans l'écran

**Files:**
- Create: `docs/audit-retention.md`, `backend/tests/unit/test_audit_retention_doc.py`
- Modify: `frontend/src/features/baselines/BaselinesSection.vue` (script: import de `RouterLink`; template: dans l'`<li>` d'une baseline), `frontend/tests/BaselinesSection.spec.ts` (ajout)

**Interfaces:**
- Produces: `docs/audit-retention.md` (procédure d'opérateur, hors application); un lien « Historique » par baseline vers `/history?kind=baseline&entity=<id>` (la suppression et la restauration d'une baseline sont des événements `baseline.deleted` / `baseline.restored`, lisibles là).

- [ ] **Step 1: Écrire les tests qui échouent**

```python
# backend/tests/unit/test_audit_retention_doc.py
"""The retention procedure names the triggers the migration creates — or it is a lie."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOC = (REPO / "docs" / "audit-retention.md").read_text(encoding="utf-8")
MIGRATION = (REPO / "backend" / "migrations" / "versions" / "0009_audit_events.py").read_text(
    encoding="utf-8"
)


def test_every_trigger_the_doc_disables_exists_in_the_migration() -> None:
    for trigger in ("audit_events_no_update_delete", "audit_events_no_truncate"):
        assert f"CREATE TRIGGER {trigger}" in MIGRATION
        assert trigger in DOC


def test_the_doc_says_it_is_an_operator_job_outside_the_application() -> None:
    assert "hors application" in DOC
    assert "24 mois" in DOC
```

Dans `frontend/tests/BaselinesSection.spec.ts`:

```ts
  it('links each baseline to its own history', async () => {
    await open()

    const link = await screen.findByRole('link', { name: 'Historique' })
    expect(link.getAttribute('href')).toContain(`kind=baseline`)
    expect(link.getAttribute('href')).toContain(`entity=${aBaseline().id}`)
  })
```

- [ ] **Step 2: Lancer, vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_audit_retention_doc.py -x` — Expected: FAIL — `FileNotFoundError: .../docs/audit-retention.md`.
Run: `cd frontend && npm test -- --run tests/BaselinesSection.spec.ts` — Expected: FAIL — pas de lien « Historique ».

- [ ] **Step 3: Écrire le document**

```markdown
# Rétention du journal d'audit

Le journal (`audit_events`, docs/adr/N1) est **append-only** et rien ne le purge
automatiquement. Ce document est la procédure d'un opérateur, **hors
application**: l'API et les services ne peuvent ni modifier ni supprimer une
ligne (les triggers lèvent), et c'est voulu.

## Quand

Au-delà de **24 mois**, ou si la table dépasse ~5 Go (ordre de grandeur: ~2 Ko
par événement, ~20 Mo par an et par utilisateur actif). Une baseline ne dépend
pas du journal: la purger ne casse aucune restauration.

## Procédure

1. **Archiver d'abord.** `make pg-backup` écrit un `pg_dump -Fc` de toute la base
   (la table y est), relu avant d'être renommé (docs/adr/0025). Un dump qui n'a
   pas quitté l'hôte n'est pas une sauvegarde: le copier ailleurs avant de continuer.
2. **Purger**, avec le rôle propriétaire de la base, en une transaction:

   ```sql
   BEGIN;
   ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update_delete;
   DELETE FROM audit_events WHERE at < now() - interval '24 months';
   ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update_delete;
   COMMIT;
   ```

   Le trigger `audit_events_no_truncate` reste actif: ne jamais vider la table
   d'un coup.
3. **Vérifier**: `SELECT count(*), min(at) FROM audit_events;` et que les deux
   triggers sont réactivés (`SELECT tgname, tgenabled FROM pg_trigger WHERE
   tgrelid = 'audit_events'::regclass AND NOT tgisinternal;` — `tgenabled = 'O'`).

## Limites

- Aucune chaîne de hachage: un opérateur de la base peut réécrire le journal.
  `pg_dump` régulier suffit à détecter une divergence (docs/adr/N1, risque R2).
- Le rétro-remplissage de la migration 0009 (`correlation = 'backfill'`) est
  purgé comme le reste.
- Si un rôle PostgreSQL d'écriture `INSERT, SELECT` seul est provisionné (à faire
  hors de ce repo), l'application ne pourra même plus désactiver un trigger; la
  purge exige alors le rôle propriétaire.
```

- [ ] **Step 4: Le lien de l'écran**

`BaselinesSection.vue` — script: `import { RouterLink, useRoute, useRouter } from 'vue-router'`. Template: dans l'`<li>` d'une baseline, avant le bouton « Comparer au modèle courant »:

```vue
        <RouterLink :to="{ name: 'history', query: { kind: 'baseline', entity: baseline.id } }">
          Historique
        </RouterLink>
```

- [ ] **Step 5: Relancer**

Run: `cd backend && uv run pytest tests/unit/test_audit_retention_doc.py -x` — Expected: PASS. Run: `cd frontend && npm test -- --run tests/BaselinesSection.spec.ts && npm run lint && npm run typecheck` — Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add docs/audit-retention.md backend/tests/unit/test_audit_retention_doc.py frontend/src/features/baselines/BaselinesSection.vue frontend/tests/BaselinesSection.spec.ts
git commit -m "docs(audit): retention procedure, and each baseline links to its own history" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 35: Clôture — `CLAUDE.md` et vérification finale

**Files:**
- Modify: `CLAUDE.md` (section « Project status », fin du paragraphe « **Every caller is authenticated by Keycloak** … »)

**Interfaces:**
- Consumes: les trois ADR (`N1`, `N2`, `N3`, fixés aux Tasks 12, 23, 32).

- [ ] **Step 1: Mettre à jour `CLAUDE.md` d'une phrase**

Dans `CLAUDE.md`, remplacer la fin du paragraphe d'authentification:

old_string:

```
decided in `services/` and nowhere else (see `docs/adr/0032`, still a *Proposition* — the realm has not been imported and the stack not redeployed).
```

new_string (remplacer `N1`, `N2`, `N3` par les numéros réels):

```
decided in `services/` and nowhere else (see `docs/adr/0032`, still a *Proposition* — the realm has not been imported and the stack not redeployed). **Every write of the catalogue also leaves an `audit_events` row in the same transaction (`services/` builds the event, the repository inserts it — `docs/adr/N1`), `/baselines` freezes, compares and restores the whole model (`docs/adr/N2`), and an element goes draft → in review → approved → locked under the realm role `ea-approver`, which the realm import must gain (`docs/adr/N3`).**
```

- [ ] **Step 2: Vérification finale**

Run: `make check` — Expected: PASS (lint, types, `openapi-check`, tests ≥ 90 %, Vitest, pipelines).
Run: `make test-integration` — Expected: PASS (0009, 0010, 0011 appliquées puis annulées sur le PostgreSQL jetable).
Run: `make audit` — Expected: PASS (bandit, pip-audit, npm audit; réseau requis).
Run: `git status --short` — Expected: rien d'indexé ni de modifié hors de ce qui a été commité.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: record the audit trail, baselines and approval in CLAUDE.md" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: À faire hors de ce repo (ne pas coder, signaler à l'opérateur)**

- Dépôt Infra: ajouter le rôle de royaume `ea-approver` à l'import du realm `ea` et l'assigner aux personnes concernées (le mapper de rôles qui exporte `realm_access.roles` est déjà celui d'`ea-editor`).
- Dépôt Infra / provisioning: rôle PostgreSQL d'écriture applicatif avec `INSERT, SELECT` seuls sur `audit_events` (le trigger reste la garantie tant qu'il n'existe pas).
- Opérateur: `make baseline-capture NAME='avant mise en service'` avant l'ouverture aux écritures; politique de rétention (`docs/audit-retention.md`).

---

## Auto-revue

**Couverture de la spec.**

| Spec | Tâche |
|---|---|
| §3 `domain/audit.py` (valeur, actions, `diff_fields`), granularité par cas d'usage, vues sans secret | 1, 6, 7 |
| §3 `domain/baselines.py` (`Baseline`, `ModelSnapshot`, `BaselineDiff`, `plan_restore`) | 13, 14 |
| §3 `domain/approval.py`, `Caller.can_approve`, `APPROVER_ROLE` | 24 |
| §4 `audit_events` + index + sans FK + `at` horloge injectée | 2, 4 |
| §4 immuabilité (trigger; REVOKE → hors repo) | 2 (écart 2) |
| §4 transaction commune (`audit` paramètre, `AuditWriter` → `write_events`) | 3, 5, 7 |
| §4 baselines en copie normalisée (4 tables) | 15, 16 |
| §4 approbation: colonnes, CHECK, hors `properties` | 25 |
| §4 rétro-remplissage (a), (b) / (c) | 2, 25 / 33 |
| §5 `GET /audit`, `/elements/{id}/history` | 8 |
| §5 baselines: CRUD, diff, restore (`dry_run`, `confirm`, 409) | 19 |
| §5 `POST /elements/{id}/approval`, champs d'`ElementRead`, 409 | 28 |
| §5 restauration (id d'origine, `keep_new`, cascade annoncée, légalité, unicité, verrouillés) | 14, 17, 18, 30 |
| §5 règles d'approbation (tableau, `approved`/`locked`, relations, `in_review` éditable) | 24, 26, 27 |
| §6 sept outils MCP + `INSTRUCTIONS` + canal | 9, 20, 29 |
| §7 SPA: Historique, Baselines (diff 3 colonnes, dialogue), fiche élément (bandeau, onglet) | 10, 11, 21, 22, 31 |
| §8 gardes (`require_approver`, AST), acteur du jeton, jamais de secret | 4, 24, 27 |
| §8 rôle Keycloak | Task 35 Step 4, ADR (hors repo) |
| §9 tests unitaires / services / intégration / API / MCP / Vitest | chaque tâche; Playwright absent (écart 14) |
| §10 trois ADR | 12, 23, 32 |
| R1 stockage, R3 rétention, R4 `EA_APPROVAL_FOUR_EYES`, R5 documents, R6, R8 | 12/34, 34, 27, 23, 26, 17 |
| R9 / extension « paquet » | non planifié (écart 8) |
| §12 lot 4 (cible `make`, documentation de rétention, audit des baselines dans l'écran) | 33, 34 |

**Placeholders.** Les seuls jetons non littéraux sont `NNNN`/`BBBB`/`CCCC` (numéros d'ADR) et `N1`/`N2`/`N3`, résolus par les Tasks 12, 23, 32 avec une commande `grep`/`sed` qui vérifie leur disparition. Aucune étape ne renvoie à une autre tâche pour son code.

**Cohérence des noms.** `write_events`, `AuditTrail.event`, `AuditService.list_events`, `InMemoryAuditLog.append|page|last_actor_of`, `Attachments`, `attachments_of` / `attachments_of_many`, `plan_restore`, `RestorePlan`, `ElementUpdate(current, restored, changes)`, `BaselineRepository` (`create`, `snapshot_of`, `current_snapshot`, `diagram_ids`, `apply_restore`, `locked_element_ids`), `BaselineService.restore(..., dry_run, confirm, keep_new) -> RestoreOutcome`, `transition_element(element_id, transition)`, `allowed_transitions(state, locked, caller)`, `reviewed_after_change`, `check_modifiable`, `touched=` sur `add_relationship` / `delete_relationship` sont utilisés avec ces signatures dans toutes les tâches qui les consomment.

**Risques.** (1) Chaque dépôt garde sa propre transaction: l'atomicité couvre une écriture et son événement, pas un cas d'usage à plusieurs écritures — point d'accroche pour la session partagée du sous-projet 3 (écart 6). (2) Le plan de restauration est calculé avant la transaction (écart 10). (3) `ElementRead.of` lit `current_caller`: tout appelant hors requête voit `allowed_transitions == []`. (4) Les numéros de migration (0009 à 0011) sont à renuméroter si une autre migration entre avant.
