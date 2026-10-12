"""Generated views against a real PostgreSQL: the source columns, freeze, thaw, RESTRICT."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType
from ea.domain.diagram_objects import DiagramObject, DiagramScene, ObjectKind
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.errors import (
    DiagramLiveError,
    DiagramNotLiveError,
    DiagramRevisionConflictError,
    SourceInUseError,
)
from ea.domain.generated_views import Generation, PackageSource, QuerySource
from ea.domain.model import Element
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.diagram_store import PostgresDiagramRepository
from ea.repositories.package_store import PostgresPackageRepository
from ea.repositories.saved_query_store import PostgresSavedQueryRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


async def a_saved_query(engine: AsyncEngine) -> UUID:
    query_id = uuid4()
    async with engine.begin() as connection:
        # Test-only raw SQL, every value bound.
        await connection.execute(
            text(
                "INSERT INTO saved_queries (id, name, description, query, created_by,"
                " created_at, updated_at)"
                " VALUES (:id, :name, '', CAST(:query AS jsonb), 'test', :now, :now)"
            ),
            {"id": query_id, "name": f"q-{query_id}", "query": '{"version": 1}', "now": NOW},
        )
    return query_id


async def a_package(engine: AsyncEngine) -> UUID:
    package_id = uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO packages (id, name, description, created_at, updated_at)"
                " VALUES (:id, :name, '', :now, :now)"
            ),
            {"id": package_id, "name": f"p-{package_id}", "now": NOW},
        )
    return package_id


@pytest.fixture
def diagrams(engine_at_head: AsyncEngine) -> PostgresDiagramRepository:
    return PostgresDiagramRepository(create_session_factory(engine_at_head))


async def test_a_generated_view_round_trips_with_its_source(
    diagrams: PostgresDiagramRepository, engine_at_head: AsyncEngine
) -> None:
    query = QuerySource(await a_saved_query(engine_at_head))
    package = PackageSource(await a_package(engine_at_head), recursive=True)

    by_query = await diagrams.add(
        Diagram.create(name="Q", now=NOW, generation=Generation.LIVE, source=query), audit=()
    )
    by_package = await diagrams.add(
        Diagram.create(name="P", now=NOW, generation=Generation.LIVE, source=package), audit=()
    )

    read_query = await diagrams.get(by_query.id)
    read_package = await diagrams.get(by_package.id)
    assert read_query is not None and read_package is not None
    assert (read_query.source, read_query.generation) == (query, Generation.LIVE)
    assert read_package.source == package


async def test_a_source_in_use_cannot_be_deleted_and_the_refusal_is_typed(
    diagrams: PostgresDiagramRepository, engine_at_head: AsyncEngine
) -> None:
    sessions = create_session_factory(engine_at_head)
    query_id = await a_saved_query(engine_at_head)
    package_id = await a_package(engine_at_head)
    await diagrams.add(
        Diagram.create(
            name="Vue A", now=NOW, generation=Generation.LIVE, source=QuerySource(query_id)
        ),
        audit=(),
    )
    await diagrams.add(
        Diagram.create(
            name="Vue B", now=NOW, generation=Generation.LIVE, source=PackageSource(package_id)
        ),
        audit=(),
    )

    assert await diagrams.names_using_source(query_id=query_id) == ("Vue A",)
    with pytest.raises(SourceInUseError):
        await PostgresSavedQueryRepository(sessions).delete(query_id, audit=())
    with pytest.raises(SourceInUseError):
        await PostgresPackageRepository(sessions).delete(
            package_id, into=None, audit=(), require_empty=False
        )


async def test_a_diagram_cannot_have_two_sources_nor_a_generation_without_one(
    engine_at_head: AsyncEngine,
) -> None:
    query_id = await a_saved_query(engine_at_head)
    package_id = await a_package(engine_at_head)
    insert = text(
        "INSERT INTO diagrams (id, name, description, created_at, updated_at, generation,"
        " source_query_id, source_package_id)"
        " VALUES (:id, :name, '', :now, :now, :generation, :q, :p)"
    )

    cases = (("live", query_id, package_id), ("live", None, None), (None, query_id, None))
    for generation, q, p in cases:
        with pytest.raises(IntegrityError):
            async with engine_at_head.begin() as connection:
                await connection.execute(
                    insert,
                    {
                        "id": uuid4(),
                        "name": f"d-{uuid4()}",
                        "now": NOW,
                        "generation": generation,
                        "q": q,
                        "p": p,
                    },
                )


async def test_freezing_stores_the_boxes_and_thawing_clears_everything_each_on_its_revision(
    diagrams: PostgresDiagramRepository, engine_at_head: AsyncEngine
) -> None:
    graph = PostgresArchitectureRepository(create_session_factory(engine_at_head))
    element = await graph.add_element(
        Element.create(element_type=ElementType.APPLICATION_COMPONENT, name="A", now=NOW),
        audit=(),
    )
    query = QuerySource(await a_saved_query(engine_at_head))
    live = await diagrams.add(
        Diagram.create(name="Vue", now=NOW, generation=Generation.LIVE, source=query), audit=()
    )
    with pytest.raises(DiagramLiveError):
        await diagrams.replace_layout(live.id, DiagramScene(), revision=0, now=NOW, audit=())
    with pytest.raises(DiagramRevisionConflictError):
        await diagrams.freeze(live.id, [], revision=7, now=NOW, audit=())

    revision = await diagrams.freeze(
        live.id, [DiagramNode(element.id, 1, 2)], revision=0, now=NOW, audit=()
    )
    frozen = await diagrams.get(live.id)

    assert frozen is not None
    assert (revision, frozen.generation, frozen.revision) == (1, Generation.FROZEN, 1)
    assert [n.element_id for n in await diagrams.nodes_of(live.id)] == [element.id]
    with pytest.raises(DiagramNotLiveError):
        await diagrams.freeze(live.id, [], revision=1, now=NOW, audit=())

    note = DiagramObject(uuid4(), ObjectKind.NOTE, 0, 0, 100, 50, text="x")
    scene = DiagramScene(nodes=(DiagramNode(element.id, 1, 2),), objects=(note,))
    await diagrams.replace_layout(live.id, scene, revision=1, now=NOW, audit=())
    thawed_revision = await diagrams.unfreeze(live.id, revision=2, now=NOW, audit=())

    assert thawed_revision == 3
    assert (await diagrams.nodes_of(live.id), await diagrams.objects_of(live.id)) == ((), ())
    thawed = await diagrams.get(live.id)
    assert thawed is not None and (thawed.generation, thawed.source) == (Generation.LIVE, query)
    with pytest.raises(DiagramLiveError):
        await diagrams.unfreeze(live.id, revision=3, now=NOW, audit=())
