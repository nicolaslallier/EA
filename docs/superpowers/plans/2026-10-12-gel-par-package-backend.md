# Gel par package — plan 1 : le cœur côté serveur

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal :** publier une version `vX.Y.Z` d'un package gèle tout son sous-arbre (appliqué par la base), prend une baseline limitée à ce sous-arbre, et laisse une trace complète.

**Architecture :** une table append-only `package_versions` dit quel package est gelé ; des triggers PostgreSQL refusent toute écriture dans un sous-arbre gelé (`SQLSTATE EA001`) ; `db/unit_of_work.work()` traduit ce refus en `PackageFrozenError` (409) et appelle un enregistreur d'audit ; `PackageService.publish/reopen` (rôle `ea-approver`) orchestrent, `PostgresPackageRepository` écrit version + baseline + audit dans une transaction.

**Tech Stack :** FastAPI, SQLAlchemy 2 async, Alembic, PostgreSQL (plpgsql), pytest (unit / integration sur conteneur jetable / e2e avec doubles).

**Spec :** `docs/superpowers/specs/2026-10-12-gel-par-package-design.md` (commit `973a0c8`).

**Hors de ce plan (plan 2, à écrire quand celui-ci est fusionné, car il consomme son schéma OpenAPI) :** `frozen_by` sur `ElementRead` / `DiagramRead`, outils MCP (`list_package_versions`, `publish_package`, `reopen_package`), baselines scopées dans la restauration et le diff, vue arbre du SPA, revue de sécurité finale de la branche complète. La tâche 9 ci-dessous fait la revue de sécurité de **ce** plan.

## Global Constraints

- Branche `claude/frozen-elements-tree-structure-e6fb97`, Conventional Commits, un commit par tâche, ligne finale `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`. Ne jamais `--no-verify`.
- Depuis `backend/` : `uv run pytest <fichier> -q` (sans `--cov`), `uv run ruff format . && uv run ruff check --fix .`, `uv run mypy src migrations`.
- Les tests d'intégration marqués `postgres` ne tournent que contre le conteneur jetable : `make test-integration` (racine) ou `make pg-up` puis `uv run pytest -m postgres`. Jamais contre la base partagée.
- Chaîne Alembic : la tête est `0019` ; la nouvelle révision est `0020`. Le modèle est importé par `db/models/__init__.py`. La convention de nommage des contraintes est figée (`db/base.py`).
- Dépendances : `api → services → domain ← repositories`. `domain/` n'importe ni FastAPI ni SQLAlchemy.
- Une borne HTTP est déclarée une fois (`api/schemas.py`) à partir d'une constante du domaine.
- Aucun secret, aucun DSN en dur. SQL brut : `text()` seulement avec paramètres liés ou DDL constant, avec un commentaire qui le justifie.
- Un refus de gel ne cite jamais un nom d'élément ni un texte : seulement identifiants, version, table, opération.
- Vocabulaire : « gelé » veut déjà dire autre chose pour un diagramme (`diagram.frozen`, ADR 0071) et pour un élément (`locked`, ADR 0048). Dans le code de ce plan : `published`, `in force`, `FrozenBy`, `PackageFrozenError`.

## Écarts à la spec (déjà reportés dans la spec, commit `973a0c8`)

Triggers PostgreSQL au lieu d'une garde Python ; publication en `READ COMMITTED` sous `LOCK TABLE … IN SHARE MODE` (une transaction `REPEATABLE READ` prend son instantané avant d'attendre le verrou) ; au-delà de 30 refus par minute et par `sub` l'événement d'audit est supprimé et le log compte les suppressions (au lieu d'un 429) ; code d'erreur existant `baseline_too_large` ; action d'audit `package.write_refused_frozen` ; pas de nouveau filtre `GET /audit` (`entity_kind=package&entity_id=` suffit).

## Review Focus

Entrées que la spec implique et qu'aucune tâche ne teste par ailleurs ; chacune a son test dans la tâche indiquée.

1. Un package vide est publié : version créée, baseline à zéro ligne, gel effectif (tâche 6).
2. Publication d'un parent dont un enfant est **déjà** publié : autorisée, l'enfant garde sa version (tâche 6).
3. Rouvrir un package dont la version publiée a un **ancêtre** publié : le trigger continue de refuser, `frozen_by` désigne l'ancêtre (tâche 6).
4. Deux publications simultanées du même package : une seule réussit, l'autre reçoit `package_already_frozen` (tâche 6).
5. Un élément **supprimé** alors qu'un diagramme publié le représente : refusé, rien n'est écrit (tâche 2).
6. Une notice de 4 000 caractères avec des guillemets, un retour à la ligne et de l'Unicode : stockée et relue à l'identique (tâche 1 et 6).

---

### Task 1 : domaine pur — versions, erreurs, actions permises

**Files :**
- Create : `backend/src/ea/domain/package_versions.py`
- Modify : `backend/src/ea/domain/errors.py` (ajout en fin de fichier)
- Modify : `backend/src/ea/domain/packages.py` (`PackageSummary`, ligne ~143)
- Test : `backend/tests/unit/test_package_versions.py`

**Interfaces :**
- Produces : `Bump`, `next_version(last: str | None, bump: Bump) -> str`, `Publication`, `publication(*, bump, notes, adr) -> Publication`, `reopening_reason(reason: str) -> str`, `digest(text: str) -> str`, `FrozenBy`, `FrozenRefusal`, `PackageVersion`, `PublicationPreview`, `PublishOutcome`, `effective_freezes(packages, in_force) -> dict[UUID, FrozenBy]`, `allowed_actions(caller, *, frozen_by, own_version) -> tuple[str, ...]`, constantes `MAX_NOTES_LENGTH`, `MAX_ADR_LENGTH`, `MAX_REASON_LENGTH`.
- Errors : `PackageFrozenError(message, refusal=None)`, `PackageAlreadyFrozenError`, `PackageNotFrozenError`, `AdrRequiredError`, `InvalidPublicationError`.

- [ ] **Step 1 : écrire le test qui échoue**

`backend/tests/unit/test_package_versions.py` :

```python
"""What publishing a package means, as pure rules (docs/adr/0074)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.auth import APPROVER_ROLE, EDITOR_ROLE, Caller
from ea.domain.errors import AdrRequiredError, InvalidPublicationError
from ea.domain.package_versions import (
    MAX_NOTES_LENGTH,
    Bump,
    FrozenBy,
    allowed_actions,
    digest,
    effective_freezes,
    next_version,
    publication,
    reopening_reason,
)
from ea.domain.packages import Package

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def a_package(name: str, parent: Package | None = None) -> Package:
    return Package.create(
        name=name, description="", parent_id=parent.id if parent else None, now=NOW
    )


class TestTheNextVersion:
    @pytest.mark.parametrize("bump", list(Bump))
    def test_the_first_publication_is_v1_whatever_the_level(self, bump: Bump) -> None:
        assert next_version(None, bump) == "v1.0.0"

    @pytest.mark.parametrize(
        ("bump", "expected"),
        [(Bump.PATCH, "v1.2.4"), (Bump.MINOR, "v1.3.0"), (Bump.MAJOR, "v2.0.0")],
    )
    def test_each_level_moves_its_number_and_resets_the_ones_below(
        self, bump: Bump, expected: str
    ) -> None:
        assert next_version("v1.2.3", bump) == expected

    @pytest.mark.parametrize("last", ["1.2.3", "v1.2", "v01.2.3", "v1.2.3-rc1", "", "v1.2.3\n"])
    def test_a_stored_version_that_does_not_read_is_refused_not_guessed(self, last: str) -> None:
        with pytest.raises(InvalidPublicationError):
            next_version(last, Bump.PATCH)


class TestWhatIsAskedOfAPublication:
    def test_notes_are_required_whatever_the_level(self) -> None:
        for bump in (Bump.PATCH, Bump.MINOR):
            with pytest.raises(InvalidPublicationError):
                publication(bump=bump, notes="   ", adr=None)

    def test_a_major_names_its_adr(self) -> None:
        with pytest.raises(AdrRequiredError):
            publication(bump=Bump.MAJOR, notes="Retrait de STD-APP-002", adr=None)
        with pytest.raises(AdrRequiredError):
            publication(bump=Bump.MAJOR, notes="Retrait", adr="  ")
        assert publication(bump=Bump.MAJOR, notes="Retrait", adr=" ADR-0042 ").adr == "ADR-0042"

    def test_notes_are_trimmed_and_kept_whole(self) -> None:
        text = 'Ligne « un »\nLigne "deux" — é' + "x" * 100
        assert publication(bump=Bump.MINOR, notes=f"  {text}  ", adr=None).notes == text

    def test_the_longest_notes_pass_and_one_more_does_not(self) -> None:
        publication(bump=Bump.MINOR, notes="n" * MAX_NOTES_LENGTH, adr=None)
        with pytest.raises(InvalidPublicationError):
            publication(bump=Bump.MINOR, notes="n" * (MAX_NOTES_LENGTH + 1), adr=None)

    def test_a_refusal_never_echoes_what_was_refused(self) -> None:
        with pytest.raises(InvalidPublicationError) as raised:
            publication(bump=Bump.MINOR, notes="secret-zzz\x00", adr=None)
        assert "secret-zzz" not in str(raised.value)

    def test_a_reason_is_required_and_bounded(self) -> None:
        assert reopening_reason("  correction d'une coquille ") == "correction d'une coquille"
        with pytest.raises(InvalidPublicationError):
            reopening_reason(" ")
        with pytest.raises(InvalidPublicationError):
            reopening_reason("x" * 501)

    def test_the_digest_is_a_stable_sha256(self) -> None:
        assert digest("abc") == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )


class TestWhatIsFrozenUnder:
    def test_a_published_package_freezes_itself_and_everything_below_it(self) -> None:
        root = a_package("Finance")
        child = a_package("Budget", root)
        leaf = a_package("2027", child)
        other = a_package("RH")
        tree = {p.id: p for p in (root, child, leaf, other)}

        frozen = effective_freezes(tree, {root.id: "v1.0.0"})

        assert set(frozen) == {root.id, child.id, leaf.id}
        assert frozen[leaf.id] == FrozenBy(root.id, "Finance", "v1.0.0")

    def test_the_nearest_published_ancestor_is_named(self) -> None:
        root = a_package("Finance")
        child = a_package("Budget", root)
        leaf = a_package("2027", child)
        tree = {p.id: p for p in (root, child, leaf)}

        frozen = effective_freezes(tree, {root.id: "v1.0.0", child.id: "v2.1.0"})

        assert frozen[leaf.id].package_id == child.id
        assert frozen[root.id].package_id == root.id

    def test_nothing_published_nothing_frozen(self) -> None:
        root = a_package("Finance")
        assert effective_freezes({root.id: root}, {}) == {}


class TestWhatACallerMayDo:
    approver = Caller("a", "a", frozenset({APPROVER_ROLE}))
    editor = Caller("e", "e", frozenset({EDITOR_ROLE}))
    here = FrozenBy(uuid4(), "Finance", "v1.0.0")

    def test_an_approver_publishes_an_open_package(self) -> None:
        assert allowed_actions(self.approver, frozen_by=None, own_version=None) == ("publish",)

    def test_an_approver_reopens_a_package_published_on_its_own(self) -> None:
        assert allowed_actions(self.approver, frozen_by=self.here, own_version="v1.0.0") == (
            "reopen",
        )

    def test_nothing_is_offered_under_an_ancestor_that_is_published(self) -> None:
        assert allowed_actions(self.approver, frozen_by=self.here, own_version=None) == ()

    def test_an_editor_without_the_approver_role_is_offered_nothing(self) -> None:
        assert allowed_actions(self.editor, frozen_by=None, own_version=None) == ()
```

- [ ] **Step 2 : vérifier l'échec**

Run : `cd backend && uv run pytest tests/unit/test_package_versions.py -q`
Expected : FAIL — `ModuleNotFoundError: ea.domain.package_versions`.

- [ ] **Step 3 : ajouter les erreurs**

Fin de `backend/src/ea/domain/errors.py` :

```python
class PackageFrozenError(DomainError):
    """A write reached a package that is published, or lies under one: reopen it first.

    Raised from the database's own refusal (SQLSTATE EA001), translated once in
    `ea.db.unit_of_work`. It names ids, a version, a table and an operation, never
    a name or a text.
    """

    def __init__(self, message: str, refusal: FrozenRefusal | None = None) -> None:
        super().__init__(message)
        self.refusal = refusal


class PackageAlreadyFrozenError(DomainError):
    """A publication aimed at a package that is already published, or under one that is."""


class PackageNotFrozenError(DomainError):
    """A reopening aimed at a package that has no version in force."""


class AdrRequiredError(DomainError):
    """A MAJOR publication that names no ADR."""


class InvalidPublicationError(DomainError):
    """Notes, ADR, reason or version that do not read. The message never echoes the value."""
```

Et en tête du même fichier, sous `from __future__ import annotations`, ajouter :

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ea.domain.package_versions import FrozenRefusal
```

- [ ] **Step 4 : écrire le module du domaine**

`backend/src/ea/domain/package_versions.py` :

```python
"""What publishing a package freezes, and how its version number moves (docs/adr/0074).

A published package freezes its whole subtree: the freeze is *derived* from the
rows of `package_versions` that are in force, never copied onto the elements, so
there is one truth to read and nothing to restore on reopening. The database
refuses the writes (triggers, docs/adr/0074); this module is the pure half: the
number, the words the caller must give, who is offered what.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from ea.domain.auth import Caller
from ea.domain.errors import AdrRequiredError, InvalidPublicationError
from ea.domain.packages import MAX_PACKAGE_DEPTH, Package, path_of

MAX_NOTES_LENGTH: Final = 4000
MAX_ADR_LENGTH: Final = 200
MAX_REASON_LENGTH: Final = 500
FIRST_VERSION: Final = "v1.0.0"

_VERSION: Final = re.compile(r"v(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")


class Bump(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


def next_version(last: str | None, bump: Bump) -> str:
    """The version that follows `last` (the latest ever published, reopened or not)."""
    if last is None:
        return FIRST_VERSION
    found = _VERSION.fullmatch(last)
    if found is None:
        msg = "the last version of this package does not read as vMAJOR.MINOR.PATCH"
        raise InvalidPublicationError(msg)
    major, minor, patch = (int(part) for part in found.groups())
    if bump is Bump.MAJOR:
        return f"v{major + 1}.0.0"
    if bump is Bump.MINOR:
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def _clean(text: str, what: str, limit: int) -> str:
    # The message names the field and the rule, never the value.
    if "\x00" in text:
        msg = f"{what} cannot contain a NUL character"
        raise InvalidPublicationError(msg)
    cleaned = text.strip()
    if not cleaned:
        msg = f"{what} cannot be blank"
        raise InvalidPublicationError(msg)
    if len(cleaned) > limit:
        msg = f"{what} is at most {limit} characters"
        raise InvalidPublicationError(msg)
    return cleaned


@dataclass(frozen=True, slots=True)
class Publication:
    bump: Bump
    notes: str
    adr: str | None


def publication(*, bump: Bump, notes: str, adr: str | None) -> Publication:
    """What a caller must say to publish: notes always, an ADR for a MAJOR."""
    cleaned_notes = _clean(notes, "the release notes", MAX_NOTES_LENGTH)
    cleaned_adr = (
        _clean(adr, "the ADR reference", MAX_ADR_LENGTH)
        if adr is not None and adr.strip()
        else None
    )
    if adr is not None and "\x00" in adr:
        msg = "the ADR reference cannot contain a NUL character"
        raise InvalidPublicationError(msg)
    if bump is Bump.MAJOR and cleaned_adr is None:
        msg = "a MAJOR version names the ADR that decides it"
        raise AdrRequiredError(msg)
    return Publication(bump=bump, notes=cleaned_notes, adr=cleaned_adr)


def reopening_reason(reason: str) -> str:
    return _clean(reason, "the reason", MAX_REASON_LENGTH)


def digest(text: str) -> str:
    """What the audit trail keeps of a text: its SHA-256, never the text (docs/adr/0046)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenBy:
    """The package whose publication freezes another (or itself)."""

    package_id: UUID
    path: str
    version: str


@dataclass(frozen=True, slots=True)
class FrozenRefusal:
    """What the database said when it refused a write: ids and words, nothing of the content.

    Every field is `None` when the refusal's detail could not be read: the write is
    refused all the same (fail closed).
    """

    package_id: UUID | None
    version: str | None
    table: str | None
    operation: str | None


@dataclass(frozen=True, slots=True)
class PackageVersion:
    id: UUID
    package_id: UUID
    version: str
    bump: Bump
    notes: str
    adr: str | None
    baseline_id: UUID
    element_count: int
    relationship_count: int
    box_count: int
    published_by_sub: str
    published_by: str
    published_at: datetime
    publish_audit_event_id: UUID | None = None
    reopened_by_sub: str | None = None
    reopened_by: str | None = None
    reopened_at: datetime | None = None
    reopen_reason: str | None = None
    reopen_audit_event_id: UUID | None = None

    @property
    def in_force(self) -> bool:
        return self.reopened_at is None


@dataclass(frozen=True, slots=True)
class PublicationPreview:
    """What publishing would do now: the number and the size of what it would freeze."""

    version: str
    bump: Bump
    elements: int
    relationships: int
    boxes: int
    diagrams: int


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    preview: PublicationPreview
    published: PackageVersion | None


def effective_freezes(
    packages: Mapping[UUID, Package], in_force: Mapping[UUID, str]
) -> dict[UUID, FrozenBy]:
    """For each package that is published or lies under one: the nearest publication."""
    found: dict[UUID, FrozenBy] = {}
    for package in packages.values():
        node: Package | None = package
        for _ in range(MAX_PACKAGE_DEPTH + 1):
            if node is None:
                break
            if node.id in in_force:
                found[package.id] = FrozenBy(node.id, path_of(node, packages), in_force[node.id])
                break
            node = packages.get(node.parent_id) if node.parent_id is not None else None
    return found


def allowed_actions(
    caller: Caller, *, frozen_by: FrozenBy | None, own_version: str | None
) -> tuple[str, ...]:
    """The gestures to offer: one per button, decided here and drawn by the SPA."""
    if not caller.can_approve:
        return ()
    if own_version is not None:
        return ("reopen",)
    if frozen_by is not None:
        return ()
    return ("publish",)
```

- [ ] **Step 5 : étendre `PackageSummary`**

Dans `backend/src/ea/domain/packages.py`, remplacer

```python
@dataclass(frozen=True, slots=True)
class PackageSummary:
    package: Package
    path: str
    counts: PackageCounts
```

par

```python
@dataclass(frozen=True, slots=True)
class PackageSummary:
    package: Package
    path: str
    counts: PackageCounts
    #: The publication that freezes this package (itself or an ancestor), docs/adr/0074.
    frozen_by: FrozenBy | None = None
    #: The version in force on *this* package, if it was published on its own.
    own_version: str | None = None
    #: The gestures offered to the caller who read it: "publish", "reopen".
    actions: tuple[str, ...] = ()
```

et ajouter sous les imports `if TYPE_CHECKING: from ea.domain.package_versions import FrozenBy` (le fichier a déjà `from __future__ import annotations` ; si `TYPE_CHECKING` n'est pas importé, l'ajouter à la ligne `from typing import ...`).

- [ ] **Step 6 : vérifier**

Run : `cd backend && uv run pytest tests/unit/test_package_versions.py tests/unit/test_packages.py tests/unit/test_package_service.py -q && uv run mypy src`
Expected : PASS, mypy propre. (Si mypy signale un import circulaire `packages ↔ package_versions`, garder l'import de `FrozenBy` sous `TYPE_CHECKING` dans `packages.py`, comme indiqué.)

- [ ] **Step 7 : commit**

```bash
git add backend/src/ea/domain backend/tests/unit/test_package_versions.py
git commit -m "feat(domain): versions de package, erreurs de gel, actions permises

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 2 : migration `0020`, modèles, triggers

**Files :**
- Create : `backend/migrations/versions/0020_package_versions.py`
- Create : `backend/src/ea/db/models/package_version.py`
- Modify : `backend/src/ea/db/models/__init__.py` (importer le module)
- Modify : `backend/src/ea/db/models/baseline.py` (deux colonnes sur `BaselineRecord`)
- Test : `backend/tests/integration/test_package_freeze_triggers.py`

**Interfaces :**
- Produces : table `package_versions`, colonnes `baselines.scope_package_id`, `baselines.scope_path`, fonctions SQL `ea_frozen_ancestor(uuid)`, `ea_assert_unfrozen(uuid, text, text)`, SQLSTATE `EA001` dont le `DETAIL` vaut `<package_id>|<version>|<table>|<operation>`, `PackageVersionRecord`.

- [ ] **Step 1 : écrire le test d'intégration qui échoue**

`backend/tests/integration/test_package_freeze_triggers.py` :

```python
"""The database refuses every write into a published subtree (docs/adr/0074)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

AT = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
FROZEN = "EA001"


def sqlstate_of(error: DBAPIError) -> str | None:
    return getattr(error.orig.__cause__, "sqlstate", None)  # type: ignore[union-attr]


def detail_of(error: DBAPIError) -> str | None:
    return getattr(error.orig.__cause__, "detail", None)  # type: ignore[union-attr]


async def insert(connection: AsyncConnection, table: str, **values: Any) -> None:
    columns = ", ".join(values)
    marks = ", ".join(f":{name}" for name in values)
    # The table and column names are the test's own constants, the values are bound.
    await connection.execute(
        text(f"INSERT INTO {table} ({columns}) VALUES ({marks})"),  # noqa: S608  # nosec B608
        values,
    )


async def an_element(connection: AsyncConnection, name: str, package: UUID | None) -> UUID:
    element_id = uuid4()
    await insert(
        connection,
        "elements",
        id=element_id,
        element_type="node",
        layer="technology",
        aspect="active_structure",
        name=name,
        package_id=package,
        created_at=AT,
        updated_at=AT,
    )
    return element_id


async def a_package(connection: AsyncConnection, name: str, parent: UUID | None) -> UUID:
    package_id = uuid4()
    await insert(
        connection,
        "packages",
        id=package_id,
        parent_id=parent,
        name=name,
        created_at=AT,
        updated_at=AT,
    )
    return package_id


async def a_diagram(connection: AsyncConnection, name: str, package: UUID | None) -> UUID:
    diagram_id = uuid4()
    await insert(
        connection,
        "diagrams",
        id=diagram_id,
        name=name,
        package_id=package,
        created_at=AT,
        updated_at=AT,
    )
    return diagram_id


async def publish(connection: AsyncConnection, package_id: UUID, version: str = "v1.0.0") -> UUID:
    baseline_id, version_id = uuid4(), uuid4()
    await insert(
        connection,
        "baselines",
        id=baseline_id,
        name=f"{package_id} {version}",
        created_by_sub="s",
        created_by="n",
        created_at=AT,
    )
    await insert(
        connection,
        "package_versions",
        id=version_id,
        package_id=package_id,
        version=version,
        bump="minor",
        notes="notes",
        baseline_id=baseline_id,
        element_count=0,
        relationship_count=0,
        box_count=0,
        published_by_sub="s",
        published_by="n",
        published_at=AT,
        publish_audit_event_id=uuid4(),
    )
    return version_id


class World:
    """A published `root`, its sub-package, and one row of every guarded table."""

    root: UUID
    child: UUID
    inside: UUID
    inside_peer: UUID
    outside: UUID
    link: UUID
    document: UUID
    diagram: UUID
    outside_diagram: UUID
    stereotype: UUID
    free: UUID


async def build_world(connection: AsyncConnection) -> World:
    world = World()
    world.root = await a_package(connection, "Finance", None)
    world.free = await a_package(connection, "Libre", None)
    world.child = await a_package(connection, "Budget", world.root)
    world.inside = await an_element(connection, "inside", world.child)
    world.inside_peer = await an_element(connection, "inside-peer", world.root)
    world.outside = await an_element(connection, "outside", None)
    world.link = uuid4()
    await insert(
        connection,
        "relationships",
        id=world.link,
        relationship_type="association",
        source_id=world.inside,
        target_id=world.outside,
        source_type="node",
        target_type="node",
        created_at=AT,
    )
    world.document = uuid4()
    await insert(
        connection,
        "element_documents",
        id=world.document,
        element_id=world.inside,
        filename="a.md",
        content="x",
        created_at=AT,
        updated_at=AT,
    )
    world.diagram = await a_diagram(connection, "Vue", world.child)
    await insert(
        connection,
        "diagram_nodes",
        diagram_id=world.diagram,
        element_id=world.outside,
        x=0,
        y=0,
    )
    world.outside_diagram = await a_diagram(connection, "Libre", None)
    profile, world.stereotype = uuid4(), uuid4()
    await insert(connection, "profiles", id=profile, name="P", created_at=AT, updated_at=AT)
    await insert(
        connection,
        "profile_stereotypes",
        id=world.stereotype,
        profile_id=profile,
        name="S",
        base_type="node",
        icon_key="k",
    )
    return world


def refused(world: World) -> dict[str, tuple[str, dict[str, Any]]]:
    """One write per guarded table (and the tricky corners), each of which must be refused."""
    return {
        "element update": (
            "UPDATE elements SET description = 'x' WHERE id = :id",
            {"id": world.inside},
        ),
        "element approval": (
            "UPDATE elements SET approval_state = 'in_review' WHERE id = :id",
            {"id": world.inside},
        ),
        "element delete": ("DELETE FROM elements WHERE id = :id", {"id": world.inside}),
        "element filed in": (
            "UPDATE elements SET package_id = :p WHERE id = :id",
            {"id": world.outside, "p": world.child},
        ),
        "element filed out": (
            "UPDATE elements SET package_id = NULL WHERE id = :id",
            {"id": world.inside},
        ),
        "element created inside": (
            "INSERT INTO elements (id, element_type, layer, aspect, name, package_id, "
            "created_at, updated_at) VALUES (:id, 'node', 'technology', 'active_structure', "
            "'new', :p, :at, :at)",
            {"id": uuid4(), "p": world.child, "at": AT},
        ),
        "link update (one end inside)": (
            "UPDATE relationships SET name = 'x' WHERE id = :id",
            {"id": world.link},
        ),
        "link delete (one end inside)": (
            "DELETE FROM relationships WHERE id = :id",
            {"id": world.link},
        ),
        "link created (one end inside)": (
            "INSERT INTO relationships (id, relationship_type, source_id, target_id, "
            "source_type, target_type, created_at) VALUES "
            "(:id, 'association', :s, :t, 'node', 'node', :at)",
            {"id": uuid4(), "s": world.outside, "t": world.inside_peer, "at": AT},
        ),
        "document update": (
            "UPDATE element_documents SET content = 'y' WHERE id = :id",
            {"id": world.document},
        ),
        "document delete": (
            "DELETE FROM element_documents WHERE id = :id",
            {"id": world.document},
        ),
        "document created": (
            "INSERT INTO element_documents (id, element_id, filename, content, created_at, "
            "updated_at) VALUES (:id, :e, 'b.md', 'x', :at, :at)",
            {"id": uuid4(), "e": world.inside, "at": AT},
        ),
        "stereotype laid": (
            "INSERT INTO element_stereotypes (element_id, stereotype_id) VALUES (:e, :s)",
            {"e": world.inside, "s": world.stereotype},
        ),
        "diagram update": (
            "UPDATE diagrams SET description = 'x' WHERE id = :id",
            {"id": world.diagram},
        ),
        "diagram delete": ("DELETE FROM diagrams WHERE id = :id", {"id": world.diagram}),
        "diagram filed in": (
            "UPDATE diagrams SET package_id = :p WHERE id = :id",
            {"id": world.outside_diagram, "p": world.child},
        ),
        "diagram box moved": (
            "UPDATE diagram_nodes SET x = 9 WHERE diagram_id = :d",
            {"d": world.diagram},
        ),
        "diagram box added": (
            "INSERT INTO diagram_nodes (diagram_id, element_id, x, y) VALUES (:d, :e, 0, 0)",
            {"d": world.diagram, "e": world.inside_peer},
        ),
        "diagram note added": (
            "INSERT INTO diagram_objects (id, diagram_id, kind, x, y, width, height) "
            "VALUES (:id, :d, 'note', 0, 0, 10, 10)",
            {"id": uuid4(), "d": world.diagram},
        ),
        "package renamed": (
            "UPDATE packages SET name = 'X' WHERE id = :id",
            {"id": world.child},
        ),
        "published package renamed": (
            "UPDATE packages SET name = 'X' WHERE id = :id",
            {"id": world.root},
        ),
        "package moved in": (
            "UPDATE packages SET parent_id = :p WHERE id = :id",
            {"id": world.free, "p": world.child},
        ),
        "sub-package created": (
            "INSERT INTO packages (id, parent_id, name, created_at, updated_at) "
            "VALUES (:id, :p, 'N', :at, :at)",
            {"id": uuid4(), "p": world.child, "at": AT},
        ),
        "package deleted": ("DELETE FROM packages WHERE id = :id", {"id": world.child}),
        # Review focus 5: an unpublished element that a published diagram draws.
        "outside element deleted while a published diagram draws it": (
            "DELETE FROM elements WHERE id = :id",
            {"id": world.outside},
        ),
    }


async def test_every_write_into_a_published_subtree_is_refused_by_the_database(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        await publish(connection, world.root)

    for label, (sql, params) in refused(world).items():
        with pytest.raises(DBAPIError) as raised:
            async with engine_at_head.begin() as connection:
                await connection.execute(text(sql), params)
        assert sqlstate_of(raised.value) == FROZEN, label
        parts = (detail_of(raised.value) or "").split("|")
        assert parts[0] == str(world.root), label
        assert parts[1] == "v1.0.0", label

    async with engine_at_head.connect() as connection:
        untouched = (
            await connection.execute(
                text("SELECT description FROM elements WHERE id = :id"), {"id": world.inside}
            )
        ).scalar_one()
    assert untouched == ""


async def test_what_is_outside_a_published_subtree_stays_writable(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        await publish(connection, world.root)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET description = 'ok' WHERE id = :id"), {"id": world.outside}
        )
        await connection.execute(
            text("UPDATE diagrams SET description = 'ok' WHERE id = :id"),
            {"id": world.outside_diagram},
        )
        await a_package(connection, "Ailleurs", None)


async def test_nothing_is_guarded_while_nothing_is_published(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET description = 'ok' WHERE id = :id"), {"id": world.inside}
        )
        await connection.execute(text("DELETE FROM elements WHERE id = :id"), {"id": world.inside})


async def test_reopening_lifts_the_freeze(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        version_id = await publish(connection, world.root)
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "UPDATE package_versions SET reopened_by_sub = 's', reopened_by = 'n', "
                "reopened_at = :at, reopen_reason = 'r', reopen_audit_event_id = :e "
                "WHERE id = :id"
            ),
            {"at": AT, "e": uuid4(), "id": version_id},
        )
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("UPDATE elements SET description = 'ok' WHERE id = :id"), {"id": world.inside}
        )


async def test_a_version_is_append_only_and_only_its_reopening_can_be_written(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        world = await build_world(connection)
        version_id = await publish(connection, world.root)

    for sql in (
        "UPDATE package_versions SET notes = 'rewritten' WHERE id = :id",
        "UPDATE package_versions SET version = 'v9.9.9' WHERE id = :id",
        "DELETE FROM package_versions WHERE id = :id",
    ):
        with pytest.raises(DBAPIError):
            async with engine_at_head.begin() as connection:
                await connection.execute(text(sql), {"id": version_id})
    with pytest.raises(DBAPIError):
        async with engine_at_head.begin() as connection:
            await connection.execute(text("TRUNCATE package_versions"))

    async with engine_at_head.begin() as connection:
        await connection.execute(
            text(
                "UPDATE package_versions SET reopened_by_sub = 's', reopened_by = 'n', "
                "reopened_at = :at, reopen_reason = 'r', reopen_audit_event_id = :e "
                "WHERE id = :id"
            ),
            {"at": AT, "e": uuid4(), "id": version_id},
        )
    with pytest.raises(DBAPIError):  # a reopened version never changes again
        async with engine_at_head.begin() as connection:
            await connection.execute(
                text("UPDATE package_versions SET reopen_reason = 'again' WHERE id = :id"),
                {"id": version_id},
            )


async def test_only_one_version_is_in_force_per_package(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await publish(connection, root, "v1.0.0")
    with pytest.raises(DBAPIError):
        async with engine_at_head.begin() as connection:
            await publish(connection, root, "v1.1.0")


async def test_a_baseline_a_version_points_at_cannot_be_deleted(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await publish(connection, root)
    with pytest.raises(DBAPIError):
        async with engine_at_head.begin() as connection:
            await connection.execute(text("DELETE FROM baselines"))
```

- [ ] **Step 2 : vérifier l'échec**

Run : `make pg-up` (racine) puis `cd backend && uv run pytest tests/integration/test_package_freeze_triggers.py -q -m postgres`
Expected : FAIL — `relation "package_versions" does not exist` (la migration n'existe pas).

- [ ] **Step 3 : le modèle**

`backend/src/ea/db/models/package_version.py` :

```python
"""The versions a package was published under (docs/adr/0074).

A row is written when the package is published and closed (`reopened_*`) when it
is reopened; nothing else about it ever changes — triggers refuse it. The package
is *frozen* exactly while a row of it is in force, which is what the partial
unique index lets there be at most one of.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class PackageVersionRecord(Base):
    __tablename__ = "package_versions"
    __table_args__ = (
        CheckConstraint("bump in ('major', 'minor', 'patch')", name="bump_known"),
        CheckConstraint(
            "(reopened_at is null) = (reopened_by_sub is null)", name="reopening_whole"
        ),
        Index("uq_package_versions_package_version", "package_id", "version", unique=True),
        # Static DDL text: at most one version in force per package.
        Index(
            "uq_package_versions_in_force",
            "package_id",
            unique=True,
            postgresql_where=text("reopened_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    package_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    bump: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    adr: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="RESTRICT"), nullable=False
    )
    element_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relationship_count: Mapped[int] = mapped_column(Integer, nullable=False)
    box_count: Mapped[int] = mapped_column(Integer, nullable=False)
    published_by_sub: Mapped[str] = mapped_column(Text, nullable=False)
    published_by: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # No foreign key to `audit_events`, by design (docs/adr/0046): the trail outlives its subject.
    publish_audit_event_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reopened_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopen_audit_event_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
```

Dans `backend/src/ea/db/models/baseline.py`, ajouter à `BaselineRecord` (après `created_at`) :

```python
    # Set when the baseline copies one package's subtree rather than the whole model
    # (docs/adr/0074). No foreign key: the copy outlives the package; the path is copied.
    scope_package_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    scope_path: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Dans `backend/src/ea/db/models/__init__.py`, importer `package_version` comme les autres modules (même style de ligne que `package`) et l'ajouter à `__all__` s'il y en a un.

- [ ] **Step 4 : la migration**

`backend/migrations/versions/0020_package_versions.py` :

```python
"""les versions d'un package et le gel de son sous-arbre (docs/adr/0074)

Revision ID: 0020
Revises: 0019
Date: 2026-10-12

Une table neuve, deux colonnes nullables sur `baselines`, des fonctions et des triggers :
aucune donnée existante n'est lue, réécrite ni convertie, et tant qu'aucune version n'est
en vigueur les triggers sortent à la première sonde (un index partiel vide) : cette révision
ne peut pas échouer sur des lignes déjà là. `lock_timeout` borne l'attente des verrous que
prend la création des triggers. `downgrade` retire triggers, fonctions et table : les versions
publiées sont perdues, les baselines qu'elles désignent restent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LOCK_TIMEOUT = "10s"

# DDL statique, aucune valeur d'exécution : rien à lier.
_FROZEN_ANCESTOR = """
CREATE FUNCTION ea_frozen_ancestor(start uuid)
RETURNS TABLE (package_id uuid, version text)
LANGUAGE sql STABLE AS $$
    WITH RECURSIVE up (id, parent_id, depth) AS (
        SELECT p.id, p.parent_id, 0 FROM packages p WHERE p.id = start
        UNION ALL
        SELECT p.id, p.parent_id, up.depth + 1 FROM packages p JOIN up ON p.id = up.parent_id
    )
    SELECT v.package_id, v.version
    FROM up JOIN package_versions v ON v.package_id = up.id AND v.reopened_at IS NULL
    ORDER BY up.depth
    LIMIT 1
$$
"""

_ASSERT_UNFROZEN = """
CREATE FUNCTION ea_assert_unfrozen(pkg uuid, tbl text, op text) RETURNS void
LANGUAGE plpgsql STABLE AS $$
DECLARE
    found record;
BEGIN
    IF pkg IS NULL THEN
        RETURN;
    END IF;
    -- The common case, nothing published anywhere: one probe of the partial index.
    IF NOT EXISTS (SELECT 1 FROM package_versions WHERE reopened_at IS NULL) THEN
        RETURN;
    END IF;
    SELECT * INTO found FROM ea_frozen_ancestor(pkg);
    IF FOUND THEN
        RAISE EXCEPTION 'package is published: reopen it first'
            USING ERRCODE = 'EA001',
                  DETAIL = found.package_id::text || '|' || found.version || '|' || tbl || '|' || op;
    END IF;
END
$$
"""

_RETURN_ROW = """
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
"""


def _guard(name: str, body: str) -> str:
    return (
        f"CREATE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$\n"
        f"BEGIN\n{body}{_RETURN_ROW}END\n$$"
    )


# Both the old and the new place of a row are checked: filing out of, or into, a published
# subtree is a write to it. Elements and diagrams both carry their own `package_id`.
_BY_OWN_PACKAGE = """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(OLD.package_id, TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(NEW.package_id, TG_TABLE_NAME, TG_OP);
    END IF;
"""
_ELEMENTS = _guard("ea_guard_elements", _BY_OWN_PACKAGE)
_DIAGRAMS = _guard("ea_guard_diagrams", _BY_OWN_PACKAGE)
_PACKAGES = _guard(
    "ea_guard_packages",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(OLD.id, TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(NEW.parent_id, TG_TABLE_NAME, TG_OP);
    END IF;
""",
)
_RELATIONSHIPS = _guard(
    "ea_guard_relationships",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = OLD.source_id), TG_TABLE_NAME, TG_OP);
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = OLD.target_id), TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = NEW.source_id), TG_TABLE_NAME, TG_OP);
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = NEW.target_id), TG_TABLE_NAME, TG_OP);
    END IF;
""",
)
# A row that hangs from an element (a document, a stereotype) or from a diagram (a box, a note,
# a route): the owner's place decides. `to_jsonb` reads the column by name for every table.
_OF_ELEMENT = _guard(
    "ea_guard_of_element",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = (to_jsonb(OLD) ->> 'element_id')::uuid),
            TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM elements WHERE id = (to_jsonb(NEW) ->> 'element_id')::uuid),
            TG_TABLE_NAME, TG_OP);
    END IF;
""",
)
_OF_DIAGRAM = _guard(
    "ea_guard_of_diagram",
    """
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM diagrams WHERE id = (to_jsonb(OLD) ->> 'diagram_id')::uuid),
            TG_TABLE_NAME, TG_OP);
    END IF;
    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        PERFORM ea_assert_unfrozen(
            (SELECT package_id FROM diagrams WHERE id = (to_jsonb(NEW) ->> 'diagram_id')::uuid),
            TG_TABLE_NAME, TG_OP);
    END IF;
""",
)

_VERSIONS_IMMUTABLE = """
CREATE FUNCTION ea_package_versions_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP <> 'UPDATE' THEN
        RAISE EXCEPTION 'package_versions is append-only' USING ERRCODE = 'restrict_violation';
    END IF;
    -- Only closing a version in force is allowed, and nothing else may move with it.
    IF OLD.reopened_at IS NOT NULL
       OR (NEW.id, NEW.package_id, NEW.version, NEW.bump, NEW.notes, NEW.adr, NEW.baseline_id,
           NEW.element_count, NEW.relationship_count, NEW.box_count, NEW.published_by_sub,
           NEW.published_by, NEW.published_at, NEW.publish_audit_event_id)
          IS DISTINCT FROM
          (OLD.id, OLD.package_id, OLD.version, OLD.bump, OLD.notes, OLD.adr, OLD.baseline_id,
           OLD.element_count, OLD.relationship_count, OLD.box_count, OLD.published_by_sub,
           OLD.published_by, OLD.published_at, OLD.publish_audit_event_id)
    THEN
        RAISE EXCEPTION 'package_versions is append-only' USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$
"""

#: (table, trigger function) — one BEFORE ... FOR EACH ROW trigger each.
_GUARDED = (
    ("elements", "ea_guard_elements"),
    ("relationships", "ea_guard_relationships"),
    ("element_documents", "ea_guard_of_element"),
    ("element_stereotypes", "ea_guard_of_element"),
    ("diagrams", "ea_guard_diagrams"),
    ("diagram_nodes", "ea_guard_of_diagram"),
    ("diagram_objects", "ea_guard_of_diagram"),
    ("diagram_edges", "ea_guard_of_diagram"),
    ("packages", "ea_guard_packages"),
)


def upgrade() -> None:
    op.execute(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'")  # nosec B608 - a constant
    op.create_table(
        "package_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("package_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("bump", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("adr", sa.Text(), nullable=True),
        sa.Column("baseline_id", sa.Uuid(), nullable=False),
        sa.Column("element_count", sa.Integer(), nullable=False),
        sa.Column("relationship_count", sa.Integer(), nullable=False),
        sa.Column("box_count", sa.Integer(), nullable=False),
        sa.Column("published_by_sub", sa.Text(), nullable=False),
        sa.Column("published_by", sa.Text(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("publish_audit_event_id", sa.Uuid(), nullable=False),
        sa.Column("reopened_by_sub", sa.Text(), nullable=True),
        sa.Column("reopened_by", sa.Text(), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column("reopen_audit_event_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint("bump in ('major', 'minor', 'patch')", name=op.f("ck_package_versions_bump_known")),
        sa.CheckConstraint(
            "(reopened_at is null) = (reopened_by_sub is null)",
            name=op.f("ck_package_versions_reopening_whole"),
        ),
        sa.ForeignKeyConstraint(
            ["package_id"],
            ["packages.id"],
            name=op.f("fk_package_versions_package_id_packages"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["baseline_id"],
            ["baselines.id"],
            name=op.f("fk_package_versions_baseline_id_baselines"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_package_versions")),
    )
    op.create_index(
        "uq_package_versions_package_version", "package_versions", ["package_id", "version"], unique=True
    )
    op.create_index(
        "uq_package_versions_in_force",
        "package_versions",
        ["package_id"],
        unique=True,
        postgresql_where=sa.text("reopened_at IS NULL"),
    )
    op.add_column("baselines", sa.Column("scope_package_id", sa.Uuid(), nullable=True))
    op.add_column("baselines", sa.Column("scope_path", sa.Text(), nullable=True))

    op.execute(_FROZEN_ANCESTOR)
    op.execute(_ASSERT_UNFROZEN)
    for function in (_ELEMENTS, _DIAGRAMS, _PACKAGES, _RELATIONSHIPS, _OF_ELEMENT, _OF_DIAGRAM):
        op.execute(function)
    for table, function in _GUARDED:
        op.execute(
            f"CREATE TRIGGER {table}_frozen BEFORE INSERT OR UPDATE OR DELETE ON {table} "  # nosec B608
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
    op.execute(_VERSIONS_IMMUTABLE)
    op.execute(
        "CREATE TRIGGER package_versions_append_only BEFORE UPDATE OR DELETE ON package_versions "
        "FOR EACH ROW EXECUTE FUNCTION ea_package_versions_append_only()"
    )
    op.execute(
        "CREATE TRIGGER package_versions_no_truncate BEFORE TRUNCATE ON package_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION ea_package_versions_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER package_versions_no_truncate ON package_versions")
    op.execute("DROP TRIGGER package_versions_append_only ON package_versions")
    for table, _ in reversed(_GUARDED):
        op.execute(f"DROP TRIGGER {table}_frozen ON {table}")  # nosec B608
    op.execute("DROP FUNCTION ea_package_versions_append_only()")
    for name in (
        "ea_guard_of_diagram",
        "ea_guard_of_element",
        "ea_guard_relationships",
        "ea_guard_packages",
        "ea_guard_diagrams",
        "ea_guard_elements",
    ):
        op.execute(f"DROP FUNCTION {name}()")  # nosec B608
    op.execute("DROP FUNCTION ea_assert_unfrozen(uuid, text, text)")
    op.execute("DROP FUNCTION ea_frozen_ancestor(uuid)")
    op.drop_column("baselines", "scope_path")
    op.drop_column("baselines", "scope_package_id")
    op.drop_index("uq_package_versions_in_force", table_name="package_versions")
    op.drop_index("uq_package_versions_package_version", table_name="package_versions")
    op.drop_table("package_versions")
```

- [ ] **Step 5 : lancer le test**

Run : `cd backend && uv run pytest tests/integration/test_package_freeze_triggers.py -q -m postgres`
Expected : PASS (huit tests). Si un `INSERT` de `build_world` est refusé pour une colonne `NOT NULL` oubliée, lui donner la valeur neutre ; ne jamais assouplir un trigger pour faire passer un test.

- [ ] **Step 6 : la chaîne complète aller-retour**

Run : `cd backend && uv run pytest tests/integration/test_graph_schema.py tests/integration/test_baseline_schema.py tests/integration/test_audit_schema.py -q -m postgres && uv run mypy src migrations`
Expected : PASS (l'`engine_at_head` monte la chaîne jusqu'à `head` puis la redescend : le `downgrade` est exercé).

- [ ] **Step 7 : commit**

```bash
git add backend/migrations backend/src/ea/db backend/tests/integration/test_package_freeze_triggers.py
git commit -m "feat(db): package_versions et triggers de gel (migration 0020)

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 3 : la traduction centrale du refus, et l'enregistreur d'audit

**Files :**
- Modify : `backend/src/ea/db/unit_of_work.py` (renommer `work` en `_write_unit`, ajouter le nouveau `work`, `frozen_refusal_of`, `set_refusal_recorder`)
- Modify : `backend/src/ea/api/errors.py` (deux lignes de `_STATUS`, import)
- Test : `backend/tests/integration/test_frozen_refusal_translation.py`
- Test : `backend/tests/unit/test_frozen_refusal_parsing.py`

**Interfaces :**
- Consumes : `FrozenRefusal`, `PackageFrozenError` (tâche 1) ; SQLSTATE `EA001` et son `DETAIL` (tâche 2).
- Produces : `frozen_refusal_of(error: DBAPIError) -> FrozenRefusal | None`, `set_refusal_recorder(recorder: RefusalRecorder | None) -> None`, `RefusalRecorder = Callable[[FrozenRefusal], Awaitable[None]]`, `FROZEN_SQLSTATE = "EA001"`. `work()` lève `PackageFrozenError` ; après le rollback, appelle l'enregistreur (une exception de l'enregistreur est journalisée et avalée).

- [ ] **Step 1 : tests unitaires de l'analyse du refus**

`backend/tests/unit/test_frozen_refusal_parsing.py` :

```python
"""Reading the database's refusal of a write into a published subtree."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.exc import DBAPIError

from ea.db.unit_of_work import frozen_refusal_of


def an_error(sqlstate: str | None, detail: str | None) -> DBAPIError:
    cause = SimpleNamespace(sqlstate=sqlstate, detail=detail)
    orig = Exception("wrapped")
    orig.__cause__ = cause  # type: ignore[assignment]
    return DBAPIError("statement", {}, orig)


def test_the_detail_is_read_into_ids_a_version_a_table_and_an_operation() -> None:
    package = uuid4()
    refusal = frozen_refusal_of(an_error("EA001", f"{package}|v1.2.0|elements|UPDATE"))
    assert refusal is not None
    assert (refusal.package_id, refusal.version, refusal.table, refusal.operation) == (
        package,
        "v1.2.0",
        "elements",
        "UPDATE",
    )


def test_any_other_error_is_not_ours() -> None:
    assert frozen_refusal_of(an_error("23505", "dup")) is None
    assert frozen_refusal_of(an_error(None, None)) is None


def test_a_refusal_whose_detail_does_not_read_is_still_a_refusal() -> None:
    for detail in (None, "", "not-a-uuid|v1|t", "a|b"):
        refusal = frozen_refusal_of(an_error("EA001", detail))
        assert refusal is not None
        assert refusal.package_id is None
```

- [ ] **Step 2 : échec attendu**

Run : `cd backend && uv run pytest tests/unit/test_frozen_refusal_parsing.py -q`
Expected : FAIL — `cannot import name 'frozen_refusal_of'`.

- [ ] **Step 3 : implémenter la traduction**

Dans `backend/src/ea/db/unit_of_work.py` :

1. Ajouter aux imports : `from collections.abc import Awaitable, Callable` (compléter l'import existant de `collections.abc` s'il y en a un), `from uuid import UUID`, et `from ea.domain.errors import PackageFrozenError` (compléter l'import de `ea.domain.errors` existant), `from ea.domain.package_versions import FrozenRefusal`.
2. Ajouter, sous les constantes du module :

```python
#: The SQLSTATE the guard triggers of migration 0020 raise (docs/adr/0074).
FROZEN_SQLSTATE: Final = "EA001"

type RefusalRecorder = Callable[[FrozenRefusal], Awaitable[None]]

#: One recorder per process, registered by `create_app` (a list so that no `global` is needed).
_RECORDER: list[RefusalRecorder | None] = [None]


def set_refusal_recorder(recorder: RefusalRecorder | None) -> None:
    """Register what `work()` calls, after the rollback, when a published subtree refused a write."""
    _RECORDER[0] = recorder


def frozen_refusal_of(error: DBAPIError) -> FrozenRefusal | None:
    """The refusal this database error means, or `None` for any other error.

    A detail that does not read still answers a refusal with every field empty: the
    write was refused, and an unreadable reason must not turn into a permitted one.
    """
    cause = getattr(error.orig, "__cause__", None)
    if getattr(cause, "sqlstate", None) != FROZEN_SQLSTATE:
        return None
    parts = (getattr(cause, "detail", None) or "").split("|")
    try:
        return FrozenRefusal(UUID(parts[0]), parts[1], parts[2], parts[3])
    except (IndexError, ValueError):
        return FrozenRefusal(None, None, None, None)
```

3. Renommer la fonction `work` existante en `_write_unit` (garder son décorateur, sa docstring et son corps), puis ajouter après elle :

```python
@asynccontextmanager
async def work(
    sessions: async_sessionmaker[AsyncSession], *, alone: bool = False
) -> AsyncIterator[AsyncSession]:
    """The unit of work of one repository *write* — and the one place that reads the database's
    refusal of a write into a published subtree (docs/adr/0074).

    The trigger's `EA001` becomes a `PackageFrozenError`. The write has been rolled back
    when the recorder is called, so the trace of the refusal is written in a transaction of
    its own and cannot be lost with it; a recorder that fails is logged, never allowed to
    hide the refusal.
    """
    try:
        async with _write_unit(sessions, alone=alone) as session:
            yield session
    except DBAPIError as error:
        refusal = frozen_refusal_of(error)
        if refusal is None:
            raise
        recorder = _RECORDER[0]
        if recorder is not None:
            try:
                await recorder(refusal)
            except Exception:  # noqa: BLE001 - the refusal must reach the caller whatever happens
                logger.exception(
                    "frozen refusal not recorded", extra={"action": "frozen_refusal_unrecorded"}
                )
        what = (
            f"package {refusal.package_id} is published as {refusal.version}"
            if refusal.package_id is not None
            else "a published package"
        )
        msg = f"{what}: an approver must reopen it first"
        raise PackageFrozenError(msg, refusal) from error
```

Les appelants existants de `work(` n'ont rien à changer.

- [ ] **Step 4 : la table d'erreurs de l'API**

Dans `backend/src/ea/api/errors.py`, importer `AdrRequiredError`, `InvalidPublicationError`, `PackageAlreadyFrozenError`, `PackageFrozenError`, `PackageNotFrozenError` avec les autres erreurs du domaine et ajouter dans `_STATUS`, près de `PackageNotEmptyError` :

```python
    PackageFrozenError: (status.HTTP_409_CONFLICT, "package_frozen"),
    PackageAlreadyFrozenError: (status.HTTP_409_CONFLICT, "package_already_frozen"),
    PackageNotFrozenError: (status.HTTP_409_CONFLICT, "package_not_frozen"),
    AdrRequiredError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "adr_required"),
    InvalidPublicationError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_publication"),
```

- [ ] **Step 5 : test d'intégration de bout en bout de la traduction**

`backend/tests/integration/test_frozen_refusal_translation.py` :

```python
"""The database's refusal reaches the caller as a typed error, and leaves a trace."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import set_refusal_recorder, work
from ea.domain.errors import PackageFrozenError
from ea.domain.package_versions import FrozenRefusal
from tests.integration.test_package_freeze_triggers import (
    a_package,
    an_element,
    publish,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _no_recorder_left_behind():  # type: ignore[no-untyped-def]
    yield
    set_refusal_recorder(None)


async def test_a_refused_write_becomes_a_typed_error_and_is_recorded_once(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root, "v2.0.0")
    seen: list[FrozenRefusal] = []

    async def recorder(refusal: FrozenRefusal) -> None:
        seen.append(refusal)

    set_refusal_recorder(recorder)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)

    with pytest.raises(PackageFrozenError) as raised:
        async with work(sessions) as session:
            await session.execute(
                text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": element}
            )

    assert raised.value.refusal == seen[0]
    assert (seen[0].package_id, seen[0].version, seen[0].table, seen[0].operation) == (
        root,
        "v2.0.0",
        "elements",
        "UPDATE",
    )
    assert len(seen) == 1
    assert "inside" not in str(raised.value)


async def test_a_recorder_that_fails_never_hides_the_refusal(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root)

    async def broken(refusal: FrozenRefusal) -> None:
        raise RuntimeError(str(uuid4()))

    set_refusal_recorder(broken)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    with pytest.raises(PackageFrozenError):
        async with work(sessions) as session:
            await session.execute(
                text("DELETE FROM elements WHERE id = :id"), {"id": element}
            )
```

- [ ] **Step 6 : lancer**

Run : `cd backend && uv run pytest tests/unit/test_frozen_refusal_parsing.py tests/integration/test_frozen_refusal_translation.py tests/unit/test_one_way_to_open_a_unit.py -q -m "postgres or not postgres" && uv run mypy src`
Expected : PASS.

- [ ] **Step 7 : commit**

```bash
git add backend/src/ea/db/unit_of_work.py backend/src/ea/api/errors.py backend/tests
git commit -m "feat(db): le refus de gel devient PackageFrozenError, traduit une seule fois

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 4 : les actions et les vues d'audit

**Files :**
- Modify : `backend/src/ea/domain/audit.py` (`AuditAction`, une vue)
- Test : `backend/tests/unit/test_package_version_audit.py`

**Interfaces :**
- Consumes : `PackageVersion`, `digest` (tâche 1).
- Produces : `AuditAction.PACKAGE_PUBLISHED = "package.published"`, `PACKAGE_REOPENED = "package.reopened"`, `PACKAGE_WRITE_REFUSED_FROZEN = "package.write_refused_frozen"` ; `package_version_view(version) -> dict[str, Any]`.

- [ ] **Step 1 : test**

`backend/tests/unit/test_package_version_audit.py` :

```python
"""What the trail says of a publication: ids, counts and digests, never a text."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ea.domain.audit import AuditAction, EntityKind, package_version_view
from ea.domain.package_versions import Bump, PackageVersion, digest

NOW = datetime(2026, 10, 12, tzinfo=UTC)


def a_version(**overrides: object) -> PackageVersion:
    fields: dict[str, object] = {
        "id": uuid4(),
        "package_id": uuid4(),
        "version": "v1.0.0",
        "bump": Bump.MAJOR,
        "notes": "Retrait de STD-APP-002 — voir l'ADR",
        "adr": "ADR-0042",
        "baseline_id": uuid4(),
        "element_count": 3,
        "relationship_count": 2,
        "box_count": 1,
        "published_by_sub": "approver-1",
        "published_by": "approver",
        "published_at": NOW,
    }
    return PackageVersion(**{**fields, **overrides})  # type: ignore[arg-type]


def test_the_three_actions_are_about_a_package() -> None:
    for action in (
        AuditAction.PACKAGE_PUBLISHED,
        AuditAction.PACKAGE_REOPENED,
        AuditAction.PACKAGE_WRITE_REFUSED_FROZEN,
    ):
        assert action.kind is EntityKind.PACKAGE


def test_the_view_keeps_digests_and_never_the_texts() -> None:
    version = a_version(reopen_reason="coquille dans le titre")
    view = package_version_view(version)
    assert view["notes_sha256"] == digest(version.notes)
    assert view["adr_sha256"] == digest("ADR-0042")
    assert view["reopen_reason_sha256"] == digest("coquille dans le titre")
    flat = repr(view)
    assert "STD-APP-002" not in flat
    assert "coquille" not in flat
    assert view["baseline_id"] == str(version.baseline_id)
    assert (view["elements"], view["relationships"], view["boxes"]) == (3, 2, 1)


def test_a_version_with_no_adr_or_reason_says_none() -> None:
    view = package_version_view(a_version(adr=None))
    assert view["adr_sha256"] is None
    assert view["reopen_reason_sha256"] is None
```

- [ ] **Step 2 : échec** — Run : `cd backend && uv run pytest tests/unit/test_package_version_audit.py -q` ; Expected : FAIL (`PACKAGE_PUBLISHED` inconnu).

- [ ] **Step 3 : implémenter**

Dans `AuditAction`, après `PACKAGE_ELEMENTS_MOVED` :

```python
    PACKAGE_PUBLISHED = "package.published"
    PACKAGE_REOPENED = "package.reopened"
    PACKAGE_WRITE_REFUSED_FROZEN = "package.write_refused_frozen"
```

Sous `package_view` :

```python
def package_version_view(version: PackageVersion) -> dict[str, Any]:
    """A publication as the trail keeps it: ids, counts and SHA-256 digests (docs/adr/0046)."""
    return {
        "id": str(version.id),
        "package_id": str(version.package_id),
        "version": version.version,
        "bump": version.bump.value,
        "baseline_id": str(version.baseline_id),
        "elements": version.element_count,
        "relationships": version.relationship_count,
        "boxes": version.box_count,
        "notes_sha256": digest(version.notes),
        "adr_sha256": digest(version.adr) if version.adr else None,
        "reopen_reason_sha256": digest(version.reopen_reason) if version.reopen_reason else None,
    }
```

avec, dans les imports de `domain/audit.py`, `from ea.domain.package_versions import PackageVersion, digest` (sous `TYPE_CHECKING` pour `PackageVersion` si l'import crée un cycle ; `digest` est importé pour de bon).

- [ ] **Step 4 : vérifier** — Run : `cd backend && uv run pytest tests/unit/test_package_version_audit.py tests/unit/test_audit_domain.py -q && uv run mypy src` ; Expected : PASS. (`test_audit_registry.py` et `test_audit_coverage.py` peuvent encore échouer : elles exigent un producteur de chaque action ; elles sont traitées à la tâche 7.)

- [ ] **Step 5 : commit**

```bash
git add backend/src/ea/domain/audit.py backend/tests/unit/test_package_version_audit.py
git commit -m "feat(audit): publication, réouverture et refus de gel dans le journal

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 5 : la baseline d'un sous-arbre

**Files :**
- Modify : `backend/src/ea/domain/baselines.py` (`Baseline`, `with_counts`, `baseline_view`)
- Modify : `backend/src/ea/repositories/baseline_store.py` (`_baseline`, `capture_scope`, `scope_size`)
- Test : `backend/tests/integration/test_baseline_scope.py`
- Test : `backend/tests/unit/test_baselines_domain.py` (mettre à jour l'attendu de `baseline_view` si nécessaire)

**Interfaces :**
- Consumes : `BaselineRecord.scope_*` (tâche 2).
- Produces : `Baseline.scope_package_id: UUID | None`, `Baseline.scope_path: str | None` ; `capture_scope(session, baseline, scope, *, package_id, path) -> Baseline` et `scope_size(session, scope) -> ModelSize` dans `repositories/baseline_store.py`.

- [ ] **Step 1 : test d'intégration**

`backend/tests/integration/test_baseline_scope.py` :

```python
"""A baseline of one subtree copies that subtree and nothing else (docs/adr/0074)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.domain.baselines import Baseline
from ea.repositories.baseline_store import capture_scope, scope_size
from tests.integration.test_package_freeze_triggers import (
    AT,
    a_diagram,
    a_package,
    an_element,
    insert,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def count(engine: AsyncEngine, table: str, baseline_id: UUID) -> int:
    async with engine.connect() as connection:
        return int(
            (
                await connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE baseline_id = :b"),  # noqa: S608  # nosec B608
                    {"b": baseline_id},
                )
            ).scalar_one()
        )


async def test_only_the_subtree_its_links_and_its_diagrams_are_copied(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
        a = await an_element(connection, "a", root)
        b = await an_element(connection, "b", child)
        outside = await an_element(connection, "outside", None)
        for source, target in ((a, b), (a, outside)):
            await insert(
                connection,
                "relationships",
                id=uuid4(),
                relationship_type="association",
                source_id=source,
                target_id=target,
                source_type="node",
                target_type="node",
                created_at=AT,
            )
        diagram = await a_diagram(connection, "Vue", child)
        await insert(connection, "diagram_nodes", diagram_id=diagram, element_id=a, x=0, y=0)
        free = await a_diagram(connection, "Libre", None)
        await insert(connection, "diagram_nodes", diagram_id=free, element_id=outside, x=0, y=0)

    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    baseline = Baseline.create(
        name="Finance v1.0.0",
        description="",
        created_by_sub="s",
        created_by="n",
        now=datetime(2026, 10, 12, tzinfo=UTC),
    )
    async with sessions.begin() as session:
        size = await scope_size(session, frozenset({root, child}))
        stored = await capture_scope(
            session, baseline, frozenset({root, child}), package_id=root, path="Finance"
        )

    assert (size.elements, size.relationships, size.boxes, size.diagrams) == (2, 1, 1, 1)
    assert (stored.element_count, stored.relationship_count, stored.box_count) == (2, 1, 1)
    assert (stored.scope_package_id, stored.scope_path) == (root, "Finance")
    assert await count(engine_at_head, "baseline_elements", baseline.id) == 2
    assert await count(engine_at_head, "baseline_relationships", baseline.id) == 1
    assert await count(engine_at_head, "baseline_boxes", baseline.id) == 1


async def test_an_empty_subtree_gives_an_empty_baseline(engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Vide", None)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)
    baseline = Baseline.create(
        name="Vide v1.0.0",
        description="",
        created_by_sub="s",
        created_by="n",
        now=datetime(2026, 10, 12, tzinfo=UTC),
    )
    async with sessions.begin() as session:
        stored = await capture_scope(
            session, baseline, frozenset({root}), package_id=root, path="Vide"
        )
    assert (stored.element_count, stored.relationship_count, stored.box_count) == (0, 0, 0)
```

- [ ] **Step 2 : échec** — Run : `cd backend && uv run pytest tests/integration/test_baseline_scope.py -q -m postgres` ; Expected : FAIL (`cannot import name 'capture_scope'`).

- [ ] **Step 3 : le domaine**

Dans `backend/src/ea/domain/baselines.py`, ajouter à `Baseline`, après `box_count: int = 0` :

```python
    #: Set when the baseline copies one package's subtree rather than the whole model.
    scope_package_id: UUID | None = None
    scope_path: str | None = None
```

remplacer le corps de `with_counts` par :

```python
        return replace(
            self, element_count=elements, relationship_count=relationships, box_count=boxes
        )
```

(importer `replace` depuis `dataclasses` à côté de `dataclass`), et ajouter `"scope_path": baseline.scope_path,` à la fin du dictionnaire de `baseline_view`.

- [ ] **Step 4 : le dépôt**

Dans `backend/src/ea/repositories/baseline_store.py` :

1. `_baseline(row, …)` : ajouter `scope_package_id=row.scope_package_id, scope_path=row.scope_path,` aux arguments de `Baseline(...)`.
2. Dans la création d'un `BaselineRecord` de `create`, ne rien changer (la baseline complète reste sans périmètre).
3. Ajouter, à la suite des fonctions de module (avant `class PostgresBaselineRepository`), en important `and_` de `sqlalchemy` et `DiagramRecord` de `ea.db.models.diagram` si absents :

```python
def _scope_filters(scope: Sequence[UUID]) -> tuple[Any, Any, Any]:
    """The three WHERE clauses of a subtree: its elements, the links with both ends in it,
    and the boxes of the diagrams filed in it."""
    in_scope = ElementRecord.package_id.in_(scope)
    members = select(ElementRecord.id).where(in_scope)
    linked = and_(
        RelationshipRecord.source_id.in_(members), RelationshipRecord.target_id.in_(members)
    )
    boxed = DiagramNodeRecord.diagram_id.in_(
        select(DiagramRecord.id).where(DiagramRecord.package_id.in_(scope))
    )
    return in_scope, linked, boxed


async def scope_size(session: AsyncSession, scope: frozenset[UUID]) -> ModelSize:
    """What a subtree holds, counted by the server before anything is copied.

    # ponytail: counts only, bytes are not measured — the count ceilings bound the copy;
    # measure the text too if a subtree of huge documents ever appears.
    """
    in_scope, linked, boxed = _scope_filters(sorted(scope))

    async def count(table: Any, clause: Any) -> int:
        return int(await session.scalar(select(func.count()).select_from(table).where(clause)) or 0)

    return ModelSize(
        elements=await count(ElementRecord, in_scope),
        relationships=await count(RelationshipRecord, linked),
        boxes=await count(DiagramNodeRecord, boxed),
        diagrams=await count(DiagramRecord, DiagramRecord.package_id.in_(sorted(scope))),
    )


async def capture_scope(
    session: AsyncSession,
    baseline: Baseline,
    scope: frozenset[UUID],
    *,
    package_id: UUID,
    path: str,
) -> Baseline:
    """Copy one subtree under this baseline, in the caller's transaction.

    The caller holds the locks that keep the subtree still (docs/adr/0074): this copies
    what is committed, with the same three `INSERT … SELECT` as a whole-model capture.
    """
    id_column = literal(baseline.id, Uuid())
    in_scope, linked, boxed = _scope_filters(sorted(scope))
    paths = _paths()
    session.add(
        BaselineRecord(
            id=baseline.id,
            name=baseline.name,
            description=baseline.description,
            created_by_sub=baseline.created_by_sub,
            created_by=baseline.created_by,
            created_at=baseline.created_at,
            scope_package_id=package_id,
            scope_path=path,
        )
    )
    try:
        await session.flush()
    except IntegrityError as error:
        if constraint_of(error) != NAME_TAKEN:
            raise
        msg = f"a baseline is already named {baseline.name!r}"
        raise DuplicateBaselineError(msg) from error
    await session.execute(
        insert(BaselineElementRecord).from_select(
            ["baseline_id", *_ELEMENT_COLUMNS, "package_path"],
            select(
                id_column,
                *(getattr(ElementRecord, name) for name in _ELEMENT_COLUMNS),
                paths.c.path,
            )
            .outerjoin(paths, paths.c.id == ElementRecord.package_id)
            .where(in_scope),
        )
    )
    for target, source, columns, clause in (
        (BaselineRelationshipRecord, RelationshipRecord, _RELATIONSHIP_COLUMNS, linked),
        (BaselineBoxRecord, DiagramNodeRecord, _BOX_COLUMNS, boxed),
    ):
        await session.execute(
            insert(target).from_select(
                ["baseline_id", *columns],
                select(id_column, *(getattr(source, name) for name in columns)).where(clause),
            )
        )
    stored = baseline.with_counts(*await _counts(session, baseline.id))
    return replace(stored, scope_package_id=package_id, scope_path=path)
```

(importer `replace` de `dataclasses`.)

- [ ] **Step 5 : vérifier**

Run : `cd backend && uv run pytest tests/integration/test_baseline_scope.py tests/integration/test_baseline_store.py tests/unit/test_baselines_domain.py tests/unit/test_baseline_service.py -q -m "postgres or not postgres" && uv run mypy src`
Expected : PASS. Si `test_baselines_domain.py` compare exactement le dictionnaire de `baseline_view`, y ajouter `"scope_path": None`.

- [ ] **Step 6 : commit**

```bash
git add backend/src/ea/domain/baselines.py backend/src/ea/repositories/baseline_store.py backend/tests
git commit -m "feat(baselines): la copie d'un sous-arbre de packages

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 6 : publier et rouvrir côté dépôt

**Files :**
- Modify : `backend/src/ea/domain/ports.py` (`PackageRepository`)
- Modify : `backend/src/ea/repositories/package_store.py`
- Test : `backend/tests/integration/test_package_publication.py`

**Interfaces :**
- Consumes : `capture_scope`, `scope_size`, `refuse_oversize`, `BaselineLimits` (tâche 5 et existants) ; `effective_freezes`, `next_version`, `Publication`, `PackageVersion` (tâche 1).
- Produces, sur `PostgresPackageRepository` (et sur le protocole) :

```python
async def versions(self, package_id: UUID) -> tuple[PackageVersion, ...]
async def in_force(self) -> dict[UUID, str]
async def preview_publication(self, package_id: UUID, *, bump: Bump) -> PublicationPreview
async def publish(
    self, package_id: UUID, *, publication: Publication,
    by_sub: str, by: str, now: datetime, audit: AuditOf[PackageVersion],
) -> PackageVersion
async def reopen(
    self, package_id: UUID, *, reason: str, by_sub: str, by: str, now: datetime,
    audit: AuditOf[PackageVersion],
) -> PackageVersion
```

`PostgresPackageRepository.__init__` gagne `limits: BaselineLimits = BaselineLimits()`.

- [ ] **Step 1 : tests d'intégration**

`backend/tests/integration/test_package_publication.py` :

```python
"""Publishing and reopening, against a real PostgreSQL (docs/adr/0074)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import work
from ea.domain.audit import AuditAction, AuditEvent, AuditChannel, EntityKind, package_version_view
from ea.domain.errors import (
    BaselineTooLargeError,
    PackageAlreadyFrozenError,
    PackageFrozenError,
    PackageNotFoundError,
    PackageNotFrozenError,
)
from ea.domain.baselines import BaselineLimits
from ea.domain.package_versions import Bump, PackageVersion, publication
from ea.repositories.package_store import PostgresPackageRepository
from tests.integration.test_package_freeze_triggers import AT, a_package, an_element

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


def an_event(action: AuditAction, entity_id: UUID, version: PackageVersion) -> AuditEvent:
    return AuditEvent(
        id=uuid4(),
        at=NOW,
        actor_sub="approver-1",
        actor_name="approver",
        action=action,
        entity_kind=EntityKind.PACKAGE,
        entity_id=entity_id,
        before=None,
        after=package_version_view(version),
        request_id="r-1",
        channel=AuditChannel.HTTP,
        correlation=None,
    )


async def publish_it(
    repo: PostgresPackageRepository,
    package_id: UUID,
    *,
    bump: Bump = Bump.MINOR,
    notes: str = "notes",
    adr: str | None = None,
) -> PackageVersion:
    return await repo.publish(
        package_id,
        publication=publication(bump=bump, notes=notes, adr=adr),
        by_sub="approver-1",
        by="approver",
        now=NOW,
        audit=lambda stored: [an_event(AuditAction.PACKAGE_PUBLISHED, package_id, stored)],
    )


async def reopen_it(repo: PostgresPackageRepository, package_id: UUID) -> PackageVersion:
    return await repo.reopen(
        package_id,
        reason="coquille",
        by_sub="approver-1",
        by="approver",
        now=NOW,
        audit=lambda stored: [an_event(AuditAction.PACKAGE_REOPENED, package_id, stored)],
    )


@pytest.fixture
def repo(engine_at_head: AsyncEngine) -> PostgresPackageRepository:
    return PostgresPackageRepository(async_sessionmaker(engine_at_head, expire_on_commit=False))


async def test_publishing_freezes_the_subtree_takes_a_baseline_and_writes_one_event(
    engine_at_head: AsyncEngine, repo: PostgresPackageRepository
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
        inside = await an_element(connection, "inside", child)

    version = await publish_it(repo, root, notes='« un »\n"deux" — é')

    assert version.version == "v1.0.0"
    assert version.notes == '« un »\n"deux" — é'
    assert version.element_count == 1
    assert version.publish_audit_event_id is not None
    assert await repo.in_force() == {root: "v1.0.0"}
    async with engine_at_head.connect() as connection:
        scope = (
            await connection.execute(
                text("SELECT scope_package_id, scope_path FROM baselines WHERE id = :b"),
                {"b": version.baseline_id},
            )
        ).one()
        events = (
            await connection.execute(
                text("SELECT id, action FROM audit_events WHERE entity_id = :p"), {"p": root}
            )
        ).all()
    assert tuple(scope) == (root, "Finance")
    assert [(e[0], e[1]) for e in events] == [
        (version.publish_audit_event_id, "package.published")
    ]
    with pytest.raises(PackageFrozenError):
        async with work(async_sessionmaker(engine_at_head)) as session:
            await session.execute(
                text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": inside}
            )


async def test_an_empty_package_can_be_published(repo: PostgresPackageRepository, engine_at_head: AsyncEngine) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Vide", None)
    version = await publish_it(repo, root)
    assert (version.element_count, version.relationship_count, version.box_count) == (0, 0, 0)
    assert await repo.in_force() == {root: "v1.0.0"}


async def test_the_next_version_follows_the_latest_even_after_a_reopening(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    await publish_it(repo, root)
    closed = await reopen_it(repo, root)
    assert closed.reopened_at is not None and closed.reopen_audit_event_id is not None
    assert await repo.in_force() == {}
    second = await publish_it(repo, root, bump=Bump.MAJOR, adr="ADR-0042")
    assert second.version == "v2.0.0"
    assert [v.version for v in await repo.versions(root)] == [second.version, "v1.0.0"]


async def test_a_package_already_published_or_under_one_cannot_be_published(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
    await publish_it(repo, root)
    for target in (root, child):
        with pytest.raises(PackageAlreadyFrozenError):
            await publish_it(repo, target)
        with pytest.raises(PackageAlreadyFrozenError):
            await repo.preview_publication(target, bump=Bump.MINOR)


async def test_publishing_a_parent_whose_child_is_already_published_is_allowed(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
    await publish_it(repo, child)
    await publish_it(repo, root)
    assert await repo.in_force() == {root: "v1.0.0", child: "v1.0.0"}
    await reopen_it(repo, root)
    assert await repo.in_force() == {child: "v1.0.0"}


async def test_reopening_a_package_under_a_published_ancestor_still_leaves_it_frozen(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        child = await a_package(connection, "Budget", root)
        inside = await an_element(connection, "inside", child)
    await publish_it(repo, child)
    await publish_it(repo, root)
    await reopen_it(repo, child)
    with pytest.raises(PackageFrozenError) as raised:
        async with work(async_sessionmaker(engine_at_head)) as session:
            await session.execute(
                text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": inside}
            )
    assert raised.value.refusal is not None and raised.value.refusal.package_id == root


async def test_reopening_what_is_not_published_or_does_not_exist(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    with pytest.raises(PackageNotFrozenError):
        await reopen_it(repo, root)
    with pytest.raises(PackageNotFoundError):
        await reopen_it(repo, uuid4())
    with pytest.raises(PackageNotFoundError):
        await publish_it(repo, uuid4())


async def test_a_subtree_past_the_ceiling_is_refused_whole_and_nothing_is_written(
    engine_at_head: AsyncEngine,
) -> None:
    repo = PostgresPackageRepository(
        async_sessionmaker(engine_at_head, expire_on_commit=False),
        limits=BaselineLimits(elements=1),
    )
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        await an_element(connection, "one", root)
        await an_element(connection, "two", root)
    with pytest.raises(BaselineTooLargeError):
        await publish_it(repo, root)
    async with engine_at_head.connect() as connection:
        left = (
            await connection.execute(text("SELECT (SELECT count(*) FROM baselines) + (SELECT count(*) FROM package_versions)"))
        ).scalar_one()
    assert left == 0


async def test_two_simultaneous_publications_of_one_package_let_exactly_one_through(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    results = await asyncio.gather(
        publish_it(repo, root), publish_it(repo, root), return_exceptions=True
    )
    published = [r for r in results if isinstance(r, PackageVersion)]
    refused = [r for r in results if isinstance(r, PackageAlreadyFrozenError)]
    assert (len(published), len(refused)) == (1, 1), results


async def test_a_write_racing_a_publication_is_either_in_the_baseline_or_refused(
    repo: PostgresPackageRepository, engine_at_head: AsyncEngine
) -> None:
    """The freeze and the copy see the same state: no write can slip in between."""
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        inside = await an_element(connection, "inside", root)
    sessions = async_sessionmaker(engine_at_head, expire_on_commit=False)

    async def write() -> str:
        try:
            async with work(sessions) as session:
                await session.execute(
                    text("UPDATE elements SET description = 'late' WHERE id = :id"),
                    {"id": inside},
                )
        except PackageFrozenError:
            return "refused"
        return "written"

    outcome, version = await asyncio.gather(write(), publish_it(repo, root))
    async with engine_at_head.connect() as connection:
        copied = (
            await connection.execute(
                text("SELECT description FROM baseline_elements WHERE baseline_id = :b"),
                {"b": version.baseline_id},
            )
        ).scalar_one()
    if outcome == "written":
        assert copied == "late"  # the write committed first, so the baseline holds it
    else:
        assert copied == ""
```

- [ ] **Step 2 : échec** — Run : `make pg-up && cd backend && uv run pytest tests/integration/test_package_publication.py -q -m postgres` ; Expected : FAIL (`PostgresPackageRepository` n'a pas de `publish`).

- [ ] **Step 3 : le protocole**

Dans `backend/src/ea/domain/ports.py`, dans `class PackageRepository(Protocol)`, ajouter (avec les imports `Bump`, `Publication`, `PackageVersion`, `PublicationPreview` de `ea.domain.package_versions` sous `TYPE_CHECKING`) :

```python
    async def versions(self, package_id: UUID) -> tuple[PackageVersion, ...]:
        """Every version this package was published under, the latest first."""
        ...

    async def in_force(self) -> dict[UUID, str]:
        """`{package id: version}` for each package that is published right now."""
        ...

    async def preview_publication(self, package_id: UUID, *, bump: Bump) -> PublicationPreview:
        """What publishing would do now. Raises `PackageNotFoundError`,
        `PackageAlreadyFrozenError` (itself or an ancestor is published) or
        `BaselineTooLargeError`; writes nothing."""
        ...

    async def publish(
        self,
        package_id: UUID,
        *,
        publication: Publication,
        by_sub: str,
        by: str,
        now: datetime,
        audit: AuditOf[PackageVersion],
    ) -> PackageVersion:
        """Freeze the subtree: its baseline, its version and its audit event in one transaction."""
        ...

    async def reopen(
        self,
        package_id: UUID,
        *,
        reason: str,
        by_sub: str,
        by: str,
        now: datetime,
        audit: AuditOf[PackageVersion],
    ) -> PackageVersion:
        """Close the version in force. Raises `PackageNotFoundError` or `PackageNotFrozenError`."""
        ...
```

- [ ] **Step 4 : l'implémentation**

Dans `backend/src/ea/repositories/package_store.py` : ajouter aux imports `from datetime import datetime`, `from uuid import uuid4` (hors `TYPE_CHECKING`), `from ea.db.models.package_version import PackageVersionRecord`, `from ea.domain.baselines import Baseline, BaselineLimits, refuse_oversize`, `from ea.domain.errors import PackageAlreadyFrozenError, PackageNotFrozenError` (compléter l'import existant), `from ea.domain.package_versions import Bump, PackageVersion, Publication, PublicationPreview, effective_freezes, next_version`, `from ea.repositories.baseline_store import capture_scope, scope_size`, `path_of` depuis `ea.domain.packages`.

Ajouter les constantes :

```python
#: The tables a published subtree's triggers guard; a publication waits for the writers of
#: all of them, and stops the new ones, so the copy and the freeze see the same state.
_GUARDED_TABLES: Final = (
    "elements",
    "relationships",
    "element_documents",
    "element_stereotypes",
    "diagrams",
    "diagram_nodes",
    "diagram_objects",
    "diagram_edges",
    "packages",
)
# A constant list of our own table names, nothing a caller wrote.
_LOCK_GUARDED: Final = "LOCK TABLE " + ", ".join(_GUARDED_TABLES) + " IN SHARE MODE"  # nosec B608
_WAIT_FOR_WRITERS: Final = "SET LOCAL lock_timeout = '10s'"


def _to_version(row: PackageVersionRecord) -> PackageVersion:
    return PackageVersion(
        id=row.id,
        package_id=row.package_id,
        version=row.version,
        bump=Bump(row.bump),
        notes=row.notes,
        adr=row.adr,
        baseline_id=row.baseline_id,
        element_count=row.element_count,
        relationship_count=row.relationship_count,
        box_count=row.box_count,
        published_by_sub=row.published_by_sub,
        published_by=row.published_by,
        published_at=row.published_at,
        publish_audit_event_id=row.publish_audit_event_id,
        reopened_by_sub=row.reopened_by_sub,
        reopened_by=row.reopened_by,
        reopened_at=row.reopened_at,
        reopen_reason=row.reopen_reason,
        reopen_audit_event_id=row.reopen_audit_event_id,
    )


async def _in_force(session: AsyncSession) -> dict[UUID, str]:
    rows = await session.execute(
        select(PackageVersionRecord.package_id, PackageVersionRecord.version).where(
            PackageVersionRecord.reopened_at.is_(None)
        )
    )
    return {package_id: version for package_id, version in rows.tuples().all()}


def _subtree(package_id: UUID, tree: Mapping[UUID, Package]) -> frozenset[UUID]:
    return frozenset({package_id, *descendants_of(package_id, tree)})
```

Ajouter à `PostgresPackageRepository` (le constructeur prend `limits`) :

```python
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        limits: BaselineLimits = BaselineLimits(),  # noqa: B008 - frozen, shared on purpose
    ) -> None:
        self._sessions = sessions
        self._limits = limits

    async def in_force(self) -> dict[UUID, str]:
        async with reading(self._sessions) as session:
            return await _in_force(session)

    async def versions(self, package_id: UUID) -> tuple[PackageVersion, ...]:
        statement = (
            select(PackageVersionRecord)
            .where(PackageVersionRecord.package_id == package_id)
            .order_by(PackageVersionRecord.published_at.desc(), PackageVersionRecord.id.desc())
        )
        async with reading(self._sessions) as session:
            return tuple(_to_version(row) for row in await session.scalars(statement))

    async def _check_publishable(
        self, session: AsyncSession, package_id: UUID, tree: Mapping[UUID, Package]
    ) -> frozenset[UUID]:
        if package_id not in tree:
            msg = f"no package with id {package_id}"
            raise PackageNotFoundError(msg)
        if package_id in effective_freezes(tree, await _in_force(session)):
            msg = "this package is published, or lies under a package that is: reopen it first"
            raise PackageAlreadyFrozenError(msg)
        return _subtree(package_id, tree)

    async def preview_publication(self, package_id: UUID, *, bump: Bump) -> PublicationPreview:
        async with reading(self._sessions) as session:
            rows = await session.scalars(select(PackageRecord))
            tree = {row.id: _to_package(row) for row in rows}
            scope = await self._check_publishable(session, package_id, tree)
            size = await scope_size(session, scope)
            refuse_oversize(size, self._limits, what="the package")
            last = await session.scalar(
                select(PackageVersionRecord.version)
                .where(PackageVersionRecord.package_id == package_id)
                .order_by(PackageVersionRecord.published_at.desc(), PackageVersionRecord.id.desc())
                .limit(1)
            )
        return PublicationPreview(
            version=next_version(last, bump),
            bump=bump,
            elements=size.elements,
            relationships=size.relationships,
            boxes=size.boxes,
            diagrams=size.diagrams,
        )

    async def publish(
        self,
        package_id: UUID,
        *,
        publication: Publication,
        by_sub: str,
        by: str,
        now: datetime,
        audit: AuditOf[PackageVersion],
    ) -> PackageVersion:
        async with work(self._sessions) as session:
            await session.execute(text(_WAIT_FOR_WRITERS))
            tree = await _locked_tree(session)
            # Waits for the writers in flight, then stops the new ones until this commits: what is
            # copied below is exactly what the freeze will protect.
            await session.execute(text(_LOCK_GUARDED))
            scope = await self._check_publishable(session, package_id, tree)
            refuse_oversize(await scope_size(session, scope), self._limits, what="the package")
            last = await session.scalar(
                select(PackageVersionRecord.version)
                .where(PackageVersionRecord.package_id == package_id)
                .order_by(PackageVersionRecord.published_at.desc(), PackageVersionRecord.id.desc())
                .limit(1)
            )
            version = next_version(last, publication.bump)
            path = path_of(tree[package_id], tree)
            label = path if len(path) <= 150 else "…" + path[-149:]
            baseline = Baseline.create(
                name=f"{label} {version} [{str(package_id)[:8]}]",
                description=f"Publication {version} of {label}",
                created_by_sub=by_sub,
                created_by=by,
                now=now,
            )
            captured = await capture_scope(
                session, baseline, scope, package_id=package_id, path=path
            )
            draft = PackageVersion(
                id=uuid4(),
                package_id=package_id,
                version=version,
                bump=publication.bump,
                notes=publication.notes,
                adr=publication.adr,
                baseline_id=captured.id,
                element_count=captured.element_count,
                relationship_count=captured.relationship_count,
                box_count=captured.box_count,
                published_by_sub=by_sub,
                published_by=by,
                published_at=now,
            )
            events = events_for(audit, draft)
            stored = replace(draft, publish_audit_event_id=events[0].id)
            session.add(
                PackageVersionRecord(
                    id=stored.id,
                    package_id=stored.package_id,
                    version=stored.version,
                    bump=stored.bump.value,
                    notes=stored.notes,
                    adr=stored.adr,
                    baseline_id=stored.baseline_id,
                    element_count=stored.element_count,
                    relationship_count=stored.relationship_count,
                    box_count=stored.box_count,
                    published_by_sub=stored.published_by_sub,
                    published_by=stored.published_by,
                    published_at=stored.published_at,
                    publish_audit_event_id=events[0].id,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                # Two publications of one package: the unique index of the version in force.
                if constraint_of(error) == "uq_package_versions_in_force":
                    msg = "this package was published meanwhile: reopen it first"
                    raise PackageAlreadyFrozenError(msg) from error
                raise
            write_events(session, events)
        return stored

    async def reopen(
        self,
        package_id: UUID,
        *,
        reason: str,
        by_sub: str,
        by: str,
        now: datetime,
        audit: AuditOf[PackageVersion],
    ) -> PackageVersion:
        async with work(self._sessions) as session:
            tree = await _locked_tree(session)
            if package_id not in tree:
                msg = f"no package with id {package_id}"
                raise PackageNotFoundError(msg)
            row = await session.scalar(
                select(PackageVersionRecord)
                .where(
                    PackageVersionRecord.package_id == package_id,
                    PackageVersionRecord.reopened_at.is_(None),
                )
                .with_for_update()
            )
            if row is None:
                msg = "this package has no version in force"
                raise PackageNotFrozenError(msg)
            closed = replace(
                _to_version(row),
                reopened_by_sub=by_sub,
                reopened_by=by,
                reopened_at=now,
                reopen_reason=reason,
            )
            events = events_for(audit, closed)
            row.reopened_by_sub = by_sub
            row.reopened_by = by
            row.reopened_at = now
            row.reopen_reason = reason
            row.reopen_audit_event_id = events[0].id
            await session.flush()
            write_events(session, events)
        return replace(closed, reopen_audit_event_id=events[0].id)
```

La concurrence de deux publications : la deuxième attend le verrou d'arbre ; quand elle l'obtient, `_check_publishable` voit la version de la première (validée) et lève `PackageAlreadyFrozenError`. Le filet est l'index unique `uq_package_versions_in_force`. `_WAIT_FOR_WRITERS` et `_LOCK_GUARDED` sont des constantes sans paramètre : `text()` sans valeur liée est justifié par ce commentaire (DDL / ordre de verrou constant, comme `migrations/versions/0019`).

- [ ] **Step 5 : vérifier**

Run : `cd backend && uv run pytest tests/integration/test_package_publication.py tests/integration/test_package_store.py tests/integration/test_package_concurrency.py -q -m postgres && uv run mypy src`
Expected : PASS. (Si `test_package_concurrency.py` n'existe pas sous ce nom, lancer `uv run pytest tests/integration -q -m postgres -k package`.)

- [ ] **Step 6 : commit**

```bash
git add backend/src/ea/domain/ports.py backend/src/ea/repositories/package_store.py backend/tests/integration/test_package_publication.py
git commit -m "feat(packages): publier et rouvrir, baseline du sous-arbre comprise

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 7 : le service, l'enregistreur de refus, le câblage

**Files :**
- Modify : `backend/src/ea/services/packages.py`
- Create : `backend/src/ea/services/frozen_refusals.py`
- Modify : `backend/src/ea/main.py` (après `package_store = PostgresPackageRepository(...)`, ligne ~368)
- Modify : `backend/tests/conftest.py` (`InMemoryPackages`, un reset autouse de l'enregistreur)
- Test : `backend/tests/unit/test_package_publication_service.py`
- Test : `backend/tests/unit/test_frozen_refusal_recorder.py`

**Interfaces :**
- Consumes : tout ce qui précède.
- Produces : `PackageService.publish(package_id, *, bump, notes, adr=None, dry_run=True) -> PublishOutcome`, `PackageService.reopen(package_id, *, reason) -> PackageVersion`, `PackageService.versions(package_id) -> tuple[PackageVersion, ...]`, `_summaries()` remplit `frozen_by`, `own_version`, `actions` ; `FrozenRefusalRecorder(sessions, *, clock, per_minute=30, monotonic)` (appelable `async (FrozenRefusal) -> None`).

- [ ] **Step 1 : le double en mémoire**

Dans `backend/tests/conftest.py`, ajouter à `InMemoryPackages` (imports en tête de fichier : `Bump, PackageVersion, Publication, PublicationPreview, effective_freezes, next_version` de `ea.domain.package_versions`, `PackageAlreadyFrozenError, PackageNotFrozenError` de `ea.domain.errors`, `dataclasses.replace`) :

```python
        self.published: list[PackageVersion] = []
```

dans `__init__`, et ces méthodes :

```python
    async def in_force(self) -> dict[UUID, str]:
        return {v.package_id: v.version for v in self.published if v.in_force}

    async def versions(self, package_id: UUID) -> tuple[PackageVersion, ...]:
        return tuple(reversed([v for v in self.published if v.package_id == package_id]))

    async def preview_publication(self, package_id: UUID, *, bump: Bump) -> PublicationPreview:
        if package_id not in self.packages:
            raise PackageNotFoundError(str(package_id))
        if package_id in effective_freezes(self.packages, await self.in_force()):
            raise PackageAlreadyFrozenError("published")
        counts = await self.counts()
        scope = {package_id, *descendants_of(package_id, self.packages)}
        last = next((v.version for v in reversed(self.published) if v.package_id == package_id), None)
        return PublicationPreview(
            version=next_version(last, bump),
            bump=bump,
            elements=sum(counts.get(p, PackageCounts()).elements for p in scope),
            relationships=0,
            boxes=0,
            diagrams=sum(counts.get(p, PackageCounts()).diagrams for p in scope),
        )

    async def publish(
        self,
        package_id: UUID,
        *,
        publication: Publication,
        by_sub: str,
        by: str,
        now: datetime,
        audit: Any,
    ) -> PackageVersion:
        preview = await self.preview_publication(package_id, bump=publication.bump)
        draft = PackageVersion(
            id=uuid4(),
            package_id=package_id,
            version=preview.version,
            bump=publication.bump,
            notes=publication.notes,
            adr=publication.adr,
            baseline_id=uuid4(),
            element_count=preview.elements,
            relationship_count=0,
            box_count=0,
            published_by_sub=by_sub,
            published_by=by,
            published_at=now,
        )
        events = events_for(audit, draft)
        stored = replace(draft, publish_audit_event_id=events[0].id)
        self.published.append(stored)
        self.audit_log.extend(events)
        return stored

    async def reopen(
        self, package_id: UUID, *, reason: str, by_sub: str, by: str, now: datetime, audit: Any
    ) -> PackageVersion:
        if package_id not in self.packages:
            raise PackageNotFoundError(str(package_id))
        for index, version in enumerate(self.published):
            if version.package_id == package_id and version.in_force:
                closed = replace(
                    version,
                    reopened_by_sub=by_sub,
                    reopened_by=by,
                    reopened_at=now,
                    reopen_reason=reason,
                )
                events = events_for(audit, closed)
                closed = replace(closed, reopen_audit_event_id=events[0].id)
                self.published[index] = closed
                self.audit_log.extend(events)
                return closed
        raise PackageNotFrozenError("not published")
```

(Si `InMemoryPackages` n'importe pas déjà `events_for`, `descendants_of`, `PackageCounts`, `uuid4`, `datetime`, `Any`, les ajouter. `self.audit_log` est déjà `graph.audit_log` dans ce double.)

Ajouter aussi, près des autres fixtures autouse :

```python
@pytest.fixture(autouse=True)
def _no_frozen_refusal_recorder_left_behind() -> Iterator[None]:
    """The recorder is a process-wide registration (ea.db.unit_of_work): no test leaks one."""
    from ea.db.unit_of_work import set_refusal_recorder

    yield
    set_refusal_recorder(None)
```

- [ ] **Step 2 : tests du service (échouent)**

`backend/tests/unit/test_package_publication_service.py` :

```python
"""Publishing and reopening, as the service decides them."""

from __future__ import annotations

import pytest

from ea.domain.audit import AuditAction
from ea.domain.errors import (
    AdrRequiredError,
    InvalidPublicationError,
    NotAuthorisedError,
    PackageAlreadyFrozenError,
    PackageNotFoundError,
    PackageNotFrozenError,
)
from ea.domain.package_versions import Bump
from ea.services.caller import acting_as
from ea.services.packages import PackageService
from tests.conftest import InMemoryPackages, a_reader, an_approver, an_editor

pytestmark = pytest.mark.asyncio


async def test_the_default_is_a_dry_run_that_changes_nothing(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        outcome = await package_service.publish(
            root.package.id, bump=Bump.MINOR, notes="Première"
        )
    assert outcome.published is None
    assert outcome.preview.version == "v1.0.0"
    assert packages.published == []


async def test_publishing_for_real_writes_a_version_and_one_audit_event(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    before = len(packages.audit_log)
    with acting_as(an_approver()):
        outcome = await package_service.publish(
            root.package.id, bump=Bump.MINOR, notes="Première", dry_run=False
        )
    assert outcome.published is not None
    assert outcome.published.version == "v1.0.0"
    events = packages.audit_log[before:]
    assert [e.action for e in events] == [AuditAction.PACKAGE_PUBLISHED]
    assert outcome.published.publish_audit_event_id == events[0].id
    assert events[0].actor_sub == "approver-1"
    assert "Première" not in repr(events[0].after)


@pytest.mark.parametrize("caller", [an_editor, a_reader])
async def test_only_an_approver_publishes_or_reopens(
    package_service: PackageService, caller: object
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(caller()):  # type: ignore[operator]
        with pytest.raises(NotAuthorisedError):
            await package_service.publish(root.package.id, bump=Bump.MINOR, notes="n", dry_run=False)
        with pytest.raises(NotAuthorisedError):
            await package_service.reopen(root.package.id, reason="r")


async def test_the_words_are_checked_before_anything_is_read_or_written(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        with pytest.raises(InvalidPublicationError):
            await package_service.publish(root.package.id, bump=Bump.MINOR, notes=" ")
        with pytest.raises(AdrRequiredError):
            await package_service.publish(root.package.id, bump=Bump.MAJOR, notes="n")
        with pytest.raises(PackageNotFoundError):
            from uuid import uuid4

            await package_service.publish(uuid4(), bump=Bump.MINOR, notes="n")
    assert packages.published == []


async def test_reopening_closes_the_version_and_leaves_its_own_event(
    package_service: PackageService, packages: InMemoryPackages
) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        await package_service.publish(root.package.id, bump=Bump.MINOR, notes="n", dry_run=False)
        closed = await package_service.reopen(root.package.id, reason="coquille")
        with pytest.raises(PackageNotFrozenError):
            await package_service.reopen(root.package.id, reason="encore")
    assert closed.reopen_reason == "coquille"
    assert packages.audit_log[-1].action is AuditAction.PACKAGE_REOPENED
    assert "coquille" not in repr(packages.audit_log[-1].after)


async def test_a_package_under_a_published_one_is_read_as_frozen_and_offers_nothing(
    package_service: PackageService,
) -> None:
    root = await package_service.create(name="Finance")
    child = await package_service.create(name="Budget", parent_id=root.package.id)
    other = await package_service.create(name="RH")
    with acting_as(an_approver()):
        await package_service.publish(root.package.id, bump=Bump.MINOR, notes="n", dry_run=False)
        read_child = await package_service.get(child.package.id)
        read_root = await package_service.get(root.package.id)
        read_other = await package_service.get(other.package.id)
        with pytest.raises(PackageAlreadyFrozenError):
            await package_service.publish(child.package.id, bump=Bump.MINOR, notes="n")
    assert read_root.own_version == "v1.0.0" and read_root.actions == ("reopen",)
    assert read_child.own_version is None and read_child.actions == ()
    assert read_child.frozen_by is not None and read_child.frozen_by.path == "Finance"
    assert read_other.frozen_by is None and read_other.actions == ("publish",)


async def test_versions_are_listed_for_any_reader(package_service: PackageService) -> None:
    root = await package_service.create(name="Finance")
    with acting_as(an_approver()):
        await package_service.publish(root.package.id, bump=Bump.MINOR, notes="n", dry_run=False)
    with acting_as(a_reader()):
        assert [v.version for v in await package_service.versions(root.package.id)] == ["v1.0.0"]
```

Run : `cd backend && uv run pytest tests/unit/test_package_publication_service.py -q` ; Expected : FAIL (`PackageService` n'a pas de `publish`).

- [ ] **Step 3 : le service**

Dans `backend/src/ea/services/packages.py` : importer `require_approver` (avec `logged, require_caller, require_editor`), `from ea.domain.audit import AuditAction, package_version_view, package_view`, `from ea.domain.package_versions import Bump, PackageVersion, PublishOutcome, allowed_actions, effective_freezes, publication, reopening_reason`.

Remplacer `_summaries` par :

```python
    async def _summaries(self) -> dict[UUID, PackageSummary]:
        packages = await self._tree()
        counts = await self._repository.counts()
        in_force = await self._repository.in_force()
        frozen = effective_freezes(packages, in_force)
        caller = require_caller()
        return {
            package.id: PackageSummary(
                package=package,
                path=path_of(package, packages),
                counts=counts.get(package.id, PackageCounts()),
                frozen_by=frozen.get(package.id),
                own_version=in_force.get(package.id),
                actions=allowed_actions(
                    caller, frozen_by=frozen.get(package.id), own_version=in_force.get(package.id)
                ),
            )
            for package in packages.values()
        }
```

Ajouter à `PackageService` :

```python
    async def versions(self, package_id: UUID) -> tuple[PackageVersion, ...]:
        require_caller()
        await self.get(package_id)
        return await self._repository.versions(package_id)

    async def publish(
        self,
        package_id: UUID,
        *,
        bump: Bump,
        notes: str,
        adr: str | None = None,
        dry_run: bool = True,
    ) -> PublishOutcome:
        """Publish a version: freeze the subtree, copy it, say so. A preview unless told otherwise.

        The words are checked before anything is read; the preview is what the caller would
        get now, and the write recomputes everything under the locks (docs/adr/0074).
        """
        caller = require_approver()
        wanted = publication(bump=bump, notes=notes, adr=adr)
        await self.get(package_id)
        preview = await self._repository.preview_publication(package_id, bump=wanted.bump)
        if dry_run:
            logger.info(
                "package publication previewed",
                extra={
                    "action": "package_publish_preview",
                    "package_id": str(package_id),
                    "version": preview.version,
                    "elements": preview.elements,
                },
            )
            return PublishOutcome(preview, None)

        def published(stored: PackageVersion) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.PACKAGE_PUBLISHED,
                    entity_id=package_id,
                    after=package_version_view(stored),
                ),
            )

        stored = await self._repository.publish(
            package_id,
            publication=wanted,
            by_sub=caller.subject,
            by=caller.username,
            now=self._now(),
            audit=published,
        )
        logger.info(
            "package published",
            extra={
                "action": "package_published",
                "package_id": str(package_id),
                "version": stored.version,
            },
        )
        return PublishOutcome(preview, stored)

    async def reopen(self, package_id: UUID, *, reason: str) -> PackageVersion:
        caller = require_approver()
        said = reopening_reason(reason)

        def reopened(stored: PackageVersion) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.PACKAGE_REOPENED,
                    entity_id=package_id,
                    after=package_version_view(stored),
                ),
            )

        stored = await self._repository.reopen(
            package_id,
            reason=said,
            by_sub=caller.subject,
            by=caller.username,
            now=self._now(),
            audit=reopened,
        )
        logger.info(
            "package reopened",
            extra={"action": "package_reopened", "package_id": str(package_id)},
        )
        return stored
```

(Un `NotAuthorisedError` vient de `require_approver()` ; `PackageNotFoundError` du `get`.)

- [ ] **Step 4 : tests de l'enregistreur (échouent), puis l'enregistreur**

`backend/tests/unit/test_frozen_refusal_recorder.py` :

```python
"""The trace of a refused write: one event, bounded, never a flood."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.audit import AuditAction
from ea.domain.package_versions import FrozenRefusal
from ea.services.frozen_refusals import FrozenRefusalRecorder
from ea.services.caller import acting_as
from tests.conftest import an_editor

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 12, tzinfo=UTC)


class Sink:
    def __init__(self) -> None:
        self.events: list = []

    async def __call__(self, events) -> None:  # type: ignore[no-untyped-def]
        self.events.extend(events)


def a_refusal() -> FrozenRefusal:
    return FrozenRefusal(uuid4(), "v1.0.0", "elements", "UPDATE")


async def test_a_refusal_leaves_one_event_about_the_package_that_froze() -> None:
    sink, refusal = Sink(), a_refusal()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    await recorder(refusal)
    (event,) = sink.events
    assert event.action is AuditAction.PACKAGE_WRITE_REFUSED_FROZEN
    assert event.entity_id == refusal.package_id
    assert event.after == {"version": "v1.0.0", "table": "elements", "operation": "UPDATE"}
    assert event.actor_sub == "editor-1"


async def test_past_thirty_a_minute_one_caller_is_only_logged() -> None:
    sink, now = Sink(), [0.0]
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: now[0])
    for _ in range(45):
        await recorder(a_refusal())
    assert len(sink.events) == 30
    now[0] = 61.0
    await recorder(a_refusal())
    assert len(sink.events) == 31


async def test_each_caller_has_its_own_budget() -> None:
    sink = Sink()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    for _ in range(40):
        await recorder(a_refusal())
    with acting_as(an_editor(subject="editor-2", username="two")):
        await recorder(a_refusal())
    assert len(sink.events) == 31


async def test_a_refusal_with_nobody_calling_is_logged_not_recorded(nobody_calling: None) -> None:
    sink = Sink()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    await recorder(a_refusal())
    assert sink.events == []


async def test_a_refusal_whose_package_could_not_be_read_is_recorded_without_an_entity() -> None:
    sink = Sink()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    await recorder(FrozenRefusal(None, None, None, None))
    assert sink.events[0].entity_id is None
```

`backend/src/ea/services/frozen_refusals.py` :

```python
"""The trace of a write that a published subtree refused (docs/adr/0074).

The refusal rolls the write back, so the event is written in a transaction of its own,
after it. A caller who loops on refusals must not turn the journal into a flood: past
`per_minute` events from one subject the event is no longer written, and the log says
how many were left out.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ea.domain.audit import AuditAction
from ea.domain.package_versions import FrozenRefusal
from ea.services.audit import AuditTrail
from ea.services.caller import current_caller

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditEvent

logger = logging.getLogger("ea.refusals")

Sink = Callable[[Sequence["AuditEvent"]], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def to_database(sessions: async_sessionmaker[AsyncSession]) -> Sink:
    """The sink that writes the events in a transaction of their own."""
    from ea.repositories.audit_store import write_events

    async def write(events: Sequence[AuditEvent]) -> None:
        async with sessions.begin() as session:
            write_events(session, events)

    return write


class FrozenRefusalRecorder:
    def __init__(
        self,
        sink: Sink,
        *,
        clock: Callable[[], datetime] = _utc_now,
        per_minute: int = 30,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._trail = AuditTrail(clock)
        self._per_minute = per_minute
        self._monotonic = monotonic
        self._recent: defaultdict[str, deque[float]] = defaultdict(deque)
        self._suppressed: defaultdict[str, int] = defaultdict(int)

    def _admit(self, subject: str) -> bool:
        now = self._monotonic()
        recent = self._recent[subject]
        while recent and now - recent[0] >= 60.0:
            recent.popleft()
        if len(recent) >= self._per_minute:
            self._suppressed[subject] += 1
            return False
        recent.append(now)
        return True

    async def __call__(self, refusal: FrozenRefusal) -> None:
        caller = current_caller.get()
        facts = {
            "package_id": str(refusal.package_id) if refusal.package_id else None,
            "version": refusal.version,
            "table": refusal.table,
            "operation": refusal.operation,
        }
        if caller is None or not self._admit(caller.subject):
            logger.warning(
                "write refused by a published package",
                extra={
                    "action": "write_refused_frozen",
                    "recorded": False,
                    "suppressed": self._suppressed.get(caller.subject, 0) if caller else 0,
                    **facts,
                },
            )
            return
        event = self._trail.event(
            AuditAction.PACKAGE_WRITE_REFUSED_FROZEN,
            entity_id=refusal.package_id,
            after={
                "version": refusal.version,
                "table": refusal.table,
                "operation": refusal.operation,
            },
        )
        await self._sink([event])
        logger.warning(
            "write refused by a published package",
            extra={"action": "write_refused_frozen", "recorded": True, **facts},
        )
```

> `AuditTrail.event` appelle `require_caller()` : le cas « personne n'appelle » est traité plus haut par `current_caller.get() is None`. `to_database` importe `write_events` dans la fonction pour respecter le sens des dépendances (`services` n'importe pas `repositories` au niveau du module) ; si le test d'architecture du dépôt refuse même cet import local, déplacer `to_database` dans `main.py`.

- [ ] **Step 5 : câblage**

Dans `backend/src/ea/main.py`, après la ligne `package_store = PostgresPackageRepository(app.state.db_sessions)` et son `if`, ajouter (imports : `from ea.db.unit_of_work import set_refusal_recorder`, `from ea.services.frozen_refusals import FrozenRefusalRecorder, to_database`) :

```python
                set_refusal_recorder(FrozenRefusalRecorder(to_database(app.state.db_sessions)))
                stack.callback(set_refusal_recorder, None)
```

à l'endroit où `app.state.db_sessions` vient d'être posé (même bloc, avant les `if … is None`), car le recorder ne dépend que des sessions.

- [ ] **Step 6 : vérifier, y compris les registres d'audit**

Run : `cd backend && uv run pytest tests/unit -q -x`
Expected : PASS. `test_audit_registry.py` / `test_audit_coverage.py` peuvent exiger que chaque `AuditAction` ait un producteur : ouvrir le test, repérer comment `PACKAGE_UPDATED` y apparaît et y déclarer de la même façon `PACKAGE_PUBLISHED` (producteur `PackageService.publish`), `PACKAGE_REOPENED` (`PackageService.reopen`) et `PACKAGE_WRITE_REFUSED_FROZEN` (`FrozenRefusalRecorder.__call__`). Ne pas affaiblir ces tests : leur ajouter les trois lignes.

- [ ] **Step 7 : commit**

```bash
git add backend/src backend/tests
git commit -m "feat(packages): le service publie et rouvre, l'enregistreur trace les refus

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 8 : l'API et le schéma OpenAPI

**Files :**
- Modify : `backend/src/ea/api/schemas.py` (`PackageRead`, nouveaux modèles)
- Modify : `backend/src/ea/api/packages.py` (trois routes)
- Modify : `backend/openapi.json`, `frontend/src/api/schema.d.ts` (générés)
- Test : `backend/tests/e2e/test_package_publication_api.py`

**Interfaces :**
- Consumes : le service (tâche 7).
- Produces :
  - `POST /packages/{id}/publish` body `PublishPackage{bump, notes, adr?, dry_run=true}` → `PublishResult{preview, published?}`
  - `POST /packages/{id}/reopen` body `ReopenPackage{reason}` → `PackageVersionRead`
  - `GET /packages/{id}/versions` → `list[PackageVersionRead]`
  - `PackageRead` gagne `frozen_version: str | None`, `frozen_by: FrozenByRead | None`, `allowed_actions: list[Literal["publish","reopen"]]`.

- [ ] **Step 1 : tests e2e (échouent)**

`backend/tests/e2e/test_package_publication_api.py` :

```python
"""The publication endpoints, against doubled stores — every status of the contract."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from ea.core.config import Settings
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from tests.conftest import (
    InMemoryDiagrams,
    InMemoryPackages,
    StaticVerifier,
    a_reader,
    an_approver,
    an_editor,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def client(
    service: ArchitectureService, diagrams: InMemoryDiagrams, packages: InMemoryPackages
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        packages=packages,  # type: ignore[arg-type]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def a_package(client: httpx.AsyncClient, name: str = "Finance") -> dict[str, Any]:
    response = await client.post("/packages", json={"name": name})
    assert response.status_code == 201, response.text
    return dict(response.json())


async def test_publishing_previews_by_default_then_publishes(client: httpx.AsyncClient) -> None:
    package = await a_package(client)
    url = f"/packages/{package['id']}/publish"

    preview = await client.post(url, json={"bump": "minor", "notes": "Première"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["published"] is None
    assert preview.json()["preview"]["version"] == "v1.0.0"

    done = await client.post(url, json={"bump": "minor", "notes": "Première", "dry_run": False})
    assert done.status_code == 200, done.text
    assert done.json()["published"]["version"] == "v1.0.0"

    read = (await client.get(f"/packages/{package['id']}")).json()
    assert read["frozen_version"] == "v1.0.0"
    assert read["frozen_by"]["version"] == "v1.0.0"
    assert read["allowed_actions"] == ["reopen"]

    again = await client.post(url, json={"bump": "minor", "notes": "x", "dry_run": False})
    assert (again.status_code, again.json()["error"]) == (409, "package_already_frozen")


async def test_the_versions_are_listed_and_reopening_closes_one(client: httpx.AsyncClient) -> None:
    package = await a_package(client)
    await client.post(
        f"/packages/{package['id']}/publish",
        json={"bump": "minor", "notes": "n", "dry_run": False},
    )
    reopened = await client.post(f"/packages/{package['id']}/reopen", json={"reason": "coquille"})
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["reopen_reason"] == "coquille"
    listed = (await client.get(f"/packages/{package['id']}/versions")).json()
    assert [v["version"] for v in listed] == ["v1.0.0"]
    assert listed[0]["reopened_at"] is not None
    nothing = await client.post(f"/packages/{package['id']}/reopen", json={"reason": "encore"})
    assert (nothing.status_code, nothing.json()["error"]) == (409, "package_not_frozen")


@pytest.mark.parametrize(
    ("body", "status", "error"),
    [
        ({"bump": "major", "notes": "n"}, 422, "adr_required"),
        ({"bump": "minor", "notes": "   "}, 422, "invalid_publication"),
        ({"bump": "minor", "notes": "n" * 4001}, 422, None),
        ({"bump": "huge", "notes": "n"}, 422, None),
        ({"bump": "minor", "notes": "n", "surplus": 1}, 422, None),
    ],
)
async def test_what_cannot_be_published_is_refused_typed(
    client: httpx.AsyncClient, body: dict[str, Any], status: int, error: str | None
) -> None:
    package = await a_package(client)
    response = await client.post(f"/packages/{package['id']}/publish", json=body)
    assert response.status_code == status, response.text
    if error is not None:
        assert response.json()["error"] == error


async def test_an_unknown_package_is_a_404(client: httpx.AsyncClient) -> None:
    missing = "00000000-0000-4000-8000-000000000000"
    for method, path, body in (
        ("post", f"/packages/{missing}/publish", {"bump": "minor", "notes": "n"}),
        ("post", f"/packages/{missing}/reopen", {"reason": "r"}),
        ("get", f"/packages/{missing}/versions", None),
    ):
        response = await client.request(method, path, json=body)
        assert response.status_code == 404, (path, response.text)


async def test_who_may_publish(
    service: ArchitectureService, diagrams: InMemoryDiagrams, packages: InMemoryPackages
) -> None:
    verifier = StaticVerifier(
        {"ap": an_approver(), "ed": an_editor(), "rd": a_reader()}
    )
    app = create_app(
        Settings(debug=True, auth_enabled=True),
        architecture_service=service,
        diagrams=diagrams,  # type: ignore[arg-type]
        packages=packages,  # type: ignore[arg-type]
        token_verifier=verifier,  # type: ignore[call-arg]
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        created = await client.post(
            "/packages", json={"name": "Finance"}, headers={"Authorization": "Bearer ed"}
        )
        package_id = created.json()["id"]
        body = {"bump": "minor", "notes": "n", "dry_run": False}
        anonymous = await client.post(f"/packages/{package_id}/publish", json=body)
        assert anonymous.status_code == 401
        for token in ("ed", "rd"):
            refused = await client.post(
                f"/packages/{package_id}/publish",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
            assert (refused.status_code, refused.json()["error"]) == (403, "forbidden")
        allowed = await client.post(
            f"/packages/{package_id}/publish", json=body, headers={"Authorization": "Bearer ap"}
        )
        assert allowed.status_code == 200, allowed.text
```

> Dans `test_who_may_publish`, la façon exacte d'injecter le vérificateur dans `create_app` est celle de `test_a_reader_reads_the_tree_but_cannot_change_it` (`tests/e2e/test_package_api.py`, ligne ~286) : recopier son appel à `create_app(...)` tel quel et n'y changer que les jetons et les routes.

Run : `cd backend && uv run pytest tests/e2e/test_package_publication_api.py -q` ; Expected : FAIL (routes absentes, 404/405).

- [ ] **Step 2 : les schémas**

Dans `backend/src/ea/api/schemas.py`, importer `Literal`, `Bump`, `FrozenBy`, `PackageVersion`, `PublicationPreview`, `PublishOutcome`, `MAX_NOTES_LENGTH`, `MAX_ADR_LENGTH`, `MAX_REASON_LENGTH`, puis ajouter après `PackageRead` ses nouveaux champs et les modèles :

Champs ajoutés à `PackageRead` (avant `created_at`) :

```python
    frozen_version: str | None = Field(
        description="The version in force on this very package, if it was published on its own."
    )
    frozen_by: FrozenByRead | None = Field(
        description="The publication that freezes this package: itself or an ancestor."
    )
    allowed_actions: list[Literal["publish", "reopen"]] = Field(
        description="The gestures offered to the caller; the server decides, the client draws."
    )
```

et dans `PackageRead.of`, les arguments `frozen_version=summary.own_version, frozen_by=FrozenByRead.of(summary.frozen_by) if summary.frozen_by else None, allowed_actions=list(summary.actions),` (avec `# type: ignore[arg-type]` interdit : la liste `summary.actions` est un `tuple[str, ...]` ; utiliser `cast("list[Literal['publish', 'reopen']]", list(summary.actions))`).

Nouveaux modèles (définir `FrozenByRead` **avant** `PackageRead`) :

```python
class FrozenByRead(BaseModel):
    package_id: UUID
    path: str
    version: str

    @classmethod
    def of(cls, frozen: FrozenBy) -> FrozenByRead:
        return cls(package_id=frozen.package_id, path=frozen.path, version=frozen.version)


PublicationNotes = Annotated[str, Field(max_length=MAX_NOTES_LENGTH)]
PublicationAdr = Annotated[str, Field(max_length=MAX_ADR_LENGTH)]
ReopeningReason = Annotated[str, Field(max_length=MAX_REASON_LENGTH)]


class PublishPackage(_Input):
    bump: Bump
    notes: PublicationNotes
    adr: PublicationAdr | None = None
    dry_run: bool = Field(default=True, description="A preview unless told otherwise.")


class ReopenPackage(_Input):
    reason: ReopeningReason


class PublicationPreviewRead(BaseModel):
    version: str
    bump: Bump
    elements: int
    relationships: int
    boxes: int
    diagrams: int


class PackageVersionRead(BaseModel):
    id: UUID
    package_id: UUID
    version: str
    bump: Bump
    notes: str
    adr: str | None
    baseline_id: UUID
    elements: int
    relationships: int
    boxes: int
    published_by: str
    published_at: datetime
    publish_audit_event_id: UUID | None
    reopened_by: str | None
    reopened_at: datetime | None
    reopen_reason: str | None
    reopen_audit_event_id: UUID | None

    @classmethod
    def of(cls, version: PackageVersion) -> PackageVersionRead:
        return cls(
            id=version.id,
            package_id=version.package_id,
            version=version.version,
            bump=version.bump,
            notes=version.notes,
            adr=version.adr,
            baseline_id=version.baseline_id,
            elements=version.element_count,
            relationships=version.relationship_count,
            boxes=version.box_count,
            published_by=version.published_by,
            published_at=version.published_at,
            publish_audit_event_id=version.publish_audit_event_id,
            reopened_by=version.reopened_by,
            reopened_at=version.reopened_at,
            reopen_reason=version.reopen_reason,
            reopen_audit_event_id=version.reopen_audit_event_id,
        )


class PublishResult(BaseModel):
    preview: PublicationPreviewRead
    published: PackageVersionRead | None

    @classmethod
    def of(cls, outcome: PublishOutcome) -> PublishResult:
        preview = outcome.preview
        return cls(
            preview=PublicationPreviewRead(**asdict(preview)),
            published=PackageVersionRead.of(outcome.published) if outcome.published else None,
        )
```

(`from dataclasses import asdict`.) Le `bump` d'un modèle de lecture renvoie l'`Enum` ; FastAPI le sérialise en `"minor"`.

- [ ] **Step 3 : les routes**

Dans `backend/src/ea/api/packages.py`, importer `PackageVersionRead`, `PublishPackage`, `PublishResult`, `ReopenPackage` depuis `ea.api.schemas`, puis ajouter avant `move_elements` :

```python
@router.post(
    "/packages/{package_id}/publish", response_model=PublishResult, responses=REJECTED
)
async def publish_package(
    package_id: UUID, body: PublishPackage, packages: Packages
) -> PublishResult:
    """Freeze the package and everything below it under a new version (an approver's gesture).

    A preview unless `dry_run` is false: the version it would take and what it would freeze.
    A MAJOR names its ADR. Nothing below a published package can be written until it is reopened.
    """
    return PublishResult.of(
        await packages.publish(
            package_id, bump=body.bump, notes=body.notes, adr=body.adr, dry_run=body.dry_run
        )
    )


@router.post(
    "/packages/{package_id}/reopen", response_model=PackageVersionRead, responses=REJECTED
)
async def reopen_package(
    package_id: UUID, body: ReopenPackage, packages: Packages
) -> PackageVersionRead:
    """Close the version in force, with a reason (an approver's gesture)."""
    return PackageVersionRead.of(await packages.reopen(package_id, reason=body.reason))


@router.get(
    "/packages/{package_id}/versions",
    response_model=list[PackageVersionRead],
    responses=NOT_FOUND,
)
async def list_package_versions(
    package_id: UUID, packages: Packages
) -> list[PackageVersionRead]:
    """Every version the package was published under, the latest first."""
    return [PackageVersionRead.of(v) for v in await packages.versions(package_id)]
```

- [ ] **Step 4 : vérifier, puis régénérer le client**

Run : `cd backend && uv run pytest tests/e2e/test_package_publication_api.py tests/e2e/test_package_api.py -q` ; Expected : PASS.
Run (racine) : `make openapi && make openapi-check`
Expected : « Client généré à jour ». Les deux fichiers générés changent ; ne jamais les éditer à la main.
Run : `cd backend && uv run pytest tests/unit/test_mcp_server.py tests/unit -q` ; Expected : PASS (les bornes MCP n'ont pas changé ; si un test d'identité des schémas échoue, le lire avant de toucher).

- [ ] **Step 5 : commit**

```bash
git add backend frontend/src/api/schema.d.ts
git commit -m "feat(api): publier, rouvrir et lister les versions d'un package

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

### Task 9 : la preuve de sécurité, la documentation, la revue

**Files :**
- Create : `backend/tests/integration/test_package_freeze_security.py`
- Create : `docs/adr/0074-gel-par-package-et-triggers.md`
- Modify : `docs/audit-retention.md`
- Modify : `CLAUDE.md` (l'état du projet, la section sur l'audit et celle sur les baselines)

Chaque ligne du modèle de menaces de la spec (§ 10) a un test nommé d'après elle. Les lignes déjà couvertes ailleurs sont référencées, pas recopiées.

- [ ] **Step 1 : les tests de sécurité**

`backend/tests/integration/test_package_freeze_security.py` :

```python
"""One test per line of the threat model (docs/superpowers/specs/2026-10-12-gel-par-package-design.md §10).

- Editor publishes or reopens ................ tests/unit/test_package_publication_service.py
                                               tests/e2e/test_package_publication_api.py (401/403)
- Forgotten door ............................. tests/integration/test_package_freeze_triggers.py
- Race with a publication .................... tests/integration/test_package_publication.py
- Filing in and out of a frozen subtree ...... tests/integration/test_package_freeze_triggers.py
- Reopening leaves no trace .................. below
- A write the trigger cannot judge ........... below (fail closed)
- Injection through notes / adr / reason ..... below
- Leak in logs and errors .................... below
- Flooding the journal ....................... tests/unit/test_frozen_refusal_recorder.py
"""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ea.db.unit_of_work import work
from ea.domain.errors import PackageFrozenError
from ea.domain.package_versions import Bump
from ea.repositories.package_store import PostgresPackageRepository
from tests.integration.test_package_freeze_triggers import a_package, an_element, publish
from tests.integration.test_package_publication import publish_it, reopen_it

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_reopening_always_leaves_a_closed_row_an_event_and_a_reason(
    engine_at_head: AsyncEngine,
) -> None:
    repo = PostgresPackageRepository(async_sessionmaker(engine_at_head, expire_on_commit=False))
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    published = await publish_it(repo, root)
    closed = await reopen_it(repo, root)
    async with engine_at_head.connect() as connection:
        actions = [
            row[0]
            for row in await connection.execute(
                text("SELECT action FROM audit_events WHERE entity_id = :p ORDER BY at, id"),
                {"p": root},
            )
        ]
        stored = (
            await connection.execute(
                text("SELECT reopen_reason, reopen_audit_event_id FROM package_versions")
            )
        ).one()
    assert actions == ["package.published", "package.reopened"]
    assert tuple(stored) == ("coquille", closed.reopen_audit_event_id)
    assert published.publish_audit_event_id != closed.reopen_audit_event_id


async def test_the_audit_trail_keeps_digests_and_never_the_words(
    engine_at_head: AsyncEngine,
) -> None:
    repo = PostgresPackageRepository(async_sessionmaker(engine_at_head, expire_on_commit=False))
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    await publish_it(repo, root, notes="NOTE-SECRÈTE-123", adr="ADR-SECRET-9", bump=Bump.MAJOR)
    async with engine_at_head.connect() as connection:
        stored = (
            await connection.execute(
                text("SELECT after::text FROM audit_events WHERE entity_id = :p"), {"p": root}
            )
        ).scalar_one()
    assert "NOTE-SECRÈTE-123" not in stored
    assert "ADR-SECRET-9" not in stored
    assert "notes_sha256" in stored


async def test_a_notes_text_made_of_sql_and_markup_is_stored_as_text(
    engine_at_head: AsyncEngine,
) -> None:
    repo = PostgresPackageRepository(async_sessionmaker(engine_at_head, expire_on_commit=False))
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
    hostile = "'); DROP TABLE elements; --\n<script>alert(1)</script>${jndi:x}"
    version = await publish_it(repo, root, notes=hostile, adr=None, bump=Bump.MINOR)
    assert version.notes == hostile
    async with engine_at_head.connect() as connection:
        assert (await connection.execute(text("SELECT count(*) FROM elements"))).scalar_one() == 0


async def test_a_refusal_never_names_the_element_or_its_content(
    engine_at_head: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance-Secret", None)
        element = await an_element(connection, "NOM-D-ELEMENT-SECRET", root)
        await publish(connection, root)
    sessions = async_sessionmaker(engine_at_head)
    with caplog.at_level(logging.DEBUG), pytest.raises(PackageFrozenError) as raised:
        async with work(sessions) as session:
            await session.execute(
                text("UPDATE elements SET description = 'TEXTE-SECRET' WHERE id = :id"),
                {"id": element},
            )
    shown = str(raised.value) + " " + caplog.text
    for secret in ("NOM-D-ELEMENT-SECRET", "TEXTE-SECRET", "Finance-Secret"):
        assert secret not in shown


async def test_a_trigger_that_cannot_decide_refuses_the_write(engine_at_head: AsyncEngine) -> None:
    """Fail closed: if the freeze lookup itself breaks, the write does not go through."""
    async with engine_at_head.begin() as connection:
        root = await a_package(connection, "Finance", None)
        element = await an_element(connection, "inside", root)
        await publish(connection, root)
    # Break the lookup the way a bad migration might: the function no longer resolves.
    async with engine_at_head.begin() as connection:
        await connection.execute(text("ALTER FUNCTION ea_frozen_ancestor(uuid) RENAME TO gone"))
    with pytest.raises(DBAPIError):
        async with engine_at_head.begin() as connection:
            await connection.execute(
                text("UPDATE elements SET description = 'x' WHERE id = :id"), {"id": element}
            )
    async with engine_at_head.begin() as connection:
        await connection.execute(text("ALTER FUNCTION gone(uuid) RENAME TO ea_frozen_ancestor"))


async def test_no_table_that_hangs_from_the_model_is_left_unguarded(
    engine_at_head: AsyncEngine,
) -> None:
    """A new table that references elements, diagrams or packages must get its trigger, or be
    listed here with the reason it is not frozen."""
    exempt = {
        "baseline_elements": "a copy that outlives the model",
        "baseline_relationships": "a copy that outlives the model",
        "baseline_boxes": "a copy that outlives the model",
        "document_chunks": "the derived search index: a reindex must still run",
        "external_references": "the cache of a third-party tool, refreshed by a pipeline",
        "audit_events": "the trail itself",
        "package_versions": "append-only by its own triggers",
        "diagram_templates": "carry no element and no package",
        "saved_queries": "carry no element and no package",
        "file_metadata": "files of the bucket",
    }
    async with engine_at_head.connect() as connection:
        referencing = {
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT DISTINCT c.conrelid::regclass::text FROM pg_constraint c "
                    "WHERE c.contype = 'f' AND c.confrelid::regclass::text IN "
                    "('elements', 'diagrams', 'packages', 'relationships')"
                )
            )
        }
        guarded = {
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT DISTINCT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE t.tgname LIKE '%\\_frozen' AND NOT t.tgisinternal"
                )
            )
        }
    missing = referencing - guarded - set(exempt) - {"elements", "diagrams", "packages", "relationships"}
    assert missing == set(), f"unguarded tables: {sorted(missing)}"
```

- [ ] **Step 2 : lancer toute la preuve**

Run : `cd backend && uv run pytest tests/integration/test_package_freeze_security.py -q -m postgres`
Expected : PASS. Si `test_no_table_that_hangs_from_the_model_is_left_unguarded` signale une table, ajouter son trigger dans une révision `0021` (ou, si elle n'a vraiment pas à être gelée, la lister dans `exempt` avec la raison).

- [ ] **Step 3 : l'ADR 0074**

`docs/adr/0074-gel-par-package-et-triggers.md`, avec les rubriques de `docs/adr/TEMPLATE.md` (lire ce gabarit d'abord et le suivre), statut `Proposition`, `affects:` la liste des fichiers des tâches 1 à 8. Contenu : le contexte (DOC-ADM-008 et DOC-ADM-010 ; trois gels existants — `locked`, baselines, diagrammes), la décision (version en vigueur dans `package_versions` ; triggers et SQLSTATE `EA001` ; traduction unique dans `work()` ; publication sous `LOCK TABLE … IN SHARE MODE` en `READ COMMITTED` et pourquoi pas `REPEATABLE READ` ; baseline scopée ; enregistreur d'audit avec budget), les alternatives écartées (matérialiser le gel sur chaque élément ; garde Python dans chaque porte ; les deux), le **modèle de menaces** de la spec § 10 recopié tel quel avec, pour chaque ligne, le fichier de test, la séparation des tâches **explicitement non appliquée** (décision du 2026-10-12, à rouvrir si le dépôt passe à plusieurs approbateurs), les conséquences (supprimer un élément qu'un diagramme publié représente est refusé ; la restauration d'une baseline qui touche un sous-arbre publié échoue en bloc ; un script hors `work()` reçoit l'erreur brute `EA001`).

- [ ] **Step 4 : rétention et CLAUDE.md**

Dans `docs/audit-retention.md`, ajouter une section : `package_versions` n'est jamais purgée (append-only, triggers) ; les événements `package.write_refused_frozen` sont bornés par l'enregistreur (30 par minute et par `sub`) mais jamais purgés ; une baseline désignée par une version ne peut pas être supprimée.

Dans `CLAUDE.md` : (a) dans « Project status », une phrase sur le gel par package (`package_versions`, migration `0020`, `docs/adr/0074`, rôle `ea-approver`) ; (b) dans « Baselines freeze the model… », la baseline scopée et la phrase « une baseline désignée par une version ne se supprime pas » ; (c) une nouvelle section courte « Un sous-arbre publié est gelé par la base » qui dit : les triggers sont la règle, il n'y a pas de garde Python à appeler ; une table nouvelle qui référence des éléments, diagrammes ou packages doit recevoir son trigger (le test `test_no_table_that_hangs_from_the_model_is_left_unguarded` échoue sinon) ; `work()` est l'unique point de traduction. Mettre à jour dans la table des décisions de pile aucune ligne (aucune dépendance nouvelle).

- [ ] **Step 5 : la porte de qualité**

Run (racine), dans cet ordre, en rapportant le résultat tel quel :

```bash
make lint-check && make typecheck && make openapi-check && make test
make test-integration
make audit
```

Expected : tout vert. Si `make audit` échoue sur une alerte étrangère à ce plan, la noter dans la description de la PR sans la corriger ici ; si c'est une alerte sur du code de ce plan (bandit sur les deux `text()` constants, par exemple), la lever avec `# nosec Bxxx` et la raison, jamais par exclusion globale.

- [ ] **Step 6 : la revue indépendante de la branche**

Les deux commandes suivantes sont à lancer par l'exécutant, pas à simuler :

1. Skill `security-review` sur les changements en attente de la branche. Chaque constat est soit corrigé avec un test, soit justifié par écrit dans la description de la PR.
2. Skill `code-review` (niveau `high`) sur la branche, mêmes règles.

La description de la PR liste, pour chaque ligne du modèle de menaces, la menace, la parade et le test. **Aucune ligne n'est cochée tant que la commande correspondante n'a pas réellement tourné et que son résultat n'a pas été lu.**

- [ ] **Step 7 : commit**

```bash
git add docs backend/tests CLAUDE.md
git commit -m "docs+test: preuve de sécurité du gel par package, ADR 0074

Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>"
```

---

## Auto-revue du plan

**Couverture de la spec.** §2 données (tâche 2) ; §3 triggers + traduction (tâches 2 et 3) ; §4 services : publier, rouvrir, rôles (tâches 6 et 7), baselines scopées capture (tâche 5) — le `diff` / `plan_restore` scopés sont au plan 2, comme annoncé ; §5 HTTP (tâche 8), MCP et `frozen_by` sur `ElementRead` / `DiagramRead` au plan 2 ; §6 SPA au plan 2 ; §7 tests : unitaires (1, 4, 7), intégration (2, 3, 5, 6, 9), API (8) ; §9 traçabilité : événements (7), chaîne version ↔ baseline ↔ événement (6, test de la tâche 6 et de la tâche 9), refus audités et bornés (3, 7), rétention (9) ; §10 revue de sécurité (9). Aucune exigence de la spec n'est sans tâche ou sans mention du plan 2.

**Placeholders.** Aucun. Deux étapes renvoient à un fichier existant dont la forme exacte ne se déduit pas sans le lire (l'appel à `create_app` avec un vérificateur, copié de `test_a_reader_reads_the_tree_but_cannot_change_it` ; les registres d'audit, auxquels on ajoute trois lignes) : chacune dit où lire et fixe la règle (ne pas affaiblir le test).

**Cohérence des types.** `FrozenRefusal`, `FrozenBy`, `PackageVersion`, `PublicationPreview`, `PublishOutcome`, `Publication`, `Bump` sont définis à la tâche 1 et utilisés avec les mêmes noms aux tâches 3 à 8 ; `PackageRepository.publish/reopen` ont la même signature au protocole, au dépôt, au double (`audit: Any`) et à l'appel du service ; `PackageSummary.frozen_by/own_version/actions` (tâche 1) sont lus par `PackageRead.of` (tâche 8).

**Review Focus.** 1 : `test_an_empty_package_can_be_published` ; 2 : `test_publishing_a_parent_whose_child_is_already_published_is_allowed` ; 3 : `test_reopening_a_package_under_a_published_ancestor_still_leaves_it_frozen` ; 4 : `test_two_simultaneous_publications…` ; 5 : cas « outside element deleted while a published diagram draws it » dans `refused()` ; 6 : `test_notes_are_trimmed_and_kept_whole` et la notice à guillemets de la tâche 6.
