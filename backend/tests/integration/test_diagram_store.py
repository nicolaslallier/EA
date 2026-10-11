"""The diagram repository against a real PostgreSQL.

What only the server can prove: migration `0004` applies, the unique name is a
constraint, a layout is replaced in one transaction, deleting a diagram takes
its nodes by the foreign key, and a box names an element by the foreign key.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.models.diagram import DiagramNodeRecord
from ea.db.postgres import create_session_factory
from ea.domain.diagram_objects import DiagramScene
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.errors import (
    DiagramLimitError,
    DiagramNotFoundError,
    DiagramRevisionConflictError,
    DuplicateDiagramError,
    PackageNotFoundError,
)
from ea.domain.packages import Package
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.package_store import PostgresPackageRepository
from tests.integration.conftest import ensure_elements

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

FIXED_NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
LATER = FIXED_NOW.replace(year=2027)


class DiagramsOnStoredElements(PostgresDiagramRepository):
    """Each element a layout names gets a row first — foreign key since 0006."""

    def __init__(self, engine: AsyncEngine) -> None:
        super().__init__(create_session_factory(engine))
        self._engine = engine

    async def lay_out(
        self, diagram_id: UUID, nodes: Sequence[DiagramNode], *, now: datetime
    ) -> bool:
        """Save `nodes` on the current revision; `False` if the diagram is gone."""
        await ensure_elements(self._engine, *(node.element_id for node in nodes))
        current = await self.get(diagram_id)
        stored = await super().replace_layout(
            diagram_id,
            DiagramScene(nodes=tuple(nodes)),
            revision=current.revision if current else 0,
            now=now,
            audit=(),
        )
        return stored is not None


@pytest.fixture
def diagrams(engine_at_head: AsyncEngine) -> DiagramsOnStoredElements:
    return DiagramsOnStoredElements(engine_at_head)


def a_diagram(name: str = "Vente") -> Diagram:
    return Diagram.create(name=name, description="é", now=FIXED_NOW)


async def test_a_diagram_survives_the_round_trip(diagrams: DiagramsOnStoredElements) -> None:
    stored = await diagrams.add(a_diagram(), audit=())

    read = await diagrams.get(stored.id)

    assert read == stored
    assert read is not None and read.created_at.tzinfo is not None


async def test_a_name_taken_twice_hits_the_constraint_and_leaves_the_store_usable(
    diagrams: DiagramsOnStoredElements,
) -> None:
    await diagrams.add(a_diagram("Vente"), audit=())

    with pytest.raises(DuplicateDiagramError):
        await diagrams.add(a_diagram("Vente"), audit=())
    await diagrams.add(a_diagram("Achat"), audit=())

    assert [d.name for d in await diagrams.list_all()] == ["Achat", "Vente"]


async def test_a_rename_onto_a_taken_name_hits_the_constraint(
    diagrams: DiagramsOnStoredElements,
) -> None:
    await diagrams.add(a_diagram("Vente"), audit=())
    other = await diagrams.add(a_diagram("Achat"), audit=())

    with pytest.raises(DuplicateDiagramError):
        await diagrams.save(other.revise(name="Vente", now=LATER), audit=())


async def test_saving_a_diagram_that_is_gone_says_so(diagrams: DiagramsOnStoredElements) -> None:
    with pytest.raises(DiagramNotFoundError):
        await diagrams.save(a_diagram(), audit=())


async def test_a_layout_is_replaced_whole_and_counted(diagrams: DiagramsOnStoredElements) -> None:
    diagram = await diagrams.add(a_diagram(), audit=())
    first, second = uuid4(), uuid4()
    await diagrams.lay_out(diagram.id, [DiagramNode(first, 0, 0)], now=FIXED_NOW)

    replaced = await diagrams.lay_out(
        diagram.id, [DiagramNode(second, 1.25, -3.5, width=240, height=90.5)], now=LATER
    )

    assert replaced is True
    assert await diagrams.nodes_of(diagram.id) == (
        DiagramNode(second, 1.25, -3.5, width=240, height=90.5),
    )
    read = await diagrams.get(diagram.id)
    assert read is not None
    assert (read.node_count, read.updated_at) == (1, LATER)


async def test_concurrent_layouts_of_one_diagram_queue_instead_of_colliding(
    diagrams: DiagramsOnStoredElements,
) -> None:
    """Autosave fires on every drag: two saves of the same boxes must not race.

    Without a lock, the second DELETE cannot see the rows the first INSERT
    committed, and its own INSERT then hits the primary key.
    """
    diagram = await diagrams.add(a_diagram(), audit=())
    moved = uuid4()

    outcomes = await asyncio.gather(
        *(
            diagrams.lay_out(diagram.id, [DiagramNode(moved, step, step)], now=LATER)
            for step in range(10)
        ),
        return_exceptions=True,
    )

    # Each save either lands or is refused as stale (the revision is read before the
    # lock): never a primary-key collision, and the last stored layout is whole.
    assert all(o is True or isinstance(o, DiagramRevisionConflictError) for o in outcomes)
    assert any(o is True for o in outcomes)
    assert len(await diagrams.nodes_of(diagram.id)) == 1


async def test_a_layout_for_a_missing_diagram_writes_nothing(
    diagrams: DiagramsOnStoredElements,
) -> None:
    assert await diagrams.lay_out(uuid4(), [DiagramNode(uuid4(), 0, 0)], now=LATER) is False


async def test_a_failed_layout_leaves_the_previous_one_in_place(
    diagrams: DiagramsOnStoredElements,
) -> None:
    """One transaction: the delete of the old nodes is rolled back with the insert."""
    diagram = await diagrams.add(a_diagram(), audit=())
    kept = DiagramNode(uuid4(), 0, 0)
    await diagrams.lay_out(diagram.id, [kept], now=FIXED_NOW)
    twice = uuid4()

    with pytest.raises(Exception):  # noqa: B017 - the primary key, whatever the driver calls it
        await diagrams.lay_out(
            diagram.id, [DiagramNode(twice, 0, 0), DiagramNode(twice, 1, 1)], now=LATER
        )

    assert await diagrams.nodes_of(diagram.id) == (kept,)


async def test_deleting_a_diagram_takes_its_nodes_by_the_foreign_key(
    diagrams: DiagramsOnStoredElements, engine_at_head: AsyncEngine
) -> None:
    diagram = await diagrams.add(a_diagram(), audit=())
    await diagrams.lay_out(diagram.id, [DiagramNode(uuid4(), 0, 0)], now=FIXED_NOW)

    assert await diagrams.delete(diagram.id, audit=()) is True
    assert await diagrams.delete(diagram.id, audit=()) is False

    async with engine_at_head.connect() as connection:
        left = await connection.scalar(select(func.count()).select_from(DiagramNodeRecord))
    assert left == 0


class TestPackages:
    async def test_a_diagram_keeps_its_package_and_is_filtered_by_it(
        self, engine_at_head: AsyncEngine, diagrams: DiagramsOnStoredElements
    ) -> None:
        packages = PostgresPackageRepository(create_session_factory(engine_at_head))
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW), audit=())
        filed = await diagrams.add(
            Diagram.create(name="Compta", now=FIXED_NOW, package_id=finance.id),
            audit=(),
        )
        await diagrams.add(Diagram.create(name="Racine", now=FIXED_NOW), audit=())

        assert (await diagrams.get(filed.id)).package_id == finance.id  # type: ignore[union-attr]
        assert [d.name for d in await diagrams.list_all(package_ids={finance.id})] == ["Compta"]
        assert [d.name for d in await diagrams.list_all(root_only=True)] == ["Racine"]
        assert (await packages.counts())[finance.id].diagrams == 1

    async def test_an_unknown_package_is_a_not_found_not_a_taken_name(
        self, diagrams: DiagramsOnStoredElements
    ) -> None:
        with pytest.raises(PackageNotFoundError):
            await diagrams.add(
                Diagram.create(name="Vente", now=FIXED_NOW, package_id=uuid4()), audit=()
            )

    async def test_deleting_a_package_moves_its_diagrams_up(
        self, engine_at_head: AsyncEngine, diagrams: DiagramsOnStoredElements
    ) -> None:
        packages = PostgresPackageRepository(create_session_factory(engine_at_head))
        finance = await packages.add(Package.create(name="Finance", now=FIXED_NOW), audit=())
        diagram = await diagrams.add(
            Diagram.create(name="Compta", now=FIXED_NOW, package_id=finance.id),
            audit=(),
        )

        await packages.delete(finance.id, into=None, audit=())

        assert (await diagrams.get(diagram.id)).package_id is None  # type: ignore[union-attr]


async def test_two_concurrent_diagrams_at_the_edge_cannot_both_get_in(
    diagrams: DiagramsOnStoredElements, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ea.repositories.diagram_store.MAX_DIAGRAMS", 2)
    await diagrams.add(a_diagram("first"), audit=())

    outcomes = await asyncio.gather(
        diagrams.add(a_diagram("a"), audit=()),
        diagrams.add(a_diagram("b"), audit=()),
        return_exceptions=True,
    )

    assert sum(isinstance(o, DiagramLimitError) for o in outcomes) == 1
    assert len(await diagrams.list_all()) == 2


async def test_a_listing_reads_one_row_past_the_cap_and_no_more(
    diagrams: DiagramsOnStoredElements, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a", "b", "c"):
        await diagrams.add(a_diagram(name), audit=())
    monkeypatch.setattr("ea.repositories.diagram_store.MAX_DIAGRAMS", 1)

    assert len(await diagrams.list_all()) == 2
