"""The diagram repository against a real PostgreSQL.

What only the server can prove: migration `0004` applies, the unique name is a
constraint, a layout is replaced in one transaction, deleting a diagram takes
its nodes by the foreign key, and discarding an element touches no other.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.models.diagram import DiagramNodeRecord
from ea.db.postgres import create_session_factory
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.errors import DiagramNotFoundError, DuplicateDiagramError
from ea.repositories.diagram_store import PostgresDiagramRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

FIXED_NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
LATER = FIXED_NOW.replace(year=2027)


@pytest_asyncio.fixture
async def engine_at_head(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> AsyncIterator[AsyncEngine]:
    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    try:
        yield postgres_engine
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")


@pytest.fixture
def diagrams(engine_at_head: AsyncEngine) -> PostgresDiagramRepository:
    return PostgresDiagramRepository(create_session_factory(engine_at_head))


def a_diagram(name: str = "Vente") -> Diagram:
    return Diagram.create(name=name, description="é", now=FIXED_NOW)


async def test_a_diagram_survives_the_round_trip(diagrams: PostgresDiagramRepository) -> None:
    stored = await diagrams.add(a_diagram())

    read = await diagrams.get(stored.id)

    assert read == stored
    assert read is not None and read.created_at.tzinfo is not None


async def test_a_name_taken_twice_hits_the_constraint_and_leaves_the_store_usable(
    diagrams: PostgresDiagramRepository,
) -> None:
    await diagrams.add(a_diagram("Vente"))

    with pytest.raises(DuplicateDiagramError):
        await diagrams.add(a_diagram("Vente"))
    await diagrams.add(a_diagram("Achat"))

    assert [d.name for d in await diagrams.list_all()] == ["Achat", "Vente"]


async def test_a_rename_onto_a_taken_name_hits_the_constraint(
    diagrams: PostgresDiagramRepository,
) -> None:
    await diagrams.add(a_diagram("Vente"))
    other = await diagrams.add(a_diagram("Achat"))

    with pytest.raises(DuplicateDiagramError):
        await diagrams.save(other.revise(name="Vente", now=LATER))


async def test_saving_a_diagram_that_is_gone_says_so(diagrams: PostgresDiagramRepository) -> None:
    with pytest.raises(DiagramNotFoundError):
        await diagrams.save(a_diagram())


async def test_a_layout_is_replaced_whole_and_counted(diagrams: PostgresDiagramRepository) -> None:
    diagram = await diagrams.add(a_diagram())
    first, second = uuid4(), uuid4()
    await diagrams.replace_layout(diagram.id, [DiagramNode(first, 0, 0)], now=FIXED_NOW)

    replaced = await diagrams.replace_layout(
        diagram.id, [DiagramNode(second, 1.25, -3.5)], now=LATER
    )

    assert replaced is True
    assert await diagrams.nodes_of(diagram.id) == (DiagramNode(second, 1.25, -3.5),)
    read = await diagrams.get(diagram.id)
    assert read is not None
    assert (read.node_count, read.updated_at) == (1, LATER)


async def test_concurrent_layouts_of_one_diagram_queue_instead_of_colliding(
    diagrams: PostgresDiagramRepository,
) -> None:
    """Autosave fires on every drag: two saves of the same boxes must not race.

    Without a lock, the second DELETE cannot see the rows the first INSERT
    committed, and its own INSERT then hits the primary key.
    """
    diagram = await diagrams.add(a_diagram())
    moved = uuid4()

    await asyncio.gather(
        *(
            diagrams.replace_layout(diagram.id, [DiagramNode(moved, step, step)], now=LATER)
            for step in range(10)
        )
    )

    assert len(await diagrams.nodes_of(diagram.id)) == 1


async def test_a_layout_for_a_missing_diagram_writes_nothing(
    diagrams: PostgresDiagramRepository,
) -> None:
    assert await diagrams.replace_layout(uuid4(), [DiagramNode(uuid4(), 0, 0)], now=LATER) is False


async def test_a_failed_layout_leaves_the_previous_one_in_place(
    diagrams: PostgresDiagramRepository,
) -> None:
    """One transaction: the delete of the old nodes is rolled back with the insert."""
    diagram = await diagrams.add(a_diagram())
    kept = DiagramNode(uuid4(), 0, 0)
    await diagrams.replace_layout(diagram.id, [kept], now=FIXED_NOW)
    twice = uuid4()

    with pytest.raises(Exception):  # noqa: B017 - the primary key, whatever the driver calls it
        await diagrams.replace_layout(
            diagram.id, [DiagramNode(twice, 0, 0), DiagramNode(twice, 1, 1)], now=LATER
        )

    assert await diagrams.nodes_of(diagram.id) == (kept,)


async def test_deleting_a_diagram_takes_its_nodes_by_the_foreign_key(
    diagrams: PostgresDiagramRepository, engine_at_head: AsyncEngine
) -> None:
    diagram = await diagrams.add(a_diagram())
    await diagrams.replace_layout(diagram.id, [DiagramNode(uuid4(), 0, 0)], now=FIXED_NOW)

    assert await diagrams.delete(diagram.id) is True
    assert await diagrams.delete(diagram.id) is False

    async with engine_at_head.connect() as connection:
        left = await connection.scalar(select(func.count()).select_from(DiagramNodeRecord))
    assert left == 0


async def test_discarding_an_element_removes_its_boxes_from_every_diagram_and_no_others(
    diagrams: PostgresDiagramRepository,
) -> None:
    doomed, spared = uuid4(), uuid4()
    first = await diagrams.add(a_diagram("A"))
    second = await diagrams.add(a_diagram("B"))
    for diagram in (first, second):
        await diagrams.replace_layout(
            diagram.id, [DiagramNode(doomed, 0, 0), DiagramNode(spared, 1, 1)], now=FIXED_NOW
        )

    assert await diagrams.discard_for_element(doomed) == 2
    assert await diagrams.nodes_of(first.id) == (DiagramNode(spared, 1, 1),)
    assert await diagrams.discard_for_element(uuid4()) == 0
