"""The scene tables against a real PostgreSQL: cascades, SET NULL, one transaction, a revision."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagram_objects import (
    DiagramEdge,
    DiagramObject,
    DiagramScene,
    ObjectKind,
    Point,
)
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.errors import DiagramRevisionConflictError
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.diagram_store import PostgresDiagramRepository

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_element(name: str) -> Element:
    return Element.create(element_type=ElementType.APPLICATION_COMPONENT, name=name, now=NOW)


def a_group(**overrides: object) -> DiagramObject:
    fields: dict[str, object] = {
        "id": uuid4(),
        "kind": ObjectKind.GROUP,
        "x": 0.0,
        "y": 0.0,
        "width": 300.0,
        "height": 200.0,
        "text": "Domaine",
    }
    return DiagramObject(**{**fields, **overrides})  # type: ignore[arg-type]


@pytest.fixture
def graph(engine_at_head: AsyncEngine) -> PostgresArchitectureRepository:
    return PostgresArchitectureRepository(create_session_factory(engine_at_head))


@pytest.fixture
def diagrams(engine_at_head: AsyncEngine) -> PostgresDiagramRepository:
    return PostgresDiagramRepository(create_session_factory(engine_at_head))


async def a_stored_diagram(diagrams: PostgresDiagramRepository) -> Diagram:
    return await diagrams.add(Diagram.create(name=f"Vue {uuid4()}", now=NOW), audit=())


async def test_a_scene_round_trips_with_nesting_notes_and_bends(
    diagrams: PostgresDiagramRepository, graph: PostgresArchitectureRepository
) -> None:
    source = await graph.add_element(an_element("A"), audit=())
    target = await graph.add_element(an_element("B"), audit=())
    link = await graph.add_relationship(
        Relationship.between(RelationshipType.ASSOCIATION, source, target, now=NOW), audit=()
    )
    diagram = await a_stored_diagram(diagrams)
    outer = a_group()
    inner = a_group(parent_id=outer.id)
    note = a_group(
        kind=ObjectKind.NOTE, text="À revoir", anchor_element_id=source.id, colour="#ffe0e0", z=3
    )
    scene = DiagramScene(
        nodes=(
            DiagramNode(source.id, 10, 10, parent_object_id=inner.id, z=2),
            DiagramNode(target.id, 400, 10),
        ),
        objects=(outer, inner, note),
        edges=(DiagramEdge(link.id, (Point(100, 80), Point(250, 60))),),
    )

    revision = await diagrams.replace_layout(diagram.id, scene, revision=0, now=NOW, audit=())

    assert revision == 1
    assert {o.id: o for o in await diagrams.objects_of(diagram.id)} == {
        o.id: o for o in scene.objects
    }
    assert await diagrams.edges_of(diagram.id) == scene.edges
    assert {n.element_id: n for n in await diagrams.nodes_of(diagram.id)} == {
        n.element_id: n for n in scene.nodes
    }
    stored = await diagrams.get(diagram.id)
    assert stored is not None and stored.revision == 1


async def test_a_stale_revision_writes_nothing(
    diagrams: PostgresDiagramRepository, graph: PostgresArchitectureRepository
) -> None:
    element = await graph.add_element(an_element("A"), audit=())
    diagram = await a_stored_diagram(diagrams)
    await diagrams.replace_layout(
        diagram.id,
        DiagramScene(nodes=(DiagramNode(element.id, 1, 1),)),
        revision=0,
        now=NOW,
        audit=(),
    )

    with pytest.raises(DiagramRevisionConflictError, match="revision 1"):
        await diagrams.replace_layout(
            diagram.id, DiagramScene(objects=(a_group(),)), revision=0, now=NOW, audit=()
        )

    assert await diagrams.objects_of(diagram.id) == ()
    assert len(await diagrams.nodes_of(diagram.id)) == 1


async def test_two_savers_on_one_revision_leave_exactly_one_winner(
    diagrams: PostgresDiagramRepository,
) -> None:
    diagram = await a_stored_diagram(diagrams)

    results = await asyncio.gather(
        *(
            diagrams.replace_layout(
                diagram.id,
                DiagramScene(objects=(a_group(text=str(i)),)),
                revision=0,
                now=NOW,
                audit=(),
            )
            for i in range(4)
        ),
        return_exceptions=True,
    )

    assert sum(1 for r in results if r == 1) == 1
    assert sum(1 for r in results if isinstance(r, DiagramRevisionConflictError)) == 3


async def test_a_refused_scene_leaves_the_previous_one_in_place(
    diagrams: PostgresDiagramRepository,
) -> None:
    diagram = await a_stored_diagram(diagrams)
    kept = a_group()
    await diagrams.replace_layout(
        diagram.id, DiagramScene(objects=(kept,)), revision=0, now=NOW, audit=()
    )

    with pytest.raises(Exception):  # noqa: B017 — a foreign key violation, whichever driver type
        await diagrams.replace_layout(
            diagram.id,
            DiagramScene(edges=(DiagramEdge(uuid4(), ()),)),  # a relationship that does not exist
            revision=1,
            now=NOW,
            audit=(),
        )

    assert [o.id for o in await diagrams.objects_of(diagram.id)] == [kept.id]


async def test_deleting_an_element_frees_its_note_and_keeps_the_group_and_siblings(
    diagrams: PostgresDiagramRepository, graph: PostgresArchitectureRepository
) -> None:
    doomed = await graph.add_element(an_element("Doomed"), audit=())
    sibling = await graph.add_element(an_element("Sibling"), audit=())
    diagram = await a_stored_diagram(diagrams)
    group = a_group()
    note = a_group(kind=ObjectKind.NOTE, anchor_element_id=doomed.id)
    await diagrams.replace_layout(
        diagram.id,
        DiagramScene(
            nodes=(
                DiagramNode(doomed.id, 0, 0, parent_object_id=group.id),
                DiagramNode(sibling.id, 50, 0, parent_object_id=group.id),
            ),
            objects=(group, note),
        ),
        revision=0,
        now=NOW,
        audit=(),
    )

    assert await graph.delete_element(doomed.id, audit=()) is True

    nodes = await diagrams.nodes_of(diagram.id)
    assert [(n.element_id, n.parent_object_id) for n in nodes] == [(sibling.id, group.id)]
    objects = {o.id: o for o in await diagrams.objects_of(diagram.id)}
    assert set(objects) == {group.id, note.id}
    assert objects[note.id].anchor_element_id is None


async def test_deleting_a_relationship_takes_its_route(
    diagrams: PostgresDiagramRepository, graph: PostgresArchitectureRepository
) -> None:
    source = await graph.add_element(an_element("A"), audit=())
    target = await graph.add_element(an_element("B"), audit=())
    link = await graph.add_relationship(
        Relationship.between(RelationshipType.ASSOCIATION, source, target, now=NOW), audit=()
    )
    diagram = await a_stored_diagram(diagrams)
    await diagrams.replace_layout(
        diagram.id,
        DiagramScene(
            nodes=(DiagramNode(source.id, 0, 0), DiagramNode(target.id, 300, 0)),
            edges=(DiagramEdge(link.id, (Point(150, 50),)),),
        ),
        revision=0,
        now=NOW,
        audit=(),
    )

    await graph.delete_relationship(link.id, audit=())

    assert await diagrams.edges_of(diagram.id) == ()


async def test_deleting_a_diagram_takes_its_objects_and_routes(
    diagrams: PostgresDiagramRepository,
) -> None:
    diagram = await a_stored_diagram(diagrams)
    await diagrams.replace_layout(
        diagram.id, DiagramScene(objects=(a_group(),)), revision=0, now=NOW, audit=()
    )

    assert await diagrams.delete(diagram.id, audit=()) is True

    assert await diagrams.objects_of(diagram.id) == ()


async def test_a_parent_group_that_goes_away_lets_its_children_go_free(
    engine_at_head: AsyncEngine, diagrams: PostgresDiagramRepository
) -> None:
    diagram = await a_stored_diagram(diagrams)
    group = a_group()
    note = a_group(kind=ObjectKind.NOTE, parent_id=group.id)
    await diagrams.replace_layout(
        diagram.id, DiagramScene(objects=(group, note)), revision=0, now=NOW, audit=()
    )

    # The DDL net, not a use case: a bare delete of the group row.
    async with engine_at_head.begin() as connection:
        await connection.execute(
            text("DELETE FROM diagram_objects WHERE id = :id"), {"id": group.id}
        )  # bound parameter

    remaining = await diagrams.objects_of(diagram.id)
    assert [(o.id, o.parent_id) for o in remaining] == [(note.id, None)]


ELEMENT_SQL = (
    "INSERT INTO elements (id, element_type, layer, aspect, name, description, documentation,"
    " properties, created_at, updated_at)"
    " VALUES (:id, 'node', 'technology', 'active_structure', :name, '', '',"
    " '{}'::jsonb, :now, :now)"
)
DIAGRAM_SQL = (
    "INSERT INTO diagrams (id, name, description, created_at, updated_at)"
    " VALUES (:id, :name, :description, :now, :now)"
)
NODE_SQL = (
    "INSERT INTO diagram_nodes (diagram_id, element_id, x, y, width, height)"
    " VALUES (:d, :e, :x, :y, :w, :h)"
)


async def test_revision_0015_applies_over_a_database_that_already_has_diagrams(
    postgres_engine: AsyncEngine, alembic_config: Config
) -> None:
    """Hostile but valid rows from the era before: odd names, extreme boxes, many of them."""
    await asyncio.to_thread(command.upgrade, alembic_config, "0014")
    try:
        names = ["Vieille vue", 'L\'été "2025" ; DROP TABLE x;--', "é" * 200, "🧱 " * 50]
        diagram_ids = [uuid4() for _ in names]
        element_ids = [uuid4() for _ in range(30)]
        async with postgres_engine.begin() as connection:
            # Test-only raw SQL, every value bound: the schema before 0015 has no ORM model.
            await connection.execute(
                text(ELEMENT_SQL),
                [{"id": e, "name": f"n{i}", "now": NOW} for i, e in enumerate(element_ids)],
            )
            await connection.execute(
                text(DIAGRAM_SQL),
                [
                    {"id": d, "name": n, "description": n, "now": NOW}
                    for d, n in zip(diagram_ids, names, strict=True)
                ],
            )
            await connection.execute(
                text(NODE_SQL),
                [
                    {"d": d, "e": e, "x": 1e9 * (i % 3 - 1), "y": -0.0, "w": 1e-9, "h": 5e8}
                    for d in diagram_ids
                    for i, e in enumerate(element_ids)
                ],
            )

        await asyncio.to_thread(command.upgrade, alembic_config, "head")

        async with postgres_engine.connect() as connection:
            revisions = (await connection.execute(text("SELECT revision FROM diagrams"))).scalars()
            boxes = (
                await connection.execute(
                    text("SELECT z, parent_object_id FROM diagram_nodes"),
                )
            ).all()
        assert set(revisions) == {0}
        assert len(boxes) == len(diagram_ids) * len(element_ids)
        assert set(boxes) == {(0, None)}
    finally:
        await asyncio.to_thread(command.downgrade, alembic_config, "base")
