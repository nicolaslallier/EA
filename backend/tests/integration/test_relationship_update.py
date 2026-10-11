"""PATCH /relationships/{id} and junctions against a real PostgreSQL (docs/adr/0062, 0063)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditAction, EntityKind
from ea.domain.errors import ElementLockedError, ElementNotFoundError
from tests.integration.test_audit_registry import World
from tests.integration.test_element_review import (
    put_in_state,
    state_of,
    wait_until_a_statement_waits_for_a_lock,
)

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_a_link_is_changed_in_place_with_one_event_and_the_review_of_its_ends(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    a = await world.graph.create_element(element_type=E.NODE, name="a")
    b = await world.graph.create_element(element_type=E.NODE, name="b")
    link = await world.graph.connect(
        relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id, properties={"x": "1"}
    )
    await put_in_state(engine_at_head, a.id, "approved")

    updated = await world.graph.update_relationship(
        link.id, name="owns", directed=True, properties={"y": "2"}
    )

    assert (updated.name, updated.directed, dict(updated.properties)) == ("owns", True, {"y": "2"})
    (stored,) = (await world.graph.relations_of(a.id)).relationships
    assert stored == updated
    assert (stored.source_id, stored.target_id, stored.created_at) == (
        link.source_id,
        link.target_id,
        link.created_at,
    )
    assert (await state_of(engine_at_head, a.id))[0] == "in_review"
    events = [
        e for e in await world.events(EntityKind.RELATIONSHIP) if e.action.value.endswith("updated")
    ]
    assert len(events) == 1
    assert events[0].before is not None and events[0].before["properties"] == {"x": "1"}
    assert events[0].after is not None and events[0].after["properties"] == {"y": "2"}


async def test_changing_nothing_takes_no_revision_and_writes_no_event(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    a = await world.graph.create_element(element_type=E.NODE, name="a")
    b = await world.graph.create_element(element_type=E.NODE, name="b")
    link = await world.graph.connect(
        relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id, name="same"
    )
    await put_in_state(engine_at_head, a.id, "approved")
    before = await state_of(engine_at_head, a.id)

    await world.graph.update_relationship(link.id, name="same")

    assert await state_of(engine_at_head, a.id) == before
    assert [
        e for e in await world.events(EntityKind.RELATIONSHIP) if e.action.value.endswith("updated")
    ] == []


async def test_a_locked_end_refuses_and_nothing_is_written(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    a = await world.graph.create_element(element_type=E.NODE, name="a")
    b = await world.graph.create_element(element_type=E.NODE, name="b")
    link = await world.graph.connect(
        relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id, name="before"
    )
    await put_in_state(engine_at_head, b.id, "approved", locked=True)

    with pytest.raises(ElementLockedError):
        await world.graph.update_relationship(link.id, name="after")

    (stored,) = (await world.graph.relations_of(a.id)).relationships
    assert stored.name == "before"
    assert [
        e for e in await world.events(EntityKind.RELATIONSHIP) if e.action.value.endswith("updated")
    ] == []


async def test_an_unknown_link_is_not_found(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)

    with pytest.raises(ElementNotFoundError):
        await world.graph.update_relationship(uuid4(), name="x")


async def test_a_lock_put_while_the_patch_waits_is_honoured(engine_at_head: AsyncEngine) -> None:
    """The ends are judged under their lock, not on the copy read before it."""
    world = World(engine_at_head)
    a = await world.graph.create_element(element_type=E.NODE, name="a")
    b = await world.graph.create_element(element_type=E.NODE, name="b")
    link = await world.graph.connect(
        relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id
    )
    await put_in_state(engine_at_head, a.id, "approved")
    sessions = create_session_factory(engine_at_head)

    async with sessions.begin() as session:
        await session.execute(
            text("UPDATE elements SET locked = true WHERE id = :id"), {"id": a.id}
        )
        writer = asyncio.create_task(world.graph.update_relationship(link.id, name="late"))
        await wait_until_a_statement_waits_for_a_lock(engine_at_head)
    with pytest.raises(ElementLockedError):
        await writer

    (stored,) = (await world.graph.relations_of(a.id)).relationships
    assert stored.name == ""


async def test_two_patches_of_one_link_both_land_on_the_row_the_other_left(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    a = await world.graph.create_element(element_type=E.NODE, name="a")
    b = await world.graph.create_element(element_type=E.NODE, name="b")
    link = await world.graph.connect(
        relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id
    )

    await asyncio.gather(
        world.graph.update_relationship(link.id, name="renamed"),
        world.graph.update_relationship(link.id, directed=True),
    )

    (stored,) = (await world.graph.relations_of(a.id)).relationships
    assert (stored.name, stored.directed) == ("renamed", True)  # neither undid the other


async def test_a_junction_wires_two_processes_and_survives_a_round_trip_of_the_store(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    a = await world.graph.create_element(element_type=E.BUSINESS_PROCESS, name="a")
    b = await world.graph.create_element(element_type=E.BUSINESS_PROCESS, name="b")
    gate = await world.graph.create_element(
        element_type=E.JUNCTION, name="Choice", properties={"junction_type": "or"}
    )
    await world.graph.connect(relationship_type=R.TRIGGERING, source_id=a.id, target_id=gate.id)
    await world.graph.connect(relationship_type=R.TRIGGERING, source_id=gate.id, target_id=b.id)

    reread = await world.graph.get_element(gate.id)
    around = await world.graph.relations_of(gate.id)

    assert (reread.element_type, reread.properties["junction_type"]) == (E.JUNCTION, "or")
    assert len(around.relationships) == 2
    impact = await world.graph.impact_of(a.id, depth=3)
    assert {e.name for e in impact.elements} >= {"a", "Choice", "b"}
    assert AuditAction.RELATIONSHIP_UPDATED.value == "relationship.updated"
