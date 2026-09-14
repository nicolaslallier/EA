# PostgreSQL seul — plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retirer Neo4j : le graphe d'architecture (éléments, relations, traversées, IPAM) est stocké dans le PostgreSQL déjà utilisé par les documents et les diagrammes, avec import ponctuel des données existantes.

**Architecture:** Deux tables (`elements`, `relationships`) créées par la révision Alembic `0005`, lues et écrites par `PostgresArchitectureRepository` (SQLAlchemy 2 async, CTE récursives pour les traversées), qui remplace `Neo4jArchitectureRepository` derrière les ports inchangés `ArchitectureRepository` et `IpamRepository`. La révision `0006` pose les clés étrangères des documents et des boîtes de diagramme vers `elements`, ce qui supprime le port `ElementAttachments`. Un module `ea.graph_import` et un script ponctuel copient le graphe Neo4j existant.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, PostgreSQL 17 + pgvector, pytest + pytest-asyncio (mode strict), uv, ruff, mypy --strict.

**Spec:** `docs/superpowers/specs/2026-09-14-postgresql-seul-design.md`

## Global Constraints

- Contrats inchangés : `backend/openapi.json` et `frontend/src/api/schema.d.ts` ne bougent pas (`make openapi-check` vert) ; aucun outil MCP ajouté ou retiré ; `pipelines/` non modifié.
- Dépendance de flèche : `api → services → domain ← repositories`. `domain/` n'importe ni SQLAlchemy ni FastAPI.
- Toute valeur variable d'une requête est un paramètre lié ; pas de `text()` hors tests et catalogue système, et tout `text()` porte un commentaire qui le justifie.
- Noms de contraintes et d'index (convention figée de `db/base.py`) : `pk_elements`, `uq_elements_element_type_name`, `ix_elements_element_type`, `ix_elements_layer`, `uq_elements_vrf_ip_address`, `uq_elements_vrf_cidr`, `pk_relationships`, `fk_relationships_source_id_elements`, `fk_relationships_target_id_elements`, `ix_relationships_source_id`, `ix_relationships_target_id`, `fk_element_documents_element_id_elements`, `fk_diagram_nodes_element_id_elements`.
- Valeurs stockées : `element_type`, `layer`, `aspect`, `relationship_type`, `access_type` en `.value` de leur enum ; `properties` en `jsonb` **sans** préfixe `p_`.
- `MAX_TRAVERSAL_DEPTH = 10`.
- Chaque test d'intégration passe par la fixture `postgres_engine` (garde loopback + port ≠ 5432) ; aucun test ne sort de la machine.
- Commits en Conventional Commits, terminés par `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.
- **Commande d'intégration** (appelée `ITEST <chemin>` dans les tâches), lancée depuis `backend/` après `make pg-up` à la racine :
  `EA_DEBUG=true EA_POSTGRES_ENABLED=true EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=5433 EA_POSTGRES_PASSWORD=developmentonly EA_EMBEDDINGS_ENABLED=false uv run pytest <chemin> -q`
- **Commande unitaire** : depuis `backend/`, `uv run pytest <chemin> -q` (sans `--cov`, qui ferait échouer le plancher sur un sous-ensemble).

---

## File Structure

| Fichier | Rôle | Tâche |
|---|---|---|
| `backend/migrations/versions/0005_architecture_graph.py` | crée `elements` et `relationships` | 1 |
| `backend/src/ea/db/models/architecture.py` | `ElementRecord`, `RelationshipRecord` | 1 |
| `backend/src/ea/repositories/architecture_store.py` | `PostgresArchitectureRepository`, mapping, traduction des refus, traversées, IPAM | 2, 3, 4 |
| `backend/migrations/versions/0006_attachments_follow_elements.py` | garde « import d'abord », purge des orphelins, FK des attachements | 5 |
| `backend/src/ea/graph_import.py` | conversion des enregistrements Neo4j, copie en une transaction, vérification | 7 |
| `backend/scripts/import_neo4j.py` | script ponctuel : lit Neo4j, appelle `ea.graph_import` | 7 |
| `docs/adr/0033-postgresql-seul-pour-le-graphe.md` | décision | 9 |
| Supprimés | `db/neo4j.py`, `db/schema.py`, `repositories/archimate_graph.py`, `tests/unit/test_neo4j_driver.py`, `tests/unit/test_schema.py`, `tests/unit/test_cypher_tracing.py`, `tests/unit/test_architecture_delete.py` | 5, 6 |

---

### Task 1: Les tables du graphe (révision 0005 et modèles)

**Files:**
- Create: `backend/src/ea/db/models/architecture.py`
- Create: `backend/migrations/versions/0005_architecture_graph.py`
- Create: `backend/tests/integration/test_graph_schema.py`
- Modify: `backend/src/ea/db/models/__init__.py`
- Modify: `backend/tests/integration/conftest.py` (ajout de la fixture `engine_at_head`)
- Modify: `backend/tests/integration/test_diagram_store.py:34-47` (retrait de sa copie locale d'`engine_at_head`)
- Test: `backend/tests/unit/test_migrations.py`

**Interfaces:**
- Produces: `ea.db.models.architecture.ElementRecord` (colonnes `id: UUID`, `element_type: str`, `layer: str`, `aspect: str`, `name: str`, `description: str`, `documentation: str`, `properties: dict[str, str]`, `created_at: datetime`, `updated_at: datetime`) ; `RelationshipRecord` (`id`, `relationship_type: str`, `source_id: UUID`, `target_id: UUID`, `source_type: str`, `target_type: str`, `name: str`, `access_type: str | None`, `directed: bool`, `properties: dict[str, str]`, `created_at`) ; fixture pytest `engine_at_head: AsyncEngine` (base à `head`, `downgrade base` à la sortie).

- [ ] **Step 1: Écrire les tests unitaires qui échouent**

Ajouter à la fin de `backend/tests/unit/test_migrations.py` :

```python
def test_an_element_name_is_unique_within_its_type_by_a_named_constraint() -> None:
    """The constraint `pipelines/` detects a duplicate by — docs/adr/0033."""
    table = Base.metadata.tables["elements"]

    assert "uq_elements_element_type_name" in {c.name for c in table.constraints}


def test_an_address_and_a_prefix_are_unique_per_vrf_by_partial_indexes() -> None:
    """Neo4j's composite constraints ignored a node missing a property; so does `WHERE`."""
    indexes = {index.name: index for index in Base.metadata.tables["elements"].indexes}

    for name in ("uq_elements_vrf_ip_address", "uq_elements_vrf_cidr"):
        assert indexes[name].unique is True
        assert indexes[name].dialect_options["postgresql"]["where"] is not None


def test_a_relationship_follows_both_its_ends_by_the_foreign_key() -> None:
    table = Base.metadata.tables["relationships"]

    for column in ("source_id", "target_id"):
        key = next(iter(table.c[column].foreign_keys))
        assert key.column.table.name == "elements"
        assert key.ondelete == "CASCADE"
        assert table.c[column].index is True


def test_user_defined_properties_are_one_jsonb_map() -> None:
    from sqlalchemy.dialects.postgresql import JSONB

    assert isinstance(Base.metadata.tables["elements"].c.properties.type, JSONB)
    assert isinstance(Base.metadata.tables["relationships"].c.properties.type, JSONB)
```

- [ ] **Step 2: Vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -q`
Expected: FAIL avec `KeyError: 'elements'`.

- [ ] **Step 3: Écrire les modèles**

Créer `backend/src/ea/db/models/architecture.py` :

```python
"""The architecture graph as two tables — see docs/adr/0033.

An element is a row of `elements`; a relationship is a row of `relationships`
whose two ends are foreign keys, so deleting an element takes its links with it
by DDL. User-defined attributes are one `jsonb` map: the domain restricts their
keys to plain identifiers and their values to strings (`domain/model.py`).

The IP addressing of docs/adr/0020 is still an attribute of the element. Its
two uniqueness rules — an address, and a prefix, once per VRF — are partial
unique indexes on expressions over that map, and the `WHERE` makes them ignore
an element missing either key, exactly as Neo4j's composite constraints did.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class ElementRecord(Base):
    """One ArchiMate element."""

    __tablename__ = "elements"
    __table_args__ = (
        # Two applications called "Billing" are a modelling mistake, not a fact.
        UniqueConstraint("element_type", "name"),
        # Static DDL text, no runtime value: the expressions of two indexes.
        Index(
            "uq_elements_vrf_ip_address",
            text("(properties ->> 'vrf')"),
            text("(properties ->> 'ip_address')"),
            unique=True,
            postgresql_where=text("properties ? 'vrf' AND properties ? 'ip_address'"),
        ),
        Index(
            "uq_elements_vrf_cidr",
            text("(properties ->> 'vrf')"),
            text("(properties ->> 'cidr')"),
            unique=True,
            postgresql_where=text("properties ? 'vrf' AND properties ? 'cidr'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    layer: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    aspect: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    documentation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RelationshipRecord(Base):
    """One typed, directed link between two elements."""

    __tablename__ = "relationships"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Indexed one by one: a traversal walks from either end.
    source_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    access_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

Remplacer le corps de `backend/src/ea/db/models/__init__.py` après la docstring par :

```python
from __future__ import annotations

from ea.db.base import Base
from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.db.models.chunk import DocumentChunk
from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.db.models.document import ElementDocument

__all__ = [
    "Base",
    "DiagramNodeRecord",
    "DiagramRecord",
    "DocumentChunk",
    "ElementDocument",
    "ElementRecord",
    "RelationshipRecord",
]
```

- [ ] **Step 4: Vérifier que les tests unitaires passent**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -q`
Expected: PASS.

- [ ] **Step 5: Écrire le test d'intégration qui échoue**

Ajouter à `backend/tests/integration/conftest.py` (imports en tête : `import asyncio`, `from alembic import command`) :

```python
@pytest_asyncio.fixture
async def engine_at_head(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> AsyncIterator[AsyncEngine]:
    """The throwaway database at `head`, migrated back to `base` on the way out.

    The chain rather than `create_all`: what is under test includes the
    revisions, and a schema built from the metadata would pass while the
    revision that deploys it was wrong.
    """
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    try:
        yield postgres_engine
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
```

Dans `backend/tests/integration/test_diagram_store.py`, supprimer la fixture locale `engine_at_head` (lignes 34-43) — celle de `conftest.py` la remplace — puis retirer les imports devenus inutiles avec `uv run ruff check --fix tests/integration/test_diagram_store.py`.

Créer `backend/tests/integration/test_graph_schema.py` :

```python
"""The graph tables of migration 0005, as the server holds them — docs/adr/0033."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

EXPECTED_INDEXES = {
    "pk_elements",
    "uq_elements_element_type_name",
    "ix_elements_element_type",
    "ix_elements_layer",
    "uq_elements_vrf_ip_address",
    "uq_elements_vrf_cidr",
    "pk_relationships",
    "ix_relationships_source_id",
    "ix_relationships_target_id",
}


async def test_the_graph_tables_carry_every_index_they_are_declared_with(
    engine_at_head: AsyncEngine,
) -> None:
    async with engine_at_head.connect() as connection:
        rows = await connection.execute(
            # A catalogue read with no runtime value: nothing to bind.
            text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename IN ('elements', 'relationships')"
            )
        )

    assert EXPECTED_INDEXES <= {row[0] for row in rows}
```

- [ ] **Step 6: Vérifier l'échec**

Run (à la racine) : `make pg-up`, puis `cd backend && ITEST tests/integration/test_graph_schema.py`
Expected: FAIL — `EXPECTED_INDEXES` n'est pas inclus (aucune table `elements` à `head`).

- [ ] **Step 7: Écrire la révision 0005**

Créer `backend/migrations/versions/0005_architecture_graph.py` :

```python
"""le graphe d'architecture en tables relationnelles

Revision ID: 0005
Revises: 0004
Date: 2026-09-14

Les éléments et les relations quittent Neo4j — voir docs/adr/0033. Les tables
sont créées vides : les données arrivent par `make graph-import`, et les clés
étrangères des documents et des diagrammes vers `elements` attendent la
révision 0006, qui ne peut être posée qu'une fois le graphe importé.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "elements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("element_type", sa.Text(), nullable=False),
        sa.Column("layer", sa.Text(), nullable=False),
        sa.Column("aspect", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("documentation", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "properties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_elements")),
        sa.UniqueConstraint("element_type", "name", name=op.f("uq_elements_element_type_name")),
    )
    op.create_index(op.f("ix_elements_element_type"), "elements", ["element_type"], unique=False)
    op.create_index(op.f("ix_elements_layer"), "elements", ["layer"], unique=False)
    # Deux index partiels sur expression : du DDL statique, aucune valeur d'exécution.
    op.create_index(
        "uq_elements_vrf_ip_address",
        "elements",
        [sa.text("(properties ->> 'vrf')"), sa.text("(properties ->> 'ip_address')")],
        unique=True,
        postgresql_where=sa.text("properties ? 'vrf' AND properties ? 'ip_address'"),
    )
    op.create_index(
        "uq_elements_vrf_cidr",
        "elements",
        [sa.text("(properties ->> 'vrf')"), sa.text("(properties ->> 'cidr')")],
        unique=True,
        postgresql_where=sa.text("properties ? 'vrf' AND properties ? 'cidr'"),
    )
    op.create_table(
        "relationships",
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
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["elements.id"],
            name=op.f("fk_relationships_source_id_elements"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["elements.id"],
            name=op.f("fk_relationships_target_id_elements"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_relationships")),
    )
    op.create_index(
        op.f("ix_relationships_source_id"), "relationships", ["source_id"], unique=False
    )
    op.create_index(
        op.f("ix_relationships_target_id"), "relationships", ["target_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_relationships_target_id"), table_name="relationships")
    op.drop_index(op.f("ix_relationships_source_id"), table_name="relationships")
    op.drop_table("relationships")
    op.drop_index("uq_elements_vrf_cidr", table_name="elements")
    op.drop_index("uq_elements_vrf_ip_address", table_name="elements")
    op.drop_index(op.f("ix_elements_layer"), table_name="elements")
    op.drop_index(op.f("ix_elements_element_type"), table_name="elements")
    op.drop_table("elements")
```

- [ ] **Step 8: Vérifier que tout passe**

Run: `cd backend && ITEST tests/integration/test_graph_schema.py tests/integration/test_postgres.py tests/integration/test_diagram_store.py` puis `uv run mypy src migrations`
Expected: PASS ; mypy `Success`.

- [ ] **Step 9: Commit**

```bash
git add backend/src/ea/db/models backend/migrations/versions/0005_architecture_graph.py backend/tests/unit/test_migrations.py backend/tests/integration/conftest.py backend/tests/integration/test_graph_schema.py backend/tests/integration/test_diagram_store.py
git commit -m "feat(db): tables elements et relationships (révision 0005)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Le repository PostgreSQL — éléments, relations, refus

**Files:**
- Create: `backend/src/ea/repositories/architecture_store.py`
- Modify: `backend/tests/integration/conftest.py` (fixtures `graph_repository`, `graph_service`)
- Modify: `backend/tests/integration/test_archimate_graph.py:1-22` (docstring, import)
- Test: `backend/tests/unit/test_ipam_constraint_translation.py` (réécrit)

**Interfaces:**
- Consumes: `ElementRecord`, `RelationshipRecord`, `engine_at_head` (tâche 1).
- Produces:
  - `class PostgresArchitectureRepository(sessions: async_sessionmaker[AsyncSession])` avec `add_element`, `get_element`, `list_elements`, `count_elements`, `save_element`, `delete_element`, `add_relationship`, `get_relationship`, `list_relationships`, `delete_relationship` (signatures de `ArchitectureRepository`, `domain/ports.py`).
  - `element_row(element: Element) -> dict[str, Any]`, `relationship_row(relationship: Relationship) -> dict[str, Any]`, `element_from_row(row: ElementRecord) -> Element`, `relationship_from_row(row: RelationshipRecord) -> Relationship`.
  - `constraint_of(error: IntegrityError) -> str | None`, `rejected(element: Element, constraint: str | None) -> DomainError | None`.
  - Constantes `MAX_TRAVERSAL_DEPTH = 10`, `NAME_TAKEN`, `ADDRESS_TAKEN`, `PREFIX_TAKEN`.

- [ ] **Step 1: Réécrire le test unitaire de traduction des refus**

Remplacer tout `backend/tests/unit/test_ipam_constraint_translation.py` par :

```python
"""Which uniqueness constraint refused a write, told apart without a database.

PostgreSQL names the constraint it enforced; the constraints themselves are
proved against a real server in `tests/integration/test_ipam_subnet_constraint.py`.
What is checked here is the translation — a lost subnet declaration is not a
name clash, and an agent told "already named" would rename and retry forever.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

from ea.domain.archimate import ElementType as E
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    DuplicateElementError,
    DuplicateNetworkError,
)
from ea.domain.model import Element
from ea.repositories.architecture_store import (
    ADDRESS_TAKEN,
    NAME_TAKEN,
    PREFIX_TAKEN,
    constraint_of,
    rejected,
)
from tests.conftest import FIXED_NOW


def _network() -> Element:
    return Element.create(
        element_type=E.COMMUNICATION_NETWORK,
        name="DMZ",
        properties={"cidr": "10.0.1.0/24", "vrf": "dmz"},
        now=FIXED_NOW,
    )


def _host() -> Element:
    return Element.create(
        element_type=E.NODE,
        name="srv-01",
        properties={"ip_address": "10.0.1.12", "vrf": "dmz"},
        now=FIXED_NOW,
    )


def test_a_taken_prefix_is_a_duplicate_subnet() -> None:
    refusal = rejected(_network(), PREFIX_TAKEN)

    assert isinstance(refusal, DuplicateNetworkError)
    assert re.search(re.escape("10.0.1.0/24") + ".*'dmz'", str(refusal))


def test_a_taken_address_is_an_address_already_assigned() -> None:
    refusal = rejected(_host(), ADDRESS_TAKEN)

    assert isinstance(refusal, AddressAlreadyAssignedError)
    assert "10.0.1.12" in str(refusal)


def test_a_taken_name_is_still_a_name_clash() -> None:
    refusal = rejected(_network(), NAME_TAKEN)

    assert isinstance(refusal, DuplicateElementError)
    assert "already named 'DMZ'" in str(refusal)


def test_an_unknown_constraint_is_not_dressed_up_as_a_duplicate() -> None:
    """Neo4j's fallback was "already named"; an unrecognised refusal now stays itself."""
    assert rejected(_network(), "some_other_constraint") is None
    assert rejected(_network(), None) is None


def test_the_constraint_name_is_read_from_the_driver_exception_under_the_adapter() -> None:
    asyncpg_error = SimpleNamespace(constraint_name=PREFIX_TAKEN)
    adapter = Exception("duplicate key")
    adapter.__cause__ = asyncpg_error  # type: ignore[assignment]

    assert constraint_of(IntegrityError("INSERT", {}, adapter)) == PREFIX_TAKEN


def test_an_exception_without_a_constraint_name_gives_none() -> None:
    assert constraint_of(IntegrityError("INSERT", {}, Exception("boom"))) is None
```

- [ ] **Step 2: Vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_ipam_constraint_translation.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.repositories.architecture_store'`.

- [ ] **Step 3: Écrire le repository (CRUD et refus)**

Créer `backend/src/ea/repositories/architecture_store.py` :

```python
"""The PostgreSQL implementation of `ArchitectureRepository` and `IpamRepository`.

The graph is two tables (docs/adr/0033): an element is a row of `elements`, a
relationship a row of `relationships` whose two ends are foreign keys, so
deleting an element takes its links — and, since revision 0006, its documents
and its boxes on diagrams — by DDL rather than by code.

It takes the session factory, like the document and diagram stores, and opens
one unit of work per call. Every value is bound, the depth of a traversal
included: SQLAlchemy constructs compile to parameters.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    DomainError,
    DuplicateElementError,
    DuplicateNetworkError,
    ElementNotFoundError,
)
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, read_vrf
from ea.domain.model import Element, Relationship

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.ports import ElementFilter

logger = logging.getLogger(__name__)

#: A traversal deeper than this is a full-graph dump wearing a filter.
MAX_TRAVERSAL_DEPTH: Final = 10

#: The three uniqueness rules an element can break, by the name the server gives.
NAME_TAKEN: Final = "uq_elements_element_type_name"
ADDRESS_TAKEN: Final = "uq_elements_vrf_ip_address"
PREFIX_TAKEN: Final = "uq_elements_vrf_cidr"

#: A relationship insert is refused by a foreign key only when an end is missing.
_MISSING_END_PREFIX: Final = "fk_relationships_"


# --------------------------------------------------------------------------
# Mapping between the domain entities and their rows
# --------------------------------------------------------------------------


def element_row(element: Element) -> dict[str, Any]:
    """Every column of an element. Complete, so a save replaces the whole row."""
    return {
        "id": element.id,
        "element_type": element.element_type.value,
        "layer": element.element_type.layer.value,
        "aspect": element.element_type.aspect.value,
        "name": element.name,
        "description": element.description,
        "documentation": element.documentation,
        "properties": dict(element.properties),
        "created_at": element.created_at,
        "updated_at": element.updated_at,
    }


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
    )


def relationship_row(relationship: Relationship) -> dict[str, Any]:
    return {
        "id": relationship.id,
        "relationship_type": relationship.relationship_type.value,
        "source_id": relationship.source_id,
        "target_id": relationship.target_id,
        "source_type": relationship.source_type.value,
        "target_type": relationship.target_type.value,
        "name": relationship.name,
        "access_type": relationship.access_type.value if relationship.access_type else None,
        "directed": relationship.directed,
        "properties": dict(relationship.properties),
        "created_at": relationship.created_at,
    }


def relationship_from_row(row: RelationshipRecord) -> Relationship:
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


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def constraint_of(error: IntegrityError) -> str | None:
    """The name of the constraint that refused a write, as the server gave it.

    asyncpg carries it on its own exception; SQLAlchemy wraps that exception in
    a DBAPI adapter (`error.orig`), whose `__cause__` it is.
    """
    cause = getattr(error.orig, "__cause__", None)
    return cast("str | None", getattr(cause, "constraint_name", None))


def rejected(element: Element, constraint: str | None) -> DomainError | None:
    """The refusal a caller must be handed for this constraint, or `None`.

    `None` for a constraint this module does not know: an unexpected refusal is
    re-raised as it is and ends as a typed 500, never as a guess.
    """
    scope = read_vrf(element.properties)
    if constraint == ADDRESS_TAKEN:
        address = element.properties.get(ADDRESS_PROPERTY)
        return AddressAlreadyAssignedError(
            f"{address} is already assigned to another element in VRF {scope!r}"
        )
    if constraint == PREFIX_TAKEN:
        cidr = element.properties.get(PREFIX_PROPERTY)
        return DuplicateNetworkError(f"{cidr} is already declared in VRF {scope!r}")
    if constraint == NAME_TAKEN:
        return DuplicateElementError(
            f"an element of type {element.element_type.value} is already named {element.name!r}"
        )
    return None


async def _flush_refusing(session: AsyncSession, element: Element) -> None:
    try:
        await session.flush()
    except IntegrityError as error:
        refusal = rejected(element, constraint_of(error))
        if refusal is None:
            raise
        logger.info("element rejected by a uniqueness constraint", exc_info=error)
        raise refusal from error


def _rows_affected(result: object) -> int:
    return cast("CursorResult[Any]", result).rowcount


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def _matching(criteria: ElementFilter) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if criteria.element_types:
        clauses.append(
            ElementRecord.element_type.in_([t.value for t in criteria.element_types])
        )
    if criteria.layers:
        clauses.append(ElementRecord.layer.in_([layer.value for layer in criteria.layers]))
    if criteria.search:
        # `strpos` and not `ILIKE`: `%` and `_` typed in the search box stay
        # characters, as they were under Cypher's `CONTAINS`.
        clauses.append(
            func.strpos(func.lower(ElementRecord.name), func.lower(criteria.search)) > 0
        )
    return clauses


def _of_types(types: Sequence[RelationshipType]) -> list[ColumnElement[bool]]:
    if not types:
        return []
    return [RelationshipRecord.relationship_type.in_([t.value for t in types])]


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------


class PostgresArchitectureRepository:
    """`ArchitectureRepository` and `IpamRepository` over two tables."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # --- Elements ---------------------------------------------------------

    async def add_element(self, element: Element) -> Element:
        async with self._sessions.begin() as session:
            session.add(ElementRecord(**element_row(element)))
            await _flush_refusing(session, element)
        return element

    async def get_element(self, element_id: UUID) -> Element | None:
        async with self._sessions() as session:
            row = await session.get(ElementRecord, element_id)
            return element_from_row(row) if row is not None else None

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord)
            .where(*_matching(criteria))
            .order_by(ElementRecord.name, ElementRecord.id)
            .offset(criteria.offset)
            .limit(criteria.limit)
        )
        async with self._sessions() as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def count_elements(self, criteria: ElementFilter) -> int:
        statement = select(func.count()).select_from(ElementRecord).where(*_matching(criteria))
        async with self._sessions() as session:
            return int((await session.execute(statement)).scalar_one())

    async def save_element(self, element: Element) -> Element:
        async with self._sessions.begin() as session:
            row = await session.get(ElementRecord, element.id)
            if row is None:
                msg = f"no element with id {element.id}"
                raise ElementNotFoundError(msg)
            for column, value in element_row(element).items():
                setattr(row, column, value)
            await _flush_refusing(session, element)
        return element

    async def delete_element(self, element_id: UUID) -> bool:
        """Its relationships follow by the foreign keys."""
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementRecord).where(ElementRecord.id == element_id)
            )
        return bool(_rows_affected(deleted))

    # --- Relationships ----------------------------------------------------

    async def add_relationship(self, relationship: Relationship) -> Relationship:
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
        return relationship

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None:
        async with self._sessions() as session:
            row = await session.get(RelationshipRecord, relationship_id)
            return relationship_from_row(row) if row is not None else None

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Relationship, ...]:
        statement = select(RelationshipRecord).where(*_of_types(relationship_types))
        if element_id is not None:
            statement = statement.where(
                or_(
                    RelationshipRecord.source_id == element_id,
                    RelationshipRecord.target_id == element_id,
                )
            )
        statement = (
            statement.order_by(RelationshipRecord.created_at, RelationshipRecord.id)
            .offset(offset)
            .limit(limit)
        )
        async with self._sessions() as session:
            return tuple(relationship_from_row(row) for row in await session.scalars(statement))

    async def delete_relationship(self, relationship_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(RelationshipRecord).where(RelationshipRecord.id == relationship_id)
            )
        return bool(_rows_affected(deleted))
```

- [ ] **Step 4: Vérifier que le test unitaire passe**

Run: `cd backend && uv run pytest tests/unit/test_ipam_constraint_translation.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Brancher les fixtures d'intégration du graphe sur PostgreSQL**

Dans `backend/tests/integration/conftest.py`, remplacer les fixtures `graph_repository` et `graph_service` par :

```python
@pytest.fixture
def graph_repository(engine_at_head: AsyncEngine) -> PostgresArchitectureRepository:
    return PostgresArchitectureRepository(create_session_factory(engine_at_head))


@pytest.fixture
def graph_service(graph_repository: PostgresArchitectureRepository) -> ArchitectureService:
    return ArchitectureService(graph_repository)
```

Ajouter les imports `from ea.db.postgres import create_session_factory` et `from ea.repositories.architecture_store import PostgresArchitectureRepository`, retirer `from ea.repositories.archimate_graph import Neo4jArchitectureRepository`. La fixture `graph_driver` reste jusqu'à la tâche 6.

Dans `backend/tests/integration/test_archimate_graph.py`, remplacer la docstring et l'import :

```python
"""The architecture repository, against a real PostgreSQL.

Everything here is a claim about the database rather than about Python: that a
constraint actually rejects a duplicate, that a recursive traversal returns
what it should, that impact analysis walks each relationship the right way
round. None of it can be proved with a double.
"""
```

et remplacer la ligne `from ea.repositories.archimate_graph import Neo4jArchitectureRepository` par `from ea.repositories.architecture_store import PostgresArchitectureRepository` (retirer l'import s'il n'est plus utilisé : `uv run ruff check --fix` sur le fichier). Remplacer aussi la docstring de `test_an_update_removes_a_property_it_no_longer_carries` par `"""A save replaces the whole map, so a dropped key really goes."""`. Ajouter `pytest.mark.postgres` à `pytestmark` : `pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]`.

- [ ] **Step 6: Vérifier les tests de persistance**

Run: `cd backend && ITEST "tests/integration/test_archimate_graph.py -k TestElementPersistence or TestRelationshipPersistence"`
(la sélection `-k` se passe telle quelle : `... uv run pytest tests/integration/test_archimate_graph.py -k "TestElementPersistence or TestRelationshipPersistence" -q`)
Expected: PASS. Les classes de traversée échouent encore (`AttributeError`) : elles relèvent de la tâche 3.

- [ ] **Step 7: Commit**

```bash
git add backend/src/ea/repositories/architecture_store.py backend/tests/unit/test_ipam_constraint_translation.py backend/tests/integration/conftest.py backend/tests/integration/test_archimate_graph.py
git commit -m "feat(repositories): éléments et relations dans PostgreSQL

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Les traversées (voisinage, impact, cycle, relations, vue)

**Files:**
- Modify: `backend/src/ea/repositories/architecture_store.py`
- Test: `backend/tests/integration/test_archimate_graph.py` (classes existantes `TestContainmentCycles`, `TestRelationsOfOneElement`, `TestTraversals`, `TestImpactAnalysis`, `TestViewOfAChosenSetOfElements`, `test_a_missing_element_is_absent_rather_than_empty`)

**Interfaces:**
- Consumes: `PostgresArchitectureRepository`, `element_from_row`, `relationship_from_row`, `_of_types`, `MAX_TRAVERSAL_DEPTH` (tâche 2).
- Produces: méthodes `relations_of`, `neighbourhood`, `impacted_by`, `would_close_a_containment_cycle`, `view_of` (signatures de `ArchitectureRepository`) ; `ALONG_THE_ARROW: tuple[str, ...]`.

- [ ] **Step 1: Ajouter le test de recherche littérale qui échoue d'abord sur la sémantique**

Ajouter dans la classe `TestElementPersistence` de `backend/tests/integration/test_archimate_graph.py` :

```python
    async def test_the_catalogue_search_treats_wildcards_as_characters(
        self, graph_service: ArchitectureService
    ) -> None:
        """`%` and `_` are what the user typed, as under Cypher's `CONTAINS`."""
        await graph_service.create_element(element_type=E.NODE, name="db_01")
        await graph_service.create_element(element_type=E.NODE, name="dbx01")

        found = await graph_service.list_elements(ElementFilter(search="db_"))

        assert [element.name for element in found.items] == ["db_01"]
```

Avant d'écrire le test, vérifier le nom de la méthode et la forme de la réponse dans `test_the_catalogue_search_ignores_case` (ligne ~109 du même fichier) et les copier exactement si elles diffèrent de `list_elements(...)` / `.items`.

- [ ] **Step 2: Vérifier l'état de départ**

Run: `cd backend && ITEST tests/integration/test_archimate_graph.py`
Expected: le nouveau test PASS (la tâche 2 utilise `strpos`) ; FAIL sur toutes les classes de traversée avec `AttributeError: 'PostgresArchitectureRepository' object has no attribute ...`.

- [ ] **Step 3: Écrire les traversées**

Dans `backend/src/ea/repositories/architecture_store.py`, compléter l'import SQLAlchemy :

```python
from sqlalchemy import ColumnElement, Integer, and_, case, delete, func, literal_column, not_, or_, select
```

ajouter sous `TYPE_CHECKING` : `from sqlalchemy.sql.selectable import CTE` ; importer `GraphView` au niveau module : `from ea.domain.ports import ElementFilter, GraphView` (retirer `ElementFilter` du bloc `TYPE_CHECKING`).

Ajouter après `_of_types` :

```python
#: The relationships along which dependency runs source → target; every other
#: type carries it target → source. Read from the metamodel, never restated.
ALONG_THE_ARROW: Final = tuple(
    relationship.value for relationship in RelationshipType if relationship.impact_follows_direction
)

#: Relationships that build the containment tree, and so must stay acyclic.
_CONTAINMENT: Final = (RelationshipType.COMPOSITION.value, RelationshipType.AGGREGATION.value)


def _clamp_depth(depth: int) -> int:
    return max(1, min(depth, MAX_TRAVERSAL_DEPTH))


def _seed(element_id: UUID) -> CTE:
    """The start of a walk: the element itself at zero hops, or nothing if absent."""
    return (
        select(ElementRecord.id.label("id"), literal_column("0", Integer).label("hops"))
        .where(ElementRecord.id == element_id)
        .cte("reached", recursive=True)
    )


def _neighbourhood_walk(
    element_id: UUID, depth: int, types: Sequence[RelationshipType]
) -> CTE:
    """Every element within `depth` hops, whichever way each link points."""
    reached = _seed(element_id)
    link = RelationshipRecord
    step = (
        select(
            case((link.source_id == reached.c.id, link.target_id), else_=link.source_id).label(
                "id"
            ),
            (reached.c.hops + 1).label("hops"),
        )
        .select_from(reached)
        .join(link, or_(link.source_id == reached.c.id, link.target_id == reached.c.id))
        .where(reached.c.hops < depth, *_of_types(types))
    )
    return reached.union(step)


def _impact_walk(element_id: UUID, depth: int, types: Sequence[RelationshipType]) -> CTE:
    """Everything that depends on an element, each hop taken the way dependency runs.

    A hop along a type of `ALONG_THE_ARROW` leaves from its source; any other
    leaves from its target. That lets one walk mix both — forwards for
    `serving`, backwards for `composition` — without answering a different
    question halfway through.
    """
    reached = _seed(element_id)
    link = RelationshipRecord
    along = link.relationship_type.in_(ALONG_THE_ARROW)
    step = (
        select(
            case((along, link.target_id), else_=link.source_id).label("id"),
            (reached.c.hops + 1).label("hops"),
        )
        .select_from(reached)
        .join(
            link,
            or_(
                and_(along, link.source_id == reached.c.id),
                and_(not_(along), link.target_id == reached.c.id),
            ),
        )
        .where(reached.c.hops < depth, *_of_types(types))
    )
    return reached.union(step)


async def _links_within(
    session: AsyncSession, ids: Sequence[UUID], types: Sequence[RelationshipType]
) -> tuple[Relationship, ...]:
    """The relationships whose two ends are both among `ids`: a drawable sub-graph."""
    if not ids:
        return ()
    statement = (
        select(RelationshipRecord)
        .where(
            RelationshipRecord.source_id.in_(ids),
            RelationshipRecord.target_id.in_(ids),
            *_of_types(types),
        )
        .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
    )
    return tuple(relationship_from_row(row) for row in await session.scalars(statement))
```

Ajouter dans la classe `PostgresArchitectureRepository`, après `delete_relationship` :

```python
    # --- Traversals -------------------------------------------------------

    async def relations_of(
        self,
        element_id: UUID,
        *,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        async with self._sessions() as session:
            row = await session.get(ElementRecord, element_id)
            if row is None:
                return GraphView(elements=(), relationships=())
            statement = (
                select(RelationshipRecord)
                .where(
                    or_(
                        RelationshipRecord.source_id == element_id,
                        RelationshipRecord.target_id == element_id,
                    ),
                    *_of_types(relationship_types),
                )
                .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
            )
            links = tuple(relationship_from_row(link) for link in await session.scalars(statement))
            # A self-association names the element at both ends: it is listed once, first.
            others = sorted({end for link in links for end in (link.source_id, link.target_id)} - {element_id})
            around: tuple[Element, ...] = ()
            if others:
                found = await session.scalars(
                    select(ElementRecord)
                    .where(ElementRecord.id.in_(others))
                    .order_by(ElementRecord.name, ElementRecord.id)
                )
                around = tuple(element_from_row(other) for other in found)
        return GraphView(elements=(element_from_row(row), *around), relationships=links)

    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        walk = _neighbourhood_walk(element_id, _clamp_depth(depth), relationship_types)
        return await self._view_of_walk(walk, relationship_types)

    async def impacted_by(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        walk = _impact_walk(element_id, _clamp_depth(depth), relationship_types)
        return await self._view_of_walk(walk, relationship_types)

    async def view_of(self, element_ids: Sequence[UUID]) -> GraphView:
        if not element_ids:
            return GraphView(elements=(), relationships=())
        async with self._sessions() as session:
            found = await session.scalars(
                select(ElementRecord)
                .where(ElementRecord.id.in_(list(element_ids)))
                .order_by(ElementRecord.name, ElementRecord.id)
            )
            elements = tuple(element_from_row(row) for row in found)
            links = await _links_within(session, [element.id for element in elements], ())
        return GraphView(elements=elements, relationships=links)

    async def would_close_a_containment_cycle(self, source_id: UUID, target_id: UUID) -> bool:
        """Whether `source` is already contained, at any depth, under `target`.

        The walk has no depth: it stops because `UNION` discards a row it has
        already produced, so a cycle already in the data cannot loop it.
        """
        if source_id == target_id:
            return True
        below = (
            select(ElementRecord.id.label("id"))
            .where(ElementRecord.id == target_id)
            .cte("below", recursive=True)
        )
        below = below.union(
            select(RelationshipRecord.target_id.label("id"))
            .select_from(below)
            .join(RelationshipRecord, RelationshipRecord.source_id == below.c.id)
            .where(RelationshipRecord.relationship_type.in_(_CONTAINMENT))
        )
        statement = select(func.count()).select_from(below).where(below.c.id == source_id)
        async with self._sessions() as session:
            return bool((await session.execute(statement)).scalar_one())

    async def _view_of_walk(
        self, walk: CTE, relationship_types: Sequence[RelationshipType]
    ) -> GraphView:
        """The elements a walk reached, nearest first, and the links between them."""
        statement = (
            select(ElementRecord)
            .join(walk, walk.c.id == ElementRecord.id)
            .group_by(ElementRecord.id)
            .order_by(func.min(walk.c.hops), ElementRecord.name, ElementRecord.id)
        )
        async with self._sessions() as session:
            elements = tuple(element_from_row(row) for row in await session.scalars(statement))
            links = await _links_within(
                session, [element.id for element in elements], relationship_types
            )
        return GraphView(elements=elements, relationships=links)
```

- [ ] **Step 4: Vérifier que tout le contrat du graphe passe**

Run: `cd backend && ITEST tests/integration/test_archimate_graph.py` puis `uv run ruff format src && uv run ruff check src && uv run mypy src`
Expected: PASS (30 tests) ; ruff et mypy propres. Si `test_the_links_of_an_element_come_with_both_endpoints` ou un test de voisinage compare un ordre d'éléments, l'ordre produit ici est : l'élément de départ, puis les autres par distance, puis par nom. Adapter le code (jamais le test) si un test attend autre chose.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/repositories/architecture_store.py backend/tests/integration/test_archimate_graph.py
git commit -m "feat(repositories): voisinage, impact et cycles en CTE récursives

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Les lectures de l'IPAM

**Files:**
- Modify: `backend/src/ea/repositories/architecture_store.py`
- Modify: `backend/tests/integration/test_ipam_graph.py:1-30`
- Modify: `backend/tests/integration/test_ipam_subnet_constraint.py:1-46`

**Interfaces:**
- Consumes: `PostgresArchitectureRepository` (tâches 2-3), fixture `engine_at_head` (tâche 1).
- Produces: méthodes `networks() -> tuple[Element, ...]`, `addressed_elements() -> tuple[Element, ...]`, `element_at(address: str, *, vrf: str) -> Element | None`.

- [ ] **Step 1: Adapter les tests IPAM**

Dans `backend/tests/integration/test_ipam_graph.py` : docstring `"""The IP addressing against a real PostgreSQL.` (première ligne), `(p_vrf, p_ip_address)` → `(vrf, ip_address)` dans la docstring, import `Neo4jArchitectureRepository` → `from ea.repositories.architecture_store import PostgresArchitectureRepository`, et l'annotation de `graph_repository` dans chaque signature → `PostgresArchitectureRepository`. Ajouter `pytest.mark.postgres` à `pytestmark`.

Dans `backend/tests/integration/test_ipam_subnet_constraint.py` : même remplacement d'import et d'annotations, ajout de `pytest.mark.postgres`, retrait des imports `AsyncDriver` et `Settings` ; remplacer `test_it_is_declared_on_the_database` par :

```python
    async def test_it_is_declared_on_the_database(self, engine_at_head: AsyncEngine) -> None:
        async with engine_at_head.connect() as connection:
            definition = await connection.scalar(
                # A catalogue read with no runtime value: nothing to bind.
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_elements_vrf_cidr'")
            )

        assert definition is not None
        assert "UNIQUE" in definition
        assert "'vrf'" in definition and "'cidr'" in definition
```

avec les imports `from sqlalchemy import text` et `from sqlalchemy.ext.asyncio import AsyncEngine`.

- [ ] **Step 2: Vérifier l'échec**

Run: `cd backend && ITEST "tests/integration/test_ipam_graph.py tests/integration/test_ipam_subnet_constraint.py"` (deux chemins après `pytest`)
Expected: les tests d'unicité (création de doublons) PASS ; ceux qui interrogent l'inventaire FAIL avec `AttributeError: ... has no attribute 'networks'` (ou `addressed_elements`, `element_at`).

- [ ] **Step 3: Écrire les trois lectures**

Dans `backend/src/ea/repositories/architecture_store.py`, compléter l'import : `from sqlalchemy import ..., Text, ...` (ajouter `Text` à la liste) et `from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, VRF_PROPERTY, read_vrf`. Ajouter après `_of_types` :

```python
def _has(key: str) -> ColumnElement[bool]:
    """`properties ? key` — the predicate of the two partial unique indexes."""
    return ElementRecord.properties.op("?", is_comparison=True)(key)


def _text_of(key: str) -> ColumnElement[str]:
    """`properties ->> key`, the expression the two unique indexes are built on."""
    return ElementRecord.properties.op("->>", return_type=Text)(key)
```

Ajouter à la fin de la classe :

```python
    # --- IP address management (docs/adr/0020) ----------------------------
    # Deliberately unbounded: an occupancy figure computed from a page of the
    # inventory would be wrong, silently, and the inventory is bounded by how
    # many machines are modelled rather than by how large the graph is.

    async def networks(self) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord)
            .where(_has(PREFIX_PROPERTY))
            .order_by(_text_of(PREFIX_PROPERTY), ElementRecord.name)
        )
        async with self._sessions() as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def addressed_elements(self) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord).where(_has(ADDRESS_PROPERTY)).order_by(ElementRecord.name)
        )
        async with self._sessions() as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def element_at(self, address: str, *, vrf: str) -> Element | None:
        """At most one row: `uq_elements_vrf_ip_address` says so, and answers it."""
        statement = select(ElementRecord).where(
            _has(VRF_PROPERTY),
            _has(ADDRESS_PROPERTY),
            _text_of(VRF_PROPERTY) == vrf,
            _text_of(ADDRESS_PROPERTY) == address,
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).first()
            return element_from_row(row) if row is not None else None
```

- [ ] **Step 4: Vérifier**

Run: `cd backend && ITEST "tests/integration/test_ipam_graph.py tests/integration/test_ipam_subnet_constraint.py"` puis `uv run mypy src`
Expected: PASS (13 tests) ; mypy propre.

- [ ] **Step 5: Commit**

```bash
git add backend/src/ea/repositories/architecture_store.py backend/tests/integration/test_ipam_graph.py backend/tests/integration/test_ipam_subnet_constraint.py
git commit -m "feat(repositories): inventaire IPAM lu dans PostgreSQL

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Les attachements suivent l'élément par clé étrangère (révision 0006)

**Files:**
- Create: `backend/migrations/versions/0006_attachments_follow_elements.py`
- Create: `backend/tests/integration/test_element_cascade.py`
- Modify: `backend/src/ea/db/models/document.py`, `backend/src/ea/db/models/diagram.py`
- Modify: `backend/src/ea/domain/ports.py` (retrait d'`ElementAttachments`)
- Modify: `backend/src/ea/services/architecture.py` (retrait d'`AllAttachments` et du paramètre `attachments`)
- Modify: `backend/src/ea/repositories/document_store.py`, `backend/src/ea/repositories/diagram_store.py` (retrait de `discard_for_element`)
- Modify: `backend/src/ea/main.py:150-183`, `backend/src/ea/reindex.py:55-62` (retrait d'`AllAttachments` / `attachments=`)
- Modify: `backend/tests/conftest.py` (doubles et fixture `service`)
- Modify: `backend/tests/integration/conftest.py` (helper `ensure_elements`)
- Modify: `backend/tests/integration/test_document_store.py`, `test_document_index.py`, `test_diagram_store.py`
- Modify: `backend/tests/unit/test_migrations.py`, `test_document_service.py`, `test_diagram_service.py`, `test_indexing.py`
- Delete: `backend/tests/unit/test_architecture_delete.py`

**Interfaces:**
- Consumes: `PostgresArchitectureRepository`, `element_row` (tâche 2), `engine_at_head` (tâche 1).
- Produces: `ArchitectureService(repository, *, clock=...)` sans `attachments` ; `DocumentRepository` et `DiagramRepository` sans `discard_for_element` ; helper de test `ensure_elements(engine: AsyncEngine, *element_ids: UUID) -> None` dans `tests/integration/conftest.py`.

- [ ] **Step 1: Écrire les tests qui échouent**

Dans `backend/tests/unit/test_migrations.py`, remplacer `test_the_element_a_document_names_carries_no_foreign_key` et `test_a_diagram_node_names_its_element_without_a_foreign_key` par :

```python
def test_a_document_follows_its_element_by_the_foreign_key() -> None:
    """The element is a row since docs/adr/0033, so the cascade is DDL."""
    element_id = Base.metadata.tables["element_documents"].c.element_id
    key = next(iter(element_id.foreign_keys))

    assert key.column.table.name == "elements"
    assert key.ondelete == "CASCADE"
    assert element_id.index is True


def test_a_diagram_node_follows_its_element_by_the_foreign_key() -> None:
    element_id = Base.metadata.tables["diagram_nodes"].c.element_id
    key = next(iter(element_id.foreign_keys))

    assert key.column.table.name == "elements"
    assert key.ondelete == "CASCADE"
    assert element_id.index is True
```

Dans la docstring de `test_the_passages_table_can_state_the_foreign_key_the_documents_could_not`, remplacer le second paragraphe par : `Both cascades are DDL now (docs/adr/0033); this one was the first.` et la docstring du module, première phrase du deuxième paragraphe, par `The graph is in PostgreSQL too since docs/adr/0033: every change to a table is a versioned script,`.

Ajouter à `backend/tests/integration/conftest.py` (imports : `from datetime import UTC, datetime`, `from uuid import UUID`, `from sqlalchemy.dialects.postgresql import insert`, `from ea.db.models.architecture import ElementRecord`, `from ea.domain.archimate import ElementType`, `from ea.domain.model import Element`, `from ea.repositories.architecture_store import element_row`) :

```python
async def ensure_elements(engine: AsyncEngine, *element_ids: UUID) -> None:
    """Rows in `elements` for ids a test made up.

    Since revision 0006 a document and a diagram box name their element by a
    foreign key, so a test about the document or diagram store must first give
    that id an element — any element will do.
    """
    now = datetime.now(UTC)
    rows = [
        element_row(
            Element.create(
                element_type=ElementType.NODE,
                name=f"node-{element_id}",
                now=now,
                element_id=element_id,
            )
        )
        for element_id in element_ids
    ]
    if not rows:
        return
    async with create_session_factory(engine).begin() as session:
        await session.execute(insert(ElementRecord).values(rows).on_conflict_do_nothing())
```

Créer `backend/tests/integration/test_element_cascade.py` :

```python
"""Deleting an element takes everything attached to it, by the foreign keys.

Since revision 0006 (docs/adr/0033) the cascade is DDL: the relationships, the
documents, their passages and the boxes on diagrams all follow the element in
the one transaction that deletes it. Nothing is left for a service to discard.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.models.chunk import DocumentChunk
from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.documents import Document
from ea.domain.model import Element, Relationship
from ea.domain.search import EMBEDDING_DIMENSIONS, EmbeddedChunk
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.document_store import PostgresDocumentRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

FIXED_NOW = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


async def test_deleting_an_element_takes_its_links_documents_passages_and_boxes(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    documents = PostgresDocumentRepository(sessions)
    diagrams = PostgresDiagramRepository(sessions)
    doomed = await graph.add_element(
        Element.create(element_type=E.APPLICATION_COMPONENT, name="Billing", now=FIXED_NOW)
    )
    spared = await graph.add_element(
        Element.create(element_type=E.NODE, name="db-01", now=FIXED_NOW)
    )
    await graph.add_relationship(
        Relationship.between(R.ASSOCIATION, doomed, spared, now=FIXED_NOW)
    )
    document = await documents.add(
        Document.create(element_id=doomed.id, filename="a.md", content="# A\n", now=FIXED_NOW),
        [
            EmbeddedChunk(
                ordinal=0,
                heading_path=("A",),
                text="texte",
                embedding=(1.0,) + (0.0,) * (EMBEDDING_DIMENSIONS - 1),
                model="test-embed",
            )
        ],
    )
    kept = await documents.add(
        Document.create(element_id=spared.id, filename="b.md", content="# B\n", now=FIXED_NOW)
    )
    diagram = await diagrams.add(Diagram.create(name="Vente", description="", now=FIXED_NOW))
    await diagrams.replace_layout(
        diagram.id,
        [DiagramNode(doomed.id, 0, 0), DiagramNode(spared.id, 1, 1)],
        now=FIXED_NOW,
    )

    assert await graph.delete_element(doomed.id) is True

    assert await graph.list_relationships(element_id=spared.id) == ()
    assert await documents.get(document.id) is None
    assert await documents.get(kept.id) is not None
    assert await diagrams.nodes_of(diagram.id) == (DiagramNode(spared.id, 1, 1),)
    async with sessions() as session:
        passages = await session.scalar(select(func.count()).select_from(DocumentChunk))
    assert passages == 0
```

Ajouter à la fin de `backend/tests/integration/test_graph_schema.py` :

```python
async def test_revision_0006_refuses_to_run_before_the_graph_is_imported(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """Otherwise every document and every box would look orphaned, and be purged."""
    await asyncio.to_thread(command.upgrade, alembic_config, "0005")
    try:
        async with postgres_engine.begin() as connection:
            await connection.execute(
                # Test setup with literal values only: a document with no element yet.
                text(
                    "INSERT INTO element_documents (id, element_id, filename, content, "
                    "created_at, updated_at) VALUES (gen_random_uuid(), gen_random_uuid(), "
                    "'a.md', '# A', now(), now())"
                )
            )

        with pytest.raises(Exception, match="make graph-import"):
            await asyncio.to_thread(command.upgrade, alembic_config, "head")
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


async def test_revision_0006_purges_true_orphans_once_elements_exist(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    await asyncio.to_thread(command.upgrade, alembic_config, "0005")
    try:
        async with postgres_engine.begin() as connection:
            # Test setup with literal values only.
            await connection.execute(
                text(
                    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, "
                    "updated_at) VALUES ('00000000-0000-0000-0000-000000000001', 'node', "
                    "'technology', 'active_structure', 'db-01', now(), now())"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO element_documents (id, element_id, filename, content, "
                    "created_at, updated_at) VALUES "
                    "(gen_random_uuid(), '00000000-0000-0000-0000-000000000001', 'kept.md', "
                    "'# K', now(), now()), "
                    "(gen_random_uuid(), gen_random_uuid(), 'orphan.md', '# O', now(), now())"
                )
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "head")

        async with postgres_engine.connect() as connection:
            names = await connection.scalars(text("SELECT filename FROM element_documents"))
            assert list(names) == ["kept.md"]
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
```

avec les imports `import asyncio`, `from alembic import command`, `from alembic.config import Config`. Avant d'écrire l'insertion dans `elements`, vérifier les valeurs exactes de `ElementType.NODE.layer.value` et `.aspect.value` (`uv run python -c "from ea.domain.archimate import ElementType as E; print(E.NODE.layer.value, E.NODE.aspect.value)"`) et les recopier.

- [ ] **Step 2: Vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_migrations.py -q` puis `ITEST "tests/integration/test_element_cascade.py tests/integration/test_graph_schema.py"`
Expected: unitaires FAIL (`StopIteration`, pas de clé étrangère) ; `test_element_cascade` FAIL sur `documents.get(document.id) is None` ; les deux tests `revision_0006` FAIL (la révision n'existe pas : `head` réussit sans erreur, ou l'orphelin reste).

- [ ] **Step 3: Écrire la révision 0006 et poser les clés sur les modèles**

Créer `backend/migrations/versions/0006_attachments_follow_elements.py` :

```python
"""les documents et les boîtes de diagramme suivent leur élément

Revision ID: 0006
Revises: 0005
Date: 2026-09-14

L'élément est une ligne depuis 0005 (docs/adr/0033) : `element_documents` et
`diagram_nodes` peuvent enfin le désigner par une clé étrangère, et la cascade
explicite de `ArchitectureService.delete_element` disparaît.

Deux étapes précèdent les clés. La première **refuse** de tourner si `elements`
est vide alors que des documents ou des boîtes existent : l'image applique
`alembic upgrade head` à chaque démarrage, et sans ce refus un déploiement
arrivé avant `make graph-import` prendrait chaque attachement pour un orphelin
et l'effacerait. La seconde supprime les vrais orphelins — ceux que l'ADR 0017
acceptait quand la suppression d'un élément ne pouvait pas atteindre ses
documents — sans quoi la clé ne pourrait pas être posée.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_ATTACHED = ("element_documents", "diagram_nodes")


def upgrade() -> None:
    bind = op.get_bind()
    elements = sa.table("elements", sa.column("id"))
    has_elements = bind.scalar(sa.select(sa.exists().select_from(elements)))
    attached = [sa.table(name, sa.column("element_id")) for name in _ATTACHED]
    has_attachments = any(
        bind.scalar(sa.select(sa.exists().select_from(table))) for table in attached
    )
    if not has_elements and has_attachments:
        msg = (
            "la table elements est vide alors que des documents ou des diagrammes existent : "
            "importe d'abord le graphe (make graph-import, docs/adr/0033)"
        )
        raise RuntimeError(msg)

    for table in attached:
        purged = bind.execute(
            sa.delete(table).where(~sa.exists().where(elements.c.id == table.c.element_id))
        ).rowcount
        logger.warning("%s : %d ligne(s) orpheline(s) supprimée(s)", table.name, purged)

    op.create_foreign_key(
        op.f("fk_element_documents_element_id_elements"),
        "element_documents",
        "elements",
        ["element_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        op.f("fk_diagram_nodes_element_id_elements"),
        "diagram_nodes",
        "elements",
        ["element_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    # Les lignes supprimées comme orphelines ne reviennent pas.
    op.drop_constraint(
        op.f("fk_diagram_nodes_element_id_elements"), "diagram_nodes", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_element_documents_element_id_elements"), "element_documents", type_="foreignkey"
    )
```

Dans `backend/src/ea/db/models/document.py` : colonne `element_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("elements.id", ondelete="CASCADE"), nullable=False, index=True)`, import `ForeignKey` ; remplacer le paragraphe de docstring **`element_id` has no foreign key…** par :

```
**`element_id` follows its element by a foreign key.** The element is a row of
`elements` since docs/adr/0033, so deleting it takes its documents — and, by
`document_chunks.document_id`, their passages — in the same transaction. The
index is what makes "the documents of this element" a cheap question.
```

Dans `backend/src/ea/db/models/diagram.py` : `element_id: Mapped[UUID] = mapped_column(Uuid, ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True, index=True)` ; remplacer le paragraphe **`diagram_nodes.element_id` has no foreign key…** par :

```
**Both keys are foreign keys.** `element_id` names a row of `elements` since
docs/adr/0033, and `diagram_id` a row next door: deleting either an element
or a diagram takes its boxes by DDL.
```

et le commentaire au-dessus de `element_id` par `# Indexed on its own: the primary key starts with \`diagram_id\`, so it cannot answer "every box of this element", which the cascade asks.`

- [ ] **Step 4: Retirer le port de cascade**

`backend/src/ea/domain/ports.py` : supprimer la classe `ElementAttachments` ; `class DocumentRepository(Protocol):` et `class DiagramRepository(Protocol):` ; supprimer le paragraphe de la docstring de `DiagramRepository` qui commence par « An `ElementAttachments` too » ; dans la docstring de `ArchitectureRepository`, remplacer « the only implementation talks to Neo4j over Bolt » par « the only implementation talks to PostgreSQL over asyncpg » ; dans celle d'`IpamRepository`, « satisfied by the same Neo4j class » → « satisfied by the same PostgreSQL class », et « Neo4j, which is the whole reason » → « PostgreSQL, which is the whole reason ».

`backend/src/ea/services/architecture.py` : supprimer la classe `AllAttachments`, l'import `ElementAttachments`, le paramètre `attachments` et `self._attachments` ; remplacer `delete_element` par :

```python
    async def delete_element(self, element_id: UUID) -> None:
        """Remove an element together with everything attached to it.

        Its relationships, its documents with their passages and its boxes on
        diagrams follow by foreign keys, in the one transaction that deletes
        it — see docs/adr/0033.
        """
        require_editor()
        if not await self._repository.delete_element(element_id):
            msg = f"no element with id {element_id}"
            raise ElementNotFoundError(msg)
        logger.info(
            "element deleted, with everything attached to it",
            extra={"action": "deleted", "element_id": str(element_id)},
        )
```

`backend/src/ea/repositories/document_store.py` et `backend/src/ea/repositories/diagram_store.py` : supprimer la méthode `discard_for_element` ; dans la docstring de module de `document_store.py`, « exactly as its Neo4j » → « exactly as the architecture store ».

`backend/src/ea/main.py` : retirer `AllAttachments` de l'import, et remplacer

```python
                app.state.architecture_service = ArchitectureService(
                    repository,
                    attachments=AllAttachments(
                        *(store for store in (attachments, diagram_store) if store is not None)
                    ),
                )
```

par `app.state.architecture_service = ArchitectureService(repository)` (supprimer aussi les deux lignes de commentaire au-dessus).

`backend/src/ea/reindex.py` : remplacer `ArchitectureService(Neo4jArchitectureRepository(driver, database=settings.neo4j_database), attachments=documents,)` par `ArchitectureService(Neo4jArchitectureRepository(driver, database=settings.neo4j_database))` (la tâche 6 remplace le repository).

- [ ] **Step 5: Mettre à jour les doubles et les tests unitaires**

`backend/tests/conftest.py` : retirer `AllAttachments` de l'import ; supprimer `discard_for_element` de `InMemoryDocuments` (~ligne 454) et de `InMemoryDiagrams` (~ligne 558) ; fixture `service` :

```python
@pytest.fixture
def service(repository: InMemoryRepository) -> ArchitectureService:
    """The service wired to the in-memory graph and to a clock that never moves."""
    return ArchitectureService(repository, clock=lambda: FIXED_NOW)
```

Si d'autres fixtures demandaient `documents`/`diagrams` uniquement pour `service`, elles restent inchangées (elles les demandent pour elles-mêmes).

Supprimer `backend/tests/unit/test_architecture_delete.py`.

Supprimer ces tests, dont le sujet — la cascade faite par le service — est désormais `test_element_cascade.py` :
- `tests/unit/test_diagram_service.py::…::test_deleting_an_element_removes_its_boxes_from_every_diagram` (~ligne 194)
- `tests/unit/test_document_service.py::…::test_deleting_the_element_takes_its_documents_with_it` (~ligne 252)
- `tests/unit/test_document_service.py::…::test_an_architecture_service_without_a_store_still_deletes_elements` (~ligne 283)
- `tests/unit/test_indexing.py::…::test_deleting_the_element_empties_the_index_of_its_documents` (~ligne 234)

Dans `tests/unit/test_document_service.py`, la docstring de module : remplacer la phrase sur le nœud Neo4j par « The element and the document are both rows since docs/adr/0033, so "attach to an element that exists" is still checked here, for a readable 404 rather than a foreign-key violation. »

- [ ] **Step 6: Mettre à jour les tests d'intégration des stores relationnels**

`backend/tests/integration/test_document_store.py` : supprimer `test_discarding_an_element_removes_its_documents_and_no_others` et `test_discarding_an_element_that_had_nothing_attached_is_not_an_error` ; remplacer la fixture `documents` par :

```python
class DocumentsOnStoredElements(PostgresDocumentRepository):
    """The repository under test, giving each element id it is handed a row first.

    Since revision 0006 a document names its element by a foreign key; what is
    under test here is the document store, not where elements come from.
    """

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(create_session_factory(engine))
        self._engine = engine

    async def add(self, document: Document, chunks: Sequence[EmbeddedChunk] = ()) -> Document:
        await ensure_elements(self._engine, document.element_id)
        return await super().add(document, chunks)


@pytest.fixture
def documents(engine_at_head: AsyncEngine) -> PostgresDocumentRepository:
    return DocumentsOnStoredElements(engine_at_head)
```

avec les imports `from collections.abc import Sequence`, `from ea.domain.search import EmbeddedChunk`, `from tests.integration.conftest import ensure_elements` ; retirer ensuite les imports inutilisés (`uv run ruff check --fix tests/integration/test_document_store.py`).

`backend/tests/integration/test_document_index.py` : même remplacement de la fixture `documents` (même classe `DocumentsOnStoredElements`, recopiée dans ce fichier) ; remplacer `test_deleting_an_element_deletes_the_passages_of_its_documents` par :

```python
    async def test_deleting_an_element_deletes_the_passages_of_its_documents(
        self, documents: PostgresDocumentRepository, engine_at_head: AsyncEngine
    ) -> None:
        """Two foreign keys in a row: element → document → passage."""
        element = uuid4()
        await documents.add(a_document(element, "a.md"), passages((0, ("A",), "texte")))

        await PostgresArchitectureRepository(create_session_factory(engine_at_head)).delete_element(
            element
        )

        assert await documents.search(axis(0), model=MODEL) == ()
```

avec l'import `from ea.repositories.architecture_store import PostgresArchitectureRepository`.

`backend/tests/integration/test_diagram_store.py` : supprimer `test_discarding_an_element_removes_its_boxes_from_every_diagram_and_no_others` ; remplacer la fixture `diagrams` par :

```python
class DiagramsOnStoredElements(PostgresDiagramRepository):
    """Each element a layout names gets a row first — foreign key since 0006."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(create_session_factory(engine))
        self._engine = engine

    async def replace_layout(
        self, diagram_id: UUID, nodes: Sequence[DiagramNode], *, now: datetime
    ) -> bool:
        await ensure_elements(self._engine, *(node.element_id for node in nodes))
        return await super().replace_layout(diagram_id, nodes, now=now)


@pytest.fixture
def diagrams(engine_at_head: AsyncEngine) -> PostgresDiagramRepository:
    return DiagramsOnStoredElements(engine_at_head)
```

avec les imports `from collections.abc import Sequence`, `from uuid import UUID`, `from tests.integration.conftest import ensure_elements` ; mettre à jour la docstring de module : « and discarding an element touches no other » → « and a box names an element by the foreign key ».

- [ ] **Step 7: Vérifier**

Run: `cd backend && uv run pytest tests/unit tests/e2e -q` puis `ITEST tests/integration -m postgres` (la marque passe après le chemin : `... uv run pytest tests/integration -m postgres -q`) puis `uv run mypy src migrations && uv run ruff check src tests migrations`
Expected: PASS partout ; mypy et ruff propres.

- [ ] **Step 8: Commit**

```bash
git add -A backend
git commit -m "feat(db): documents et diagrammes suivent leur élément par clé étrangère (révision 0006)

Le port ElementAttachments et la cascade du service disparaissent : la
suppression d'un élément est une seule transaction.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: L'application sur PostgreSQL seul, Neo4j retiré du code

**Files:**
- Modify: `backend/src/ea/main.py`, `backend/src/ea/reindex.py`, `backend/src/ea/core/config.py`, `backend/src/ea/core/logging.py`, `backend/pyproject.toml`, `backend/uv.lock`
- Modify: `backend/tests/conftest.py:195-212`, `backend/tests/integration/conftest.py`, `backend/tests/integration/throwaway.py`, `backend/tests/integration/test_throwaway_guards.py`, `backend/tests/integration/test_application_boot.py`, `backend/tests/unit/test_config.py`, `backend/tests/unit/test_logging.py`, `backend/tests/unit/test_reindex.py`
- Delete: `backend/src/ea/db/neo4j.py`, `backend/src/ea/db/schema.py`, `backend/src/ea/repositories/archimate_graph.py`, `backend/tests/unit/test_neo4j_driver.py`, `backend/tests/unit/test_schema.py`, `backend/tests/unit/test_cypher_tracing.py`

**Interfaces:**
- Consumes: `PostgresArchitectureRepository` (tâches 2-4), `ArchitectureService(repository)` (tâche 5).
- Produces: `Settings` sans champs `neo4j_*` ni `log_cypher` ; `ea.core.logging` sans `CYPHER_LOGGER` ; le lifespan construit `PostgresArchitectureRepository(app.state.db_sessions)` quand `postgres_enabled` et qu'aucun `architecture_service` n'est injecté.

- [ ] **Step 1: Réécrire le test de démarrage (échoue tant que l'app ouvre Neo4j)**

Remplacer tout `backend/tests/integration/test_application_boot.py` par :

```python
"""Booting the real application against the real database.

Everywhere else the service is injected and the lifespan never runs. This is the
one place that proves the assembled process works: the pool opens, the graph is
built on it, and a request reaches PostgreSQL and comes back.
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.main import create_app

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


async def test_the_application_boots_and_serves_the_catalogue(
    engine_at_head: AsyncEngine,
) -> None:
    """`engine_at_head` is requested for its schema and its guard, not used directly."""
    app = create_app(Settings(debug=True, postgres_enabled=True, auth_enabled=False))
    transport = httpx.ASGITransport(app=app)

    async with (
        httpx.AsyncClient(transport=transport, base_url="http://test") as client,
        app.router.lifespan_context(app),
    ):
        created = await client.post(
            "/elements", json={"element_type": "node", "name": "boot-check-01"}
        )
        listed = await client.get("/elements", params={"search": "boot-check"})

    assert created.status_code == 201
    assert [item["name"] for item in listed.json()["items"]] == ["boot-check-01"]
```

Dans `backend/tests/unit/test_reindex.py`, fixture `steps` : remplacer la ligne `engine, embedder, driver = (Recorder(log, name) for name in ("engine", "embedder", "driver"))` par `engine, embedder = (Recorder(log, name) for name in ("engine", "embedder"))`, supprimer `monkeypatch.setattr(script, "create_driver", lambda _: driver)`, remplacer `monkeypatch.setattr(script, "Neo4jArchitectureRepository", lambda *a, **k: object())` par `monkeypatch.setattr(script, "PostgresArchitectureRepository", lambda *a, **k: object())` ; dans `test_it_closes_what_it_opened` et partout où l'ensemble apparaît, `{"engine.closed", "embedder.closed", "driver.closed"}` → `{"engine.closed", "embedder.closed"}`.

- [ ] **Step 2: Vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_reindex.py -q` puis `ITEST tests/integration/test_application_boot.py`
Expected: `test_reindex` FAIL (`AttributeError: module 'ea.reindex' has no attribute 'PostgresArchitectureRepository'`) ; le démarrage FAIL (le lifespan tente Bolt sur 127.0.0.1:7687 et le garde réseau ou la connexion refuse).

- [ ] **Step 3: Recâbler `main.py` et `reindex.py`**

`backend/src/ea/main.py` :
- retirer `from ea.db.neo4j import create_driver, prepare_database` et `from ea.repositories.archimate_graph import Neo4jArchitectureRepository` ; ajouter `from ea.repositories.architecture_store import PostgresArchitectureRepository` ;
- dans `lifespan`, renommer la variable locale `attachments` en `document_store` (toutes ses occurrences dans la fonction), supprimer tout le bloc `if open_graph:` qui ouvre le pilote, et insérer à la fin du bloc `if settings.postgres_enabled:` (après le `logger.info("relational store ready …")`) :

```python
                if open_graph:
                    repository = PostgresArchitectureRepository(app.state.db_sessions)
                    app.state.architecture_service = ArchitectureService(repository)
                    # The IP addressing is a reading of that same graph and adds
                    # no store, so it is built from the very repository above —
                    # see docs/adr/0020.
                    app.state.ipam_service = IpamService(app.state.architecture_service, repository)
```

- remplacer la docstring de `_lifespan` par :

```python
    """Start and stop everything the process owns, however it was assembled.

    PostgreSQL holds the whole model since docs/adr/0033 — the graph, the
    documents and their passages, the diagrams — so its pool is opened and
    checked first, and a process that cannot reach it does not start. Its
    schema is Alembic's, applied before the process starts, never here.

    The embedding service owns an HTTP connection pool, so it is built once
    and closed here, and it is *probed* at boot: the model configured must
    answer vectors of the width the column was created with. The MCP transport
    keeps its sessions in a manager that has to be running before `/mcp`
    answers anything; it is picked up off `app.state`, where `_mount_mcp` left
    it, because the manager only exists once the app it is mounted on does.

    An `AsyncExitStack` composes them: an app built with `architecture_service=`
    opens no graph but must still start the session manager.
    """
```

- dans la docstring de `create_app`, remplacer « Passing `architecture_service` swaps the graph for a double, so an API test never needs a running database — and, conversely, an app built without one opens the driver on startup. » par « Passing `architecture_service` swaps the graph for a double, so an API test never needs a running database — and, conversely, an app built without one builds it on the relational store at startup. » ;
- la clé `"graph": open_graph` du log de démarrage reste.

`backend/src/ea/reindex.py` : remplacer les imports `from ea.db.neo4j import create_driver` et `from ea.repositories.archimate_graph import Neo4jArchitectureRepository` par `from ea.repositories.architecture_store import PostgresArchitectureRepository` ; remplacer la fonction `reindex` par :

```python
async def reindex(settings: Settings) -> int:
    """Re-cut and re-embed every stored document, and say how many.

    Everything is checked before the first document is read — the store answers
    and the model is the width of the column — so a misconfigured run stops at
    once instead of halfway through a corpus.

    The architecture service is built because `DocumentService` owns the rule
    that an element must exist before a file hangs off it. Reindexing never
    asks it anything; it shares the relational store, so it costs nothing.
    """
    engine = create_engine(settings)
    embedder = build_embedder(settings)
    try:
        await check_connectivity(engine)
        await embedder.probe()
        sessions = create_session_factory(engine)
        documents = PostgresDocumentRepository(sessions)
        architecture = ArchitectureService(PostgresArchitectureRepository(sessions))
        service = DocumentService(documents, architecture, indexer=DocumentIndexer(embedder))
        # An operator's script has no request behind it, so it runs as SYSTEM.
        with acting_as(SYSTEM):
            return await service.reindex_all()
    finally:
        await embedder.aclose()
        await engine.dispose()
```

- [ ] **Step 4: Retirer la configuration, les journaux et le pilote Neo4j**

`backend/src/ea/core/config.py` : supprimer `log_cypher`, tout le bloc `# --- Neo4j …` (champs `neo4j_*`), et le validateur `_require_a_neo4j_password_outside_debug`. Remplacer le commentaire au-dessus des trois flux par « The noisy streams, each behind its own switch and each off. They are deliberately *not* opened by `log_level=DEBUG`: a developer wanting to see our own reasoning in detail is not asking for every SELECT and every HTTP round trip at once. » Remplacer le commentaire de tête du bloc PostgreSQL (« Audit and scheduled work will live here rather than in Neo4j — see docs/adr/0004 for the split … ») par « The one database: the architecture graph (docs/adr/0033), the documents and their index (docs/adr/0017, 0019), the diagrams (docs/adr/0031). Authentication is not here: it is the Keycloak realm `ea`, docs/adr/0032. » ; « exactly as it already is for the graph » → « since it holds the whole model » ; « The pool bounds a slow or wedged server the same way the Neo4j one does. » → « The pool bounds a slow or wedged server. » ; dans `_require_a_postgres_password_when_the_store_is_used`, « Same rule as the graph, but owed only by a process that connects. » → « A deployed instance talking to an unauthenticated database is a breach — owed only by a process that connects. »

`backend/src/ea/core/logging.py` : supprimer la constante `CYPHER_LOGGER` et son commentaire, et les deux entrées `CYPHER_LOGGER: {...}` et `"neo4j": {...}` du dictionnaire des loggers ; dans la docstring de tête, « the Cypher, the SQL and the embedding round trips » → « the SQL and the embedding round trips » ; dans le commentaire ligne ~71, retirer « the Neo4j driver » de l'énumération.

`backend/pyproject.toml` : supprimer la ligne `"neo4j>=5.26",` ; marqueur `integration` : `"integration: needs the throwaway local PostgreSQL — \`make test-integration\` starts it",`. Puis `cd backend && uv lock`.

Supprimer : `src/ea/db/neo4j.py`, `src/ea/db/schema.py`, `src/ea/repositories/archimate_graph.py`, `tests/unit/test_neo4j_driver.py`, `tests/unit/test_schema.py`, `tests/unit/test_cypher_tracing.py`.

- [ ] **Step 5: Retirer Neo4j des tests**

`backend/tests/conftest.py`, fixture `_database_credentials_in_the_environment` : `for variable in ("EA_POSTGRES_PASSWORD",):` et docstring « `Settings` refuses an empty PostgreSQL password outside debug since `postgres_enabled` defaults to on (docs/adr/0017) — so a suite … » ; ligne ~133 et ~148 : retirer « Neo4j, » et « the Neo4j driver, » des énumérations ; ligne ~665 « exactly as the Neo4j class does » → « exactly as the PostgreSQL class does ».

`backend/tests/integration/conftest.py` : supprimer `WIPE`, la fixture `graph_driver` et les imports `os`, `AsyncDriver`, `create_driver`, `apply_schema`, `DESTRUCTIVE_OPT_IN`, `refuse_a_shared_graph` ; remplacer la docstring de module par :

```python
"""Fixtures for the tests that talk to a real PostgreSQL.

It is the **throwaway container** from `docker-compose.yml`, published on
127.0.0.1 and nowhere else — never the shared database (docs/adr/0024). Every
test here destroys what it touches: it ends with `alembic downgrade base`,
which drops every table, the graph included since docs/adr/0033.

That is why the address, not a setting, is the guard. The shared database is
the *default* in `Settings`, and `backend/.env` names it too, so a fixture that
trusted configuration would be one forgotten variable away from dropping it.
The decision lives in `throwaway.py`, where it is tested without a database;
`postgres_engine` only acts on it, and `alembic_config` stands behind it, so no
migration runs without the guard.

A refusal is a skip whose message names the host, not a failure: a bare
`uv run pytest` stays useful off the network and destroys nothing.
`make test-integration` starts the container and points at it.
"""
```

`backend/tests/integration/throwaway.py` : supprimer `DESTRUCTIVE_OPT_IN`, `SHARED_BOLT_PORT`, `refuse_a_shared_graph`, l'import `urlsplit`, et les deux paragraphes de docstring sur le graphe (« Loopback is no longer sufficient for the graph … »). Remplacer « Both stores have a shared instance on the cluster » par « The database has a shared instance ».

`backend/tests/integration/test_throwaway_guards.py` : supprimer la classe `TestTheGraph` et l'import `refuse_a_shared_graph` ; docstring : « Both stores are wiped by the suite in this directory — the graph between every case, PostgreSQL by `alembic downgrade base` — and both have a shared instance » → « The database is wiped by the suite in this directory, by `alembic downgrade base`, and has a shared instance ».

`backend/tests/unit/test_config.py` : supprimer `test_neo4j_connection_is_read_from_the_environment`, `test_the_neo4j_password_never_appears_in_a_repr`, `test_an_empty_neo4j_password_is_refused_outside_debug`, `test_an_empty_neo4j_password_is_tolerated_in_debug` ; retirer l'argument `neo4j_password="x"` des deux appels restants (lignes ~114 et ~126). Ajouter :

```python
def test_the_postgres_password_never_appears_in_a_repr() -> None:
    """Settings end up in log lines and tracebacks; the password must not."""
    settings = Settings(postgres_password="s3cret")

    assert "s3cret" not in repr(settings)
```

`backend/tests/unit/test_logging.py` : retirer `CYPHER_LOGGER` de l'import ; supprimer `test_cypher_is_quiet_until_it_is_asked_for` et `test_cypher_opens_the_driver_too` ; dans `test_a_global_debug_level_does_not_drag_them_open`, supprimer `assert levels[CYPHER_LOGGER] == "WARNING"` et « three firehoses » → « the firehoses » ; retirer `neo4j_password="x", ` de l'appel ligne ~70 ; ligne ~167 `"EA_NEO4J_PASSWORD=hunter2"` → `"EA_POSTGRES_PASSWORD=hunter2"`.

- [ ] **Step 6: Vérifier qu'il ne reste rien**

Run: `cd backend && grep -rniE "neo4j|cypher|bolt" src tests migrations pyproject.toml | grep -v "docs/adr"`
Expected: seulement des mentions historiques légitimes : `tests/e2e/test_unexpected_errors.py` (chaîne factice `bolt://…` d'un secret à masquer — la remplacer par `postgresql://ea:hunter2@db/ea`), `tests/unit/test_openapi.py:19` (docstring : « No database password, no pool, no network »), `tests/integration/test_postgres.py:92` (docstring : « The graph is injected rather than built: what is under test is the relational half of the lifespan alone. »), `domain/archimate/relations.py:62` (docstring de `label` : « The upper-case form, e.g. `SERVING`. »), `domain/archimate/taxonomy.py:3` (« Nothing here imports FastAPI or SQLAlchemy »), `domain/ipam.py` lignes 14, 44, 56, `domain/search.py:73`, `domain/diagrams.py:4`, `api/schemas.py:385`, `mcp/server.py:10`, `services/documents.py:5`, `services/diagrams.py:4`, `services/ipam.py:20`, `repositories/embeddings.py:61`, `db/postgres.py:5`, `db/models/chunk.py:5`, `migrations/versions/0002…0004` (historique, **ne pas modifier** les révisions déjà appliquées). Pour chaque fichier de `src/` listé : réécrire la phrase pour qu'elle dise PostgreSQL / « the architecture store » au lieu de Neo4j, sans changer le code. Relancer la commande jusqu'à ce qu'elle ne liste plus que `migrations/versions/0002`, `0003`, `0004`.

- [ ] **Step 7: Vérifier la barrière**

Run (racine) : `make check`
Expected: vert (lint, types, openapi-check inchangé, tests unitaires + e2e ≥ 90 %, pipelines).
Run : `cd backend && ITEST tests/integration`
Expected: PASS, aucun test ne saute pour « no Neo4j ».

- [ ] **Step 8: Commit**

```bash
git add -A backend
git commit -m "refactor!: le graphe sur PostgreSQL seul, Neo4j retiré du backend

BREAKING CHANGE: EA_NEO4J_* et EA_LOG_CYPHER ne sont plus lus ; le graphe
existant doit être importé (make graph-import) avant de déployer.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: L'import ponctuel du graphe Neo4j

**Files:**
- Create: `backend/src/ea/graph_import.py`
- Create: `backend/scripts/import_neo4j.py`
- Create: `backend/tests/unit/test_graph_import.py`
- Create: `backend/tests/integration/test_graph_import.py`
- Modify: `Makefile` (cible `graph-import`)

**Interfaces:**
- Consumes: `ElementRecord`, `RelationshipRecord` (tâche 1), `element_row`, `relationship_row`, `PostgresArchitectureRepository` (tâche 2).
- Produces:
  - `element_from_neo4j(stored: Mapping[str, Any]) -> Element`
  - `relationship_from_neo4j(stored: Mapping[str, Any]) -> Relationship`
  - `async copy_graph(sessions: async_sessionmaker[AsyncSession], elements: Sequence[Element], relationships: Sequence[Relationship]) -> None`
  - `async verify_copy(sessions: async_sessionmaker[AsyncSession], elements: Sequence[Element], relationships: Sequence[Relationship]) -> None`
  - `class GraphImportError(RuntimeError)`

- [ ] **Step 1: Écrire les tests unitaires qui échouent**

Créer `backend/tests/unit/test_graph_import.py` :

```python
"""Reading a Neo4j record into the domain, without Neo4j — docs/adr/0033."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.graph_import import element_from_neo4j, relationship_from_neo4j

WHEN = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)


class Neo4jDateTime:
    """The driver's own temporal type: it only becomes a `datetime` when asked."""

    def __init__(self, value: datetime) -> None:
        self._value = value

    def to_native(self) -> datetime:
        return self._value


def test_an_element_loses_the_property_prefix_and_keeps_its_id() -> None:
    element_id = uuid4()

    element = element_from_neo4j(
        {
            "id": str(element_id),
            "element_type": "node",
            "layer": "technology",
            "aspect": "active_structure",
            "name": "db-01",
            "description": "Base",
            "documentation": "",
            "created_at": Neo4jDateTime(WHEN),
            "updated_at": Neo4jDateTime(WHEN),
            "p_ip_address": "10.0.1.12",
            "p_vrf": "default",
            "p_rack": 12,
        }
    )

    assert element.id == element_id
    assert element.element_type is ElementType.NODE
    assert element.created_at == WHEN
    assert dict(element.properties) == {"ip_address": "10.0.1.12", "vrf": "default", "rack": "12"}


def test_a_missing_text_field_is_empty_rather_than_absent() -> None:
    element = element_from_neo4j(
        {
            "id": str(uuid4()),
            "element_type": "capability",
            "name": "Facturer",
            "created_at": WHEN,
            "updated_at": WHEN,
        }
    )

    assert element.description == ""
    assert element.documentation == ""
    assert dict(element.properties) == {}


def test_a_relationship_keeps_its_ends_types_and_access() -> None:
    source, target = uuid4(), uuid4()

    relationship = relationship_from_neo4j(
        {
            "id": str(uuid4()),
            "relationship_type": "access",
            "source_id": str(source),
            "target_id": str(target),
            "source_type": "application_component",
            "target_type": "data_object",
            "created_at": Neo4jDateTime(WHEN),
            "name": "lit",
            "access_type": "read",
            "directed": True,
            "p_since": "2024",
        }
    )

    assert relationship.relationship_type is RelationshipType.ACCESS
    assert (relationship.source_id, relationship.target_id) == (source, target)
    assert relationship.source_type is ElementType.APPLICATION_COMPONENT
    assert relationship.access_type is AccessType("read")
    assert relationship.directed is True
    assert dict(relationship.properties) == {"since": "2024"}
```

Avant de lancer, vérifier que `"read"` est une valeur d'`AccessType` et `"active_structure"` / `"technology"` les valeurs de `ElementType.NODE.aspect` / `.layer` (`uv run python -c "from ea.domain.archimate import AccessType, ElementType as E; print([a.value for a in AccessType], E.NODE.layer.value, E.NODE.aspect.value)"`) et corriger les littéraux du test si besoin.

- [ ] **Step 2: Vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_graph_import.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'ea.graph_import'`.

- [ ] **Step 3: Écrire le module d'import**

Créer `backend/src/ea/graph_import.py` :

```python
"""Copy the graph Neo4j held into the two PostgreSQL tables — once.

Used by `scripts/import_neo4j.py` (`make graph-import`) during the cut-over of
docs/adr/0033, then deleted with it. It never imports the Neo4j driver: the
script reads the records and hands them over as plain mappings, so this module
is typed, tested and covered like the rest of `ea`.

The copy is one transaction and refuses a non-empty `elements`, so a failure
leaves nothing behind and a second run cannot duplicate the first. Ids are
kept, which is what keeps every document and every diagram attached.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, cast
from uuid import UUID

from sqlalchemy import func, select

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import element_row, relationship_row

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

#: How Neo4j kept user-defined attributes apart from the fields the model owns.
NEO4J_PROPERTY_PREFIX: Final = "p_"


class GraphImportError(RuntimeError):
    """The copy was refused, or does not match what was read."""


def _native(value: Any) -> datetime:
    """Neo4j hands back its own temporal type; the domain speaks `datetime`."""
    return cast("datetime", value.to_native() if hasattr(value, "to_native") else value)


def _user_properties(stored: Mapping[str, Any]) -> dict[str, str]:
    prefix = len(NEO4J_PROPERTY_PREFIX)
    return {
        key[prefix:]: str(value)
        for key, value in stored.items()
        if key.startswith(NEO4J_PROPERTY_PREFIX)
    }


def element_from_neo4j(stored: Mapping[str, Any]) -> Element:
    return Element(
        id=UUID(str(stored["id"])),
        element_type=ElementType(stored["element_type"]),
        name=stored["name"],
        created_at=_native(stored["created_at"]),
        updated_at=_native(stored["updated_at"]),
        description=stored.get("description") or "",
        documentation=stored.get("documentation") or "",
        properties=_user_properties(stored),
    )


def relationship_from_neo4j(stored: Mapping[str, Any]) -> Relationship:
    access = stored.get("access_type")
    return Relationship(
        id=UUID(str(stored["id"])),
        relationship_type=RelationshipType(stored["relationship_type"]),
        source_id=UUID(str(stored["source_id"])),
        target_id=UUID(str(stored["target_id"])),
        source_type=ElementType(stored["source_type"]),
        target_type=ElementType(stored["target_type"]),
        created_at=_native(stored["created_at"]),
        name=stored.get("name") or "",
        access_type=AccessType(access) if access else None,
        directed=bool(stored.get("directed", False)),
        properties=_user_properties(stored),
    )


async def copy_graph(
    sessions: async_sessionmaker[AsyncSession],
    elements: Sequence[Element],
    relationships: Sequence[Relationship],
) -> None:
    """Write every element, then every relationship, in one transaction."""
    async with sessions.begin() as session:
        already = (await session.execute(select(func.count()).select_from(ElementRecord))).scalar_one()
        if already:
            msg = f"elements already holds {already} row(s): the graph was imported before"
            raise GraphImportError(msg)
        session.add_all(ElementRecord(**element_row(element)) for element in elements)
        await session.flush()
        session.add_all(
            RelationshipRecord(**relationship_row(relationship)) for relationship in relationships
        )


async def verify_copy(
    sessions: async_sessionmaker[AsyncSession],
    elements: Sequence[Element],
    relationships: Sequence[Relationship],
) -> None:
    """Fail unless PostgreSQL holds exactly the ids that were read from Neo4j."""
    async with sessions() as session:
        stored_elements = set(await session.scalars(select(ElementRecord.id)))
        stored_links = set(await session.scalars(select(RelationshipRecord.id)))
    for kind, read, stored in (
        ("element", {e.id for e in elements}, stored_elements),
        ("relationship", {r.id for r in relationships}, stored_links),
    ):
        if read != stored:
            msg = (
                f"{kind}s differ: {len(read - stored)} read but not stored, "
                f"{len(stored - read)} stored but not read"
            )
            raise GraphImportError(msg)
```

- [ ] **Step 4: Vérifier les tests unitaires**

Run: `cd backend && uv run pytest tests/unit/test_graph_import.py -q && uv run mypy src`
Expected: PASS ; mypy propre.

- [ ] **Step 5: Écrire le test d'intégration de la copie**

Créer `backend/tests/integration/test_graph_import.py` :

```python
"""The one-off copy, against a real PostgreSQL — docs/adr/0033."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element, Relationship
from ea.graph_import import GraphImportError, copy_graph, verify_copy
from ea.repositories.architecture_store import PostgresArchitectureRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

WHEN = datetime(2026, 9, 1, 8, 30, tzinfo=UTC)


def a_small_graph() -> tuple[list[Element], list[Relationship]]:
    app = Element.create(element_type=E.APPLICATION_COMPONENT, name="Billing", now=WHEN)
    host = Element.create(
        element_type=E.NODE, name="srv-01", properties={"rack": "12"}, now=WHEN
    )
    return [app, host], [Relationship.between(R.ASSOCIATION, app, host, now=WHEN)]


async def test_the_copy_keeps_ids_and_passes_its_own_verification(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    elements, relationships = a_small_graph()

    await copy_graph(sessions, elements, relationships)
    await verify_copy(sessions, elements, relationships)

    repository = PostgresArchitectureRepository(sessions)
    assert await repository.get_element(elements[1].id) == elements[1]
    assert await repository.get_relationship(relationships[0].id) == relationships[0]


async def test_a_second_copy_is_refused(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    elements, relationships = a_small_graph()
    await copy_graph(sessions, elements, relationships)

    with pytest.raises(GraphImportError, match="imported before"):
        await copy_graph(sessions, elements, relationships)


async def test_verification_notices_what_was_not_stored(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    elements, relationships = a_small_graph()
    await copy_graph(sessions, elements[:1], [])

    with pytest.raises(GraphImportError, match="1 read but not stored"):
        await verify_copy(sessions, elements, relationships)
```

- [ ] **Step 6: Vérifier**

Run: `cd backend && ITEST tests/integration/test_graph_import.py`
Expected: PASS (3 tests).

- [ ] **Step 7: Écrire le script et la cible**

Créer `backend/scripts/import_neo4j.py` :

```python
"""One-off: copy the Neo4j graph into PostgreSQL — docs/adr/0033.

    make graph-import CONFIRM=yes     # from the repository root

Run from `backend/`, with the API stopped (`make app-down`) and a fresh
`make pg-backup`. It writes to the database `backend/.env` names — the shared
one. Steps: `alembic upgrade 0005`, read every element and relationship from
Neo4j, copy them in one transaction, verify the ids on both sides, then
`alembic upgrade head` (revision 0006 and its foreign keys).

The Neo4j driver is not a dependency of the project: `uv run --with neo4j`
loads it for this run only. Deleted, with `ea.graph_import`, once the cut-over
is confirmed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from neo4j import AsyncGraphDatabase
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from ea.core.config import get_settings
from ea.db.postgres import check_connectivity, create_engine, create_session_factory
from ea.graph_import import copy_graph, element_from_neo4j, relationship_from_neo4j, verify_copy

BACKEND = Path(__file__).resolve().parents[1]


class Neo4jSource(BaseSettings):
    """Where the old graph is. Read here only — `Settings` no longer knows Neo4j."""

    model_config = SettingsConfigDict(env_prefix="EA_NEO4J_", env_file=".env", extra="ignore")

    uri: str = "bolt://127.0.0.1:7687"
    user: str = "neo4j"
    password: SecretStr
    database: str = "neo4j"


async def read_neo4j(source: Neo4jSource) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    driver = AsyncGraphDatabase.driver(
        source.uri, auth=(source.user, source.password.get_secret_value())
    )
    try:
        nodes = await driver.execute_query(
            "MATCH (e:Element) RETURN properties(e) AS e", database_=source.database
        )
        edges = await driver.execute_query(
            "MATCH (:Element)-[r]->(:Element) RETURN properties(r) AS r",
            database_=source.database,
        )
    finally:
        await driver.close()
    return [dict(record["e"]) for record in nodes.records], [
        dict(record["r"]) for record in edges.records
    ]


async def copy(source: Neo4jSource) -> tuple[int, int]:
    elements_read, relationships_read = await read_neo4j(source)
    elements = [element_from_neo4j(stored) for stored in elements_read]
    relationships = [relationship_from_neo4j(stored) for stored in relationships_read]
    engine = create_engine(get_settings())
    try:
        await check_connectivity(engine)
        sessions = create_session_factory(engine)
        await copy_graph(sessions, elements, relationships)
        await verify_copy(sessions, elements, relationships)
    finally:
        await engine.dispose()
    return len(elements), len(relationships)


def main() -> int:
    source = Neo4jSource()  # type: ignore[call-arg]  # the password comes from the environment
    alembic = Config(BACKEND / "alembic.ini")
    print("alembic upgrade 0005 ...")
    command.upgrade(alembic, "0005")
    print(f"reading {source.uri} ...")
    elements, relationships = asyncio.run(copy(source))
    print(f"{elements} element(s) and {relationships} relationship(s) copied and verified.")
    print("alembic upgrade head ...")
    command.upgrade(alembic, "head")
    print("done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Dans `Makefile`, ajouter `graph-import` à la liste `.PHONY` (ligne `pg-backup pg-restore db-backup-howto`) et, après la cible `pg-restore` :

```make
# Ponctuel (docs/adr/0033) : copie le graphe Neo4j dans PostgreSQL, puis
# applique la révision 0006. Vise la base PARTAGÉE de backend/.env : API arrêtée
# (make app-down) et make pg-backup d'abord. Le pilote neo4j n'est chargé que
# pour cette commande. EA_NEO4J_PASSWORD est lu dans backend/.env ou
# l'environnement. Supprimée avec le script une fois la bascule confirmée.
graph-import: | $(VENV_STAMP) ## Importe une fois le graphe Neo4j dans PostgreSQL (CONFIRM=yes)
	@test "$(CONFIRM)" = "yes" || { \
		printf "$(RED)Écrit dans la base PARTAGÉE. D'abord : make app-down, make pg-backup.$(NC)\n"; \
		printf "Relance avec : make graph-import CONFIRM=yes\n"; exit 1; }
	cd $(BACKEND) && uv run --with 'neo4j>=5.26' python scripts/import_neo4j.py
```

- [ ] **Step 8: Vérifier le script sans le lancer contre la base partagée**

Run: `cd backend && uv run ruff format scripts src tests && uv run ruff check scripts src tests && uv run --with 'neo4j>=5.26' python -c "import importlib.util, sys; spec = importlib.util.spec_from_file_location('m', 'scripts/import_neo4j.py'); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print('ok')"`
Run (racine) : `make graph-import`
Expected: ruff propre ; `ok` ; `make graph-import` sans `CONFIRM` refuse avec le message rouge et code de sortie 1. **Ne pas lancer `CONFIRM=yes`** : c'est l'étape 3 du runbook, faite par un humain.

- [ ] **Step 9: Commit**

```bash
git add backend/src/ea/graph_import.py backend/scripts/import_neo4j.py backend/tests/unit/test_graph_import.py backend/tests/integration/test_graph_import.py Makefile
git commit -m "feat(import): copie ponctuelle du graphe Neo4j vers PostgreSQL

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Outillage — compose, CI, Makefile, déploiement

**Files:**
- Modify: `docker-compose.yml`, `.github/workflows/ci.yml`, `Makefile`, `deploy/ea.stack.yml`, `deploy/ea.env.example`, `backend/.env.example`
- Test: `backend/tests/unit/test_deploy_stack.py`

**Interfaces:**
- Consumes: le backend sans Neo4j (tâche 6), la cible `graph-import` (tâche 7).
- Produces: `make test-integration` et `make compose-up` sur PostgreSQL seul ; stack déployée sans `EA_NEO4J_*`.

- [ ] **Step 1: Écrire les tests de déploiement qui échouent**

Dans `backend/tests/unit/test_deploy_stack.py` : supprimer `test_the_local_compose_neo4j_is_a_test_instance_not_the_graph` et `test_the_test_graph_healthcheck_keeps_the_password_off_the_command_line` ; remplacer le premier point de la docstring de module par :

```
* `docker-compose.yml` must hold nothing but the throwaway PostgreSQL the
  integration tests migrate up and down — since docs/adr/0033 there is no
  graph database left anywhere in this repository.
```

et la première phrase « The shared graph is the `neo4j` service of the Infra stack… » par « The shared database is the `postgres` service of the Infra stack (docs/adr/0029). » ; dans `test_every_local_container_listens_on_loopback_only`, docstring « It carries a disposable password in clear; the LAN must not reach it. ». Ajouter :

```python
def test_no_graph_database_is_left_in_the_deployment_files() -> None:
    """The graph lives in PostgreSQL since docs/adr/0033; Neo4j must not creep back."""
    for path in (COMPOSE, STACK, REPO_ROOT / ".github" / "workflows" / "ci.yml"):
        declarations = "\n".join(
            line
            for line in path.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "neo4j" not in declarations.lower(), path
```

- [ ] **Step 2: Vérifier l'échec**

Run: `cd backend && uv run pytest tests/unit/test_deploy_stack.py -q`
Expected: FAIL sur `test_no_graph_database_is_left_in_the_deployment_files` (`docker-compose.yml`).

- [ ] **Step 3: Retirer Neo4j des fichiers d'outillage**

`docker-compose.yml` : supprimer tout le service `neo4j:` ; remplacer l'en-tête (lignes 1-~25) par :

```yaml
# Base jetable, pour les tests — et pour rien d'autre.
#
# La base du projet ne tourne pas ici : le PostgreSQL applicatif, qui porte le
# graphe depuis docs/adr/0033, vit dans la stack Infra, sur 127.0.0.1:5432
# (docs/adr/0029). Personne ne modélise contre ce fichier.
#
# Ce conteneur existe pour une seule raison : les tests d'intégration
# appliquent puis annulent la chaîne de migrations (`alembic downgrade base`) —
# ce qu'on ne fait pas sur une base que d'autres utilisent. Voir docs/adr/0024.
#
# `make test-integration` le démarre et pointe dessus. `make pg-ping`,
# `pg-shell` et `pg-migrate` visent l'instance partagée, pas ce conteneur.
#
# Il ne publie que sur 127.0.0.1, et sur un autre port que 5432 : les fixtures
# refusent toute adresse qui n'est pas locale, et le port de la base partagée.
```

(conserver tel quel tout commentaire restant sous cet en-tête qui ne parle que de PostgreSQL ; supprimer ceux qui parlent du graphe).

`.github/workflows/ci.yml`, job `integration` : `name: Intégration — PostgreSQL jetable` ; supprimer le service `neo4j:` et son commentaire ; commentaire des services : « La même image que la stack Infra : un test qui passe ici doit passer là-bas, contraintes et pgvector compris. Le mot de passe est jetable, comme la machine. » ; supprimer les variables `EA_ALLOW_DESTRUCTIVE_TESTS`, `EA_NEO4J_URI`, `EA_NEO4J_PASSWORD`.

`Makefile` :
- supprimer le bloc de variables `NEO4J_HOST` … `NEO4J_CONTAINER` et son commentaire (lignes ~48-59) ;
- dans le commentaire des mots de passe : « les deux instances sont partagées » → « l'instance est partagée » ; « voir NEO4J_ENV et POSTGRES_ENV, préfixes de recette » → « voir POSTGRES_ENV, préfixe de recette » ; supprimer les deux lignes « `make db-ping NEO4J_PASSWORD=...` fonctionne toujours … » ;
- supprimer la ligne `NEO4J_ENV = …` ;
- commentaire `POSTGRES_HOST` : « Base relationnelle : tout ce qui n'est pas le graphe (auth, audit, planification). Comme le graphe, une seule instance » → « La base du projet : le graphe (docs/adr/0033), les documents, les diagrammes. Une seule instance » ;
- supprimer les variables `NEO4J_TEST_*` et leur commentaire ; `COMPOSE_TEST` sans les deux lignes `NEO4J_TEST_*` ;
- `.PHONY` : retirer `db-stack db-ping db-shell db-reset require-neo4j-password`, `db-backup-howto`, `db-test-up` ;
- règle `$(BE_ENV)` : supprimer le paragraphe « L'exemple ne porte aucun mot de passe : le graphe … » et remplacer la ligne rouge par `@printf "$(RED)Renseigne EA_POSTGRES_PASSWORD dans $@ — voir make pg-stack.$(NC)\n"` ;
- supprimer toute la section `## --- Base de données graphe` (de `require-neo4j-password:` à la fin de `db-backup-howto`) ;
- supprimer la cible `db-test-up` et son commentaire ;
- `pg-down` : `## Arrête le PostgreSQL jetable local (garde son volume)` ; `compose-up` : `$(COMPOSE_TEST) up -d --wait` avec `## Démarre le PostgreSQL jetable local et attend qu'il soit sain` ; `compose-logs` : `## Suit les logs du PostgreSQL jetable` ;
- `test-integration` :

```make
# Contre le conteneur jetable, jamais contre la base partagée : ces tests
# annulent la chaîne de migrations, graphe compris. Les fixtures refusent de
# toute façon un hôte qui n'est pas local et le port 5432 (docs/adr/0024).
# Les embeddings sont coupés : le démarrage de l'application irait sinon
# interroger LM Studio sur le cluster, et la suite n'en dépend pas.
test-integration: | $(VENV_STAMP) ## Tests d'intégration contre le PostgreSQL jetable local (le démarre au besoin)
	$(COMPOSE_TEST) up -d --wait postgres
	cd $(BACKEND) && EA_DEBUG=true EA_POSTGRES_ENABLED=true \
		EA_POSTGRES_HOST=127.0.0.1 EA_POSTGRES_PORT=$(POSTGRES_TEST_PORT) \
		EA_POSTGRES_PASSWORD='$(POSTGRES_TEST_PASSWORD)' \
		EA_EMBEDDINGS_ENABLED=false \
		uv run pytest tests/integration -q
```

- dans le texte de `pg-backup` / `pg-restore` et la section embeddings, remplacer « à côté du graphe » par « sur le cluster » et toute mention de `db-backup-howto` par rien (vérifier avec `grep -n "graphe\|db-backup-howto\|neo4j" Makefile`, en ne gardant que la cible `graph-import` et son commentaire).

`deploy/ea.stack.yml` : supprimer le commentaire « Le graphe n'est pas dans l'Infra … » et les lignes `EA_NEO4J_URI` / `EA_NEO4J_PASSWORD` ; commentaire PostgreSQL : ajouter « Il porte tout le modèle, graphe compris (docs/adr/0033). ».

`deploy/ea.env.example` : supprimer le commentaire du graphe et les lignes `EA_NEO4J_URI=`, `EA_NEO4J_PASSWORD=`.

`backend/.env.example` : supprimer `EA_LOG_CYPHER=false` et tout le bloc `# --- Neo4j …` (commentaire et quatre variables) ; titre du bloc suivant : `# --- PostgreSQL, the whole model (docs/adr/0015, 0033) ---------------------`. Ajouter à la fin :

```
# --- Only for `make graph-import`, once (docs/adr/0033) -----------------------
# The old Neo4j graph the import reads. Delete these lines after the cut-over.
# EA_NEO4J_URI=bolt://127.0.0.1:7687
# EA_NEO4J_PASSWORD=
```

- [ ] **Step 4: Vérifier**

Run: `cd backend && uv run pytest tests/unit/test_deploy_stack.py -q`
Run (racine) : `grep -rniE "neo4j|cypher|7688|DESTRUCTIVE" Makefile docker-compose.yml .github deploy backend/.env.example`
Expected: tests PASS ; le grep ne liste que la cible `graph-import` (Makefile) et le bloc « Only for `make graph-import` » de `.env.example`.
Run (racine) : `make compose-down && make test-integration && make check`
Expected: vert.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .github/workflows/ci.yml Makefile deploy backend/.env.example backend/tests/unit/test_deploy_stack.py
git commit -m "chore: retirer Neo4j de compose, de la CI, du Makefile et du déploiement

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Documentation — ADR 0033, CLAUDE.md, README

**Files:**
- Create: `docs/adr/0033-postgresql-seul-pour-le-graphe.md`
- Modify: `docs/adr/0004-neo4j-pour-le-graphe-d-architecture.md:4`, `docs/adr/0030-neo4j-dans-la-stack-infra.md:4`
- Modify: `CLAUDE.md`, `README.md`
- Modify: `docs/superpowers/specs/2026-09-14-postgresql-seul-design.md` (écarts du plan)

**Interfaces:**
- Consumes: tout ce qui précède.
- Produces: la décision écrite, et `CLAUDE.md` décrivant le dépôt tel qu'il est.

- [ ] **Step 1: Écrire l'ADR 0033**

Créer `docs/adr/0033-postgresql-seul-pour-le-graphe.md` :

```markdown
---
titre: PostgreSQL seul — le graphe d'architecture en tables relationnelles
date: 2026-09-14
statut: Proposition
affects: backend/src/ea/repositories/architecture_store.py, backend/src/ea/db/models/architecture.py, backend/migrations/versions/0005_architecture_graph.py, backend/migrations/versions/0006_attachments_follow_elements.py, backend/src/ea/services/architecture.py, backend/src/ea/domain/ports.py, Makefile, docker-compose.yml, deploy/, .github/workflows/ci.yml
---

# 33. PostgreSQL seul : le graphe d'architecture en tables relationnelles

## Contexte

L'ADR [0004](0004-neo4j-pour-le-graphe-d-architecture.md) a mis le graphe dans
Neo4j et laissé à PostgreSQL « ce qui n'est pas un graphe ». Depuis, PostgreSQL
porte les documents ([0017](0017-documents-markdown-attaches-aux-elements.md)),
leur index vectoriel ([0019](0019-recherche-semantique-sur-les-documents.md)) et
les diagrammes ([0031](0031-diagrammes-enregistres.md)) : chacun désigne un
élément qu'aucune clé étrangère ne pouvait atteindre. Le prix annoncé par 0004
est arrivé en entier : deux bases à exploiter, deux sauvegardes — dont une hors
ligne seulement ([0025](0025-sauvegardes-des-deux-bases.md)) —, deux conteneurs
jetables ([0024](0024-tests-d-integration-sur-des-bases-jetables.md)), un port
`ElementAttachments` et des lignes orphelines acceptées faute de transaction
commune.

0004 avait écarté le graphe relationnel sur un seul motif : la lisibilité de
l'analyse d'impact en SQL récursif.

## Décision

**Le graphe d'architecture est stocké dans PostgreSQL. Neo4j quitte le projet.**

- `elements` et `relationships` (révision `0005`) ; les attributs utilisateur
  en `jsonb`, sans préfixe ; `UNIQUE (element_type, name)`.
- Les deux règles de l'IPAM ([0020](0020-adressage-ip-en-proprietes-du-graphe.md))
  deviennent les index uniques partiels `uq_elements_vrf_ip_address` et
  `uq_elements_vrf_cidr` sur `properties ->> 'vrf'` et la clé concernée, avec
  `WHERE properties ? 'vrf' AND properties ? '<clé>'` : un élément sans l'une
  des deux clés n'est pas concerné, comme sous Neo4j. Un refus est traduit
  d'après le **nom** de la contrainte, plus d'après le texte du message.
- `relationships.source_id` / `target_id`, `element_documents.element_id`
  et `diagram_nodes.element_id` sont des clés étrangères `ON DELETE CASCADE`
  (révision `0006`). Supprimer un élément est une transaction ; le port
  `ElementAttachments` disparaît.
- Voisinage, analyse d'impact et détection de cycle sont trois CTE
  `WITH RECURSIVE` en SQLAlchemy Core dans `repositories/architecture_store.py`.
  La profondeur est un paramètre lié : la dérogation « trois sites Cypher
  composés » de `CLAUDE.md` disparaît.
- `EA_LOG_CYPHER` disparaît ; `EA_LOG_SQL` couvre toutes les requêtes.

Le coût de lisibilité reconnu par 0004 est accepté : trois requêtes, un fichier,
couvertes par les tests d'intégration du graphe et de l'IPAM écrits contre
Neo4j et conservés tels quels comme contrat.

## Alternatives envisagées et écartées

| Option | Pour | Contre | Verdict |
|---|---|---|---|
| Garder Neo4j | Cypher lisible | deux bases, pas de transaction commune, orphelins | écarté |
| Apache AGE | Cypher conservé dans PostgreSQL | image à construire portant pgvector *et* AGE ; intégration SQLAlchemy artisanale | écarté |
| Adjacence en `jsonb` | une seule table | ni clé étrangère ni contrainte d'unicité | écarté |
| Tables + CTE récursives | une base, une chaîne Alembic, FK, contraintes nommées | SQL récursif moins lisible | **retenu** |

## Conséquences

- **Bascule.** L'image applique `alembic upgrade head` au démarrage. `0006`
  refuse de tourner si `elements` est vide alors que des documents ou des
  boîtes existent, pour qu'un déploiement arrivé avant l'import ne les efface
  pas comme orphelins. Procédure : `make pg-backup` et dump Neo4j hors ligne ;
  `make app-down` ; `make graph-import CONFIRM=yes` (`0005`, copie en une
  transaction, vérification des ids, `0006`) ; fusion sur `main` et
  `make app-up` ; vérification dans le SPA. Retour arrière : `make pg-restore`,
  le graphe Neo4j n'ayant pas été touché.
- Dump Neo4j hors ligne, à faire une dernière fois avant la bascule, sur le Mac
  de la stack Infra :
  `docker stop infra-neo4j-1 && docker run --rm --volumes-from infra-neo4j-1 -v "$dir":/backups <image> neo4j-admin database dump neo4j --to-path=/backups && docker start infra-neo4j-1`.
- Le service `neo4j` de la stack Infra reste en place comme filet jusqu'à son
  retrait dans ce dépôt-là. `scripts/import_neo4j.py`, `ea.graph_import` et la
  cible `graph-import` sont supprimés une fois la bascule confirmée ; cet ADR
  passe alors à *Accepté*.
- Une seule base à sauvegarder : `make pg-backup` couvre tout le modèle
  (amende 0025). Un seul conteneur jetable et plus
  d'`EA_ALLOW_DESTRUCTIVE_TESTS` (amende 0024). La recherche du catalogue est
  un parcours complet de `elements` ; un index trigramme sera à ajouter
  au-delà de ~100 000 éléments.
- Aucun contrat visible ne change : schéma OpenAPI, outils MCP, SPA et
  `pipelines/` sont identiques.

## Références

- Remplace : [0004](0004-neo4j-pour-le-graphe-d-architecture.md), [0030](0030-neo4j-dans-la-stack-infra.md)
- Amende : [0017](0017-documents-markdown-attaches-aux-elements.md), [0020](0020-adressage-ip-en-proprietes-du-graphe.md), [0021](0021-des-logs-que-quelqu-un-peut-lire.md), [0024](0024-tests-d-integration-sur-des-bases-jetables.md), [0025](0025-sauvegardes-des-deux-bases.md), [0031](0031-diagrammes-enregistres.md)
- Spec : `docs/superpowers/specs/2026-09-14-postgresql-seul-design.md`
```

Modifier `docs/adr/0004-neo4j-pour-le-graphe-d-architecture.md` ligne 4 : `Statut : Supersédé par [\`0033\`](0033-postgresql-seul-pour-le-graphe.md) — le graphe est dans PostgreSQL`.
Modifier `docs/adr/0030-neo4j-dans-la-stack-infra.md` ligne 4 : `statut: "Supersédé par : 0033"`.

- [ ] **Step 2: Mettre `CLAUDE.md` en accord avec le code**

Dans `CLAUDE.md` (racine), appliquer exactement ceci :

1. Paragraphe *Project status*, première phrase : « `backend/` serves a FastAPI app with the full ArchiMate 3.2 metamodel, an element/relationship catalogue and two graph traversals, stored in Neo4j. » → « … and two graph traversals, stored in PostgreSQL (see `docs/adr/0033`). »
2. Paragraphe « The relational half holds the documents in **two tables**. » → « PostgreSQL holds the whole model: the graph in `elements` and `relationships` (migration `0005`, see `docs/adr/0033`), and the documents in **two tables**. » ; phrase « a database that has lived in the `~/OpenCode/Infra` stack … `EA_POSTGRES_ENABLED` is **on** since the first table exists, so a deployment that cannot reach PostgreSQL no longer boots. » : conserver.
3. Paragraphe *diagrams* : « `diagram_nodes.element_id` has no foreign key, like `element_documents.element_id`, and deleting an element discards its boxes through the same `ElementAttachments` cascade as its documents — `AllAttachments` fans it out, it is not a second mechanism (see `docs/adr/0031`). » → « `diagram_nodes.element_id` is a foreign key to `elements`, like `element_documents.element_id`, so deleting an element takes its boxes and its documents in the same transaction (migration `0006`, see `docs/adr/0033`). » ; « the relationships whose two ends are on it (one Cypher query, `view_of`) » → « (one query, `view_of`) ».
4. Tableau *Locked stack decisions* : supprimer la ligne `Architecture graph | Neo4j …` ; remplacer la ligne `Everything not a graph | PostgreSQL …` par `| Storage | PostgreSQL + SQLAlchemy 2 (async, \`asyncpg\`) + Alembic | One database for the whole model: the graph as two tables walked by recursive CTEs, the documents and their index, the diagrams — see \`docs/adr/0033\` |` ; ligne *IP addressing* : « no new node kind » → « no new table ».
5. *Repository layout* : `docker-compose.yml  # throwaway PostgreSQL for the integration tests, on 127.0.0.1 only` ; sous `domain/` rien ; sous `repositories/` : « implementations of the ports declared in domain: SQL, and the outbound HTTP clients … » ; remplacer `db/  # Neo4j driver lifecycle and schema (constraints + indexes); the PostgreSQL engine, session factory and declarative base` par `db/  # the PostgreSQL engine, session factory and declarative base` ; `migrations/  # Alembic revisions for PostgreSQL — the graph included since 0005` ; ajouter sous `src/ea/` : `graph_import.py  # the one-off copy from Neo4j (docs/adr/0033), deleted after the cut-over` et sous `backend/` : `scripts/import_neo4j.py  # \`make graph-import\`, same lifetime`.
6. *Commands* : retirer `make db-test-up` ; `make test-integration  # the throwaway PostgreSQL, started if needed — never the shared one` ; `make pg-down  # stop the throwaway container (alias: compose-down)` ; `make compose-up  # the throwaway container, waiting until healthy` ; `make compose-ps  # compose-logs, compose-reset drops the PostgreSQL volume` ; ajouter `make graph-import CONFIRM=yes  # once: copy the Neo4j graph into PostgreSQL, then migration 0006`.
7. Paragraphe « Whole stack … » : supprimer de « The graph is a single instance: the `neo4j` service of the Infra stack … » jusqu'à « … because make expands `$` in a variable. » inclus ; « PostgreSQL is a second shared instance in that same stack » → « PostgreSQL is the one shared instance, in the Infra stack » ; « Nothing here starts any of the three. » → « Nothing here starts either. » ; « The embedding service is what is left on the Docker cluster » : conserver.
8. Paragraphe *Backups* : remplacer « Neo4j Community has no online backup, so `make db-backup-howto` only *prints* the offline `neo4j-admin database dump` procedure to run on the host — its name says it backs nothing up. » par « Since `docs/adr/0033` that dump is the whole model. »
9. Paragraphe *Deployed* : « the graph stays wherever `EA_NEO4J_URI` says » → supprimer la proposition.
10. *The MCP adapter…* : inchangé.
11. Section **The relational store holds the documents and their index** → titre **The relational store holds the whole model** ; premier paragraphe « PostgreSQL is a second shared instance » → « PostgreSQL is the one shared instance » ; phrase « Three rules hold for every table, starting with `element_documents`: » inchangée ; paragraphe « A third rule arrived with the second table: … which is possible here and nowhere else in this repository, since the graph is in another server. » → « … which is possible for every use case now that the graph is here too. »
12. Section **A document is markdown, and markdown is text** : remplacer tout le paragraphe **There is no foreign key, and there cannot be one:** … `element_id` a clean-up joins on. par : « **It follows its element by a foreign key.** `element_documents.element_id` references `elements` `ON DELETE CASCADE` since migration `0006`; `DocumentService` still reads the element before attaching, for a readable 404 rather than a constraint violation (see `docs/adr/0033`). »
13. Section **A document is found by its headings** : « **This is the first table that can carry a foreign key**, because a passage names a document — a row next door — where `element_documents.element_id` names a Neo4j node and can reference nothing. So its cascade is DDL rather than a service, » → « **This was the first table to carry a foreign key**, because a passage names a document — a row next door. Its cascade is DDL, » .
14. Section **An IP address is an attribute of the machine** : « There is no `:IpAddress` node and no `:Subnet` node » → « There is no IP address table and no subnet table » ; « A subnet is a `communication_network` element carrying `p_cidr`, and an address is `p_ip_address` on the element » → « carrying `cidr` in its properties, and an address is `ip_address` in the properties of the element » ; « A list in a property would have been easier to write and impossible to constrain: a composite Neo4j constraint only sees a scalar. With one address, `REQUIRE (e.p_vrf, e.p_ip_address) IS UNIQUE` is declarable » → « A list would have been easier to write and impossible to constrain: a unique index sees one value per row. With one address, the partial unique index `uq_elements_vrf_ip_address` on `(properties ->> 'vrf', properties ->> 'ip_address')` is declarable » ; « `REQUIRE (e.p_vrf, e.p_cidr) IS UNIQUE` declares it once per VRF » → « `uq_elements_vrf_cidr` declares it once per VRF » ; « `p_vrf` is written *beside* every address and prefix, because a composite constraint does not apply to a node missing one of its properties » → « `vrf` is written *beside* every address and prefix, because the index's `WHERE` leaves out an element missing either key » ; supprimer tout le paragraphe **The prefix constraint can stop an existing deployment from booting.** (le refus est désormais une migration versionnée).
15. Section **Drawing a graph in the SPA** : inchangée.
16. Supprimer entièrement la section **The graph has no Alembic** et la remplacer par :

```markdown
## The graph is two tables

`elements` and `relationships` (migration `0005`, `db/models/architecture.py`)
are read and written by `repositories/architecture_store.py` alone. User-defined
attributes are one `jsonb` map, `properties`, with no prefix. The neighbourhood,
the impact analysis and the containment-cycle check are recursive CTEs built
with SQLAlchemy Core — the depth is a bound parameter, clamped to
`MAX_TRAVERSAL_DEPTH` — and the impact walk takes each hop the way
`impact_follows_direction` says, read from the metamodel. A uniqueness refusal
is translated by the **name** of the constraint (`constraint_of`), never by its
message. Renaming a stored value — an element type, say — is a data migration:
an Alembic revision like any other. See `docs/adr/0033`.
```

17. *Security rules* : remplacer la puce « All DB access goes through SQLAlchemy constructs; raw `text()` requires bound parameters and a comment justifying it. **Cypher follows the same rule**: … Adding a fourth needs the same justification. » par « All DB access goes through SQLAlchemy constructs; raw `text()` requires bound parameters and a comment justifying it. » ; puce `Settings` : « `Settings` refuses to build without a Neo4j password unless `EA_DEBUG` is on. » → « `Settings` refuses to build without a PostgreSQL password while `postgres_enabled` is on, unless `EA_DEBUG` is on. »
18. *Nothing is logged…* : « `EA_LOG_CYPHER` (`ea.cypher` plus the driver), `EA_LOG_SQL` (`sqlalchemy.engine`) and `EA_LOG_EMBEDDINGS` (`ea.embeddings` plus `httpx`) are three firehoses opened one at a time » → « `EA_LOG_SQL` (`sqlalchemy.engine`) and `EA_LOG_EMBEDDINGS` (`ea.embeddings` plus `httpx`) are two firehoses opened one at a time ».
19. *TDD*, point 2 : remplacer tout le texte après « **Integration** (`tests/integration`): » par « repositories and services against a real PostgreSQL — the **throwaway local container** of `docker-compose.yml`, never the shared database (see `docs/adr/0024`). No mocked driver, no faked records — these exist to prove the SQL. Each test migrates the chain up to `head` (`engine_at_head`) and back to `base`. So the fixtures refuse any host that is not loopback — and, even on loopback, PostgreSQL's 5432, where the shared database listens (`docs/adr/0029`); the throwaway container publishes 5433 (`tests/integration/throwaway.py`) — and *skip* with a message naming it. `make test-integration` starts the container and points at it; a bare `uv run pytest` skips these tests. »
20. *Who is calling* : inchangé. *Definition of done* : « any new graph constraint added to `SCHEMA_STATEMENTS` and applied cleanly to a database that already had data » → « any new constraint shipped as an Alembic revision and applied cleanly to a database that already had data ».
21. Enfin : `grep -niE "neo4j|cypher|bolt|7687|7688|SCHEMA_STATEMENTS|ElementAttachments|AllAttachments|db-ping|db-shell|db-reset|db-stack|DESTRUCTIVE" CLAUDE.md` ne doit plus lister que les mentions de `docs/adr/0033`, `graph_import.py`, `scripts/import_neo4j.py` et `make graph-import`. Corriger toute autre ligne dans le même esprit.

- [ ] **Step 3: Mettre le README à jour**

Dans `README.md` :
- ligne 4 : « modèle **ArchiMate 3.2** dans une base de données **graphe (Neo4j)** » → « modèle **ArchiMate 3.2** dans **PostgreSQL** » ;
- lignes 7-10 : remplacer la phrase « … raison du choix de Neo4j — voir `docs/adr/0004` » par « Le graphe est stocké en tables et parcouru par des requêtes récursives — voir [`docs/adr/0033`](docs/adr/0033-postgresql-seul-pour-le-graphe.md). » ;
- lignes 21, 28-37 : supprimer les phrases sur `cypher-shell`, `EA_NEO4J_PASSWORD`, `make db-ping`, le service `neo4j` et `make db-stack` ; les remplacer par « Renseigne `EA_POSTGRES_PASSWORD` dans `backend/.env` ; `make pg-ping` vérifie que la base partagée répond. » ;
- ligne 53 : supprimer la ligne du tableau `bolt://127.0.0.1:7687` ;
- lignes 66-74 : supprimer les lignes `db-ping`, `db-stack`, `db-shell`, `db-reset`, `db-backup-howto` ;
- ligne 88 : `make test-integration     # contre un PostgreSQL jetable local, démarré au besoin` ;
- ligne 92 : « ils vident le graphe » → « ils annulent la chaîne de migrations ».
Vérifier : `grep -niE "neo4j|cypher|bolt|db-ping|db-shell|db-reset|db-stack" README.md` ne renvoie que le lien vers 0033 s'il contient le mot.

- [ ] **Step 4: Aligner la spec sur le plan**

Dans `docs/superpowers/specs/2026-09-14-postgresql-seul-design.md`, section 4 *Tests* :
- remplacer « `TRUNCATE elements CASCADE` entre tests » par « `alembic upgrade head` puis `downgrade base` à chaque test (`engine_at_head`), comme les autres stores » ;
- ajouter la puce : « Stores relationnels : depuis `0006`, un document ou une boîte de diagramme exige un élément existant ; `test_document_store.py`, `test_document_index.py` et `test_diagram_store.py` créent l'élément d'abord (`ensure_elements`). Les tests unitaires de cascade par le service (`test_document_service`, `test_diagram_service`, `test_indexing`, `test_architecture_delete.py`) sont remplacés par `tests/integration/test_element_cascade.py`. » ;
- section 3, script : « écrit par `PostgresArchitectureRepository.add_*` » → « écrit par `ea.graph_import.copy_graph` (lignes construites par `element_row` / `relationship_row`) ».

- [ ] **Step 5: Barrière finale**

Run (racine) : `make check && make test-integration && make openapi-check && git diff --stat main -- backend/openapi.json frontend/src/api/schema.d.ts`
Expected: tout vert ; le `git diff --stat` n'affiche **aucune** ligne (contrat inchangé).

- [ ] **Step 6: Commit**

```bash
git add docs/adr/0033-postgresql-seul-pour-le-graphe.md docs/adr/0004-neo4j-pour-le-graphe-d-architecture.md docs/adr/0030-neo4j-dans-la-stack-infra.md CLAUDE.md README.md docs/superpowers/specs/2026-09-14-postgresql-seul-design.md
git commit -m "docs: ADR 0033, PostgreSQL seul pour le graphe ; CLAUDE.md et README à jour

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Après le plan (hors exécution automatique)

1. **PR 1** : pousser la branche et ouvrir la PR (description : quoi, pourquoi, runbook de bascule, lien vers l'ADR 0033).
2. **Bascule** (humain, dans l'ordre de l'ADR 0033) : `make pg-backup` + dump Neo4j hors ligne → `make app-down` → `make graph-import CONFIRM=yes` depuis la branche de la PR → fusion sur `main` → `make app-up` → vérifier catalogue, voisinage, impact, IPAM, un document, un diagramme.
3. **PR 2** : supprimer `backend/scripts/import_neo4j.py`, `backend/src/ea/graph_import.py`, `backend/tests/unit/test_graph_import.py`, `backend/tests/integration/test_graph_import.py`, la cible `graph-import`, le bloc « Only for `make graph-import` » de `.env.example` et leurs mentions dans `CLAUDE.md` ; ADR 0033 → `statut: Accepté`.
4. **Dépôt Infra** : retirer le service `neo4j`.
