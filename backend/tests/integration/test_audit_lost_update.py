"""Two writers of one element never undo each other (docs/adr/0044).

The service hands the repository a *patch*; the repository applies it to the
row it has locked, so a writer changes only what it means to and its event
diffs exactly that.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction, EntityKind
from ea.domain.ipam import ADDRESS_PROPERTY, VRF_PROPERTY
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.ipam import IpamService
from tests.integration.test_audit_registry import World

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_a_rename_and_a_change_of_properties_both_survive(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    element = await world.graph.create_element(element_type=E.NODE, name="old")

    await asyncio.gather(
        world.graph.update_element(element.id, name="new"),
        world.graph.update_element(element.id, properties={"owner": "ops"}),
    )

    final = await world.graph.get_element(element.id)
    assert (final.name, dict(final.properties)) == ("new", {"owner": "ops"})
    updates = [
        e for e in await world.events(EntityKind.ELEMENT) if e.action is AuditAction.ELEMENT_UPDATED
    ]
    assert sorted(sorted(c.field for c in e.changes) for e in updates) == [
        ["name"],
        ["properties.owner"],
    ]  # neither diff shows the other writer's field going back


async def test_a_rename_and_a_move_into_a_package_both_survive(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P")
    element = await world.graph.create_element(element_type=E.NODE, name="old")

    await asyncio.gather(
        world.graph.update_element(element.id, name="new"),
        world.packages.move_elements([element.id], package.package.id),
    )

    final = await world.graph.get_element(element.id)
    assert (final.name, final.package_id) == ("new", package.package.id)


async def test_a_release_and_a_rename_both_survive(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    ipam = IpamService(
        world.graph,
        PostgresArchitectureRepository(world.graph._repository._sessions),  # type: ignore[attr-defined]
    )
    element = await world.graph.create_element(
        element_type=E.NODE,
        name="old",
        properties={ADDRESS_PROPERTY: "10.0.0.5", VRF_PROPERTY: "default"},
    )

    await asyncio.gather(
        world.graph.update_element(element.id, name="new"),
        ipam.release_address(element.id),
    )

    final = await world.graph.get_element(element.id)
    assert final.name == "new"
    assert ADDRESS_PROPERTY not in final.properties


async def test_an_update_that_changes_nothing_writes_no_event(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    element = await world.graph.create_element(element_type=E.NODE, name="same")

    again = await world.graph.update_element(element.id, name="same")

    assert again.updated_at == element.updated_at  # untouched
    events = await world.events(EntityKind.ELEMENT)
    assert [e.action for e in events] == [AuditAction.ELEMENT_CREATED]
