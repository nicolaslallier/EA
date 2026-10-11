"""Packages and saved queries: the event is in the database iff the write is (docs/adr/0046).

Against a real PostgreSQL, under the tree's advisory lock for the packages.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.audit import AuditAction, AuditEvent, AuditFilter, EntityKind
from ea.domain.errors import (
    CyclicPackageError,
    DuplicatePackageError,
    DuplicateSavedQueryError,
    ElementNotFoundError,
    PackageNotEmptyError,
)
from ea.domain.query import ElementQuery
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository
from ea.repositories.package_store import PostgresPackageRepository
from ea.repositories.saved_query_store import PostgresSavedQueryRepository
from ea.services.architecture import ArchitectureService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

START = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_ticking_clock():  # type: ignore[no-untyped-def]
    ticks = count()
    return lambda: START + timedelta(milliseconds=next(ticks))


class World:
    def __init__(self, engine: AsyncEngine) -> None:
        sessions = create_session_factory(engine)
        clock = a_ticking_clock()
        self.graph = ArchitectureService(PostgresArchitectureRepository(sessions), clock=clock)
        self.package_store = PostgresPackageRepository(sessions)
        self.packages = PackageService(self.package_store, clock=clock)
        self.queries = SavedQueryService(
            PostgresSavedQueryRepository(sessions), self.package_store, self.graph, clock=clock
        )
        self.trail = PostgresAuditRepository(sessions)

    async def events(self, kind: EntityKind) -> list[AuditEvent]:
        page = await self.trail.page(AuditFilter(entity_kind=kind, limit=200))
        return list(reversed(page.events))  # oldest first


async def test_package_writes_and_refusals(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    root = await world.packages.create(name="Root")
    child = await world.packages.create(name="Child", parent_id=root.package.id)
    element = await world.graph.create_element(element_type=E.NODE, name="n")
    await world.packages.move_elements([element.id], child.package.id)
    before = len(await world.events(EntityKind.PACKAGE))

    with pytest.raises(DuplicatePackageError):
        await world.packages.create(name="root")
    with pytest.raises(CyclicPackageError):
        await world.packages.update(root.package.id, parent_id=child.package.id)
    with pytest.raises(PackageNotEmptyError):
        await world.packages.delete(root.package.id)
    with pytest.raises(ElementNotFoundError):
        await world.packages.move_elements([uuid4()], root.package.id)

    assert len(await world.events(EntityKind.PACKAGE)) == before  # four refusals, no event

    await world.packages.delete(child.package.id, move_contents_to=root.package.id)
    moved, deleted = (await world.events(EntityKind.PACKAGE))[-2:]
    assert moved.action is AuditAction.PACKAGE_ELEMENTS_MOVED
    assert moved.before == {"origins": {"root": [str(element.id)]}}
    assert deleted.action is AuditAction.PACKAGE_DELETED
    assert deleted.before is not None
    assert deleted.before["cascaded"]["elements"] == {"count": 1, "ids": [str(element.id)]}
    assert deleted.before["cascaded"]["moved_to"] == str(root.package.id)
    assert await world.package_store.get(child.package.id) is None  # and the story remains


async def test_two_concurrent_renames_chain_their_snapshots(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P0")

    await asyncio.gather(
        world.packages.update(package.package.id, name="P1"),
        world.packages.update(package.package.id, name="P2"),
    )

    _, first, second = await world.events(EntityKind.PACKAGE)
    assert first.before is not None and first.after is not None and second.before is not None
    assert first.before["name"] == "P0"
    assert second.before["name"] == first.after["name"]


async def test_moving_reads_the_origins_inside_the_transaction(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    one = await world.packages.create(name="One")
    two = await world.packages.create(name="Two")
    element = await world.graph.create_element(element_type=E.NODE, name="n")

    await asyncio.gather(
        world.packages.move_elements([element.id], one.package.id),
        world.packages.move_elements([element.id], two.package.id),
    )

    moves = [
        e
        for e in await world.events(EntityKind.PACKAGE)
        if e.action is AuditAction.PACKAGE_ELEMENTS_MOVED
    ]
    first, second = moves
    assert first.before == {"origins": {"root": [str(element.id)]}}
    assert first.after is not None and second.before is not None
    assert second.before["origins"] == {first.after["destination"]: [str(element.id)]}


async def test_saved_query_writes_refusals_and_a_stale_row(engine_at_head: AsyncEngine) -> None:
    world = World(engine_at_head)
    saved = await world.queries.create(name="A", query=ElementQuery(text="db"))
    await world.queries.update(saved.id, query=ElementQuery(text="web"))
    with pytest.raises(DuplicateSavedQueryError):
        await world.queries.create(name="A", query=ElementQuery())
    async with engine_at_head.begin() as connection:
        # Test setup with literal values: a row whose JSON the code no longer reads.
        await connection.execute(
            text(
                "INSERT INTO saved_queries (id, name, description, query, created_by, "
                "created_at, updated_at) VALUES (:id, 'old', '', "
                "CAST('{\"version\": 99}' AS jsonb), 'someone', now(), now())"
            ),
            {"id": (stale := uuid4())},
        )

    await world.queries.delete(saved.id)
    await world.queries.delete(stale)

    events = await world.events(EntityKind.SAVED_QUERY)
    assert [e.action for e in events] == [
        AuditAction.SAVED_QUERY_CREATED,
        AuditAction.SAVED_QUERY_UPDATED,
        AuditAction.SAVED_QUERY_DELETED,
        AuditAction.SAVED_QUERY_DELETED,
    ]
    assert events[1].before is not None and events[1].before["query"]["text"] == "db"
    assert events[3].before is not None and events[3].before["query"] == {"version": 99}


async def test_two_concurrent_reparentings_chain_their_snapshots(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    package = await world.packages.create(name="P")
    one = await world.packages.create(name="One")
    two = await world.packages.create(name="Two")

    await asyncio.gather(
        world.packages.update(package.package.id, parent_id=one.package.id),
        world.packages.update(package.package.id, parent_id=two.package.id),
    )

    updates = [
        e for e in await world.events(EntityKind.PACKAGE) if e.action is AuditAction.PACKAGE_UPDATED
    ]
    first, second = updates
    assert first.before is not None and first.after is not None and second.before is not None
    assert first.before["parent_id"] is None
    assert second.before["parent_id"] == first.after["parent_id"]


async def test_a_move_and_a_deletion_racing_tell_one_consistent_story(
    engine_at_head: AsyncEngine,
) -> None:
    world = World(engine_at_head)
    holder = await world.packages.create(name="Holder")
    target = await world.packages.create(name="Target")
    element = await world.graph.create_element(element_type=E.NODE, name="n")
    await world.packages.move_elements([element.id], holder.package.id)

    await asyncio.gather(
        world.packages.delete(holder.package.id, move_contents_to=None),
        world.packages.move_elements([element.id], target.package.id),
        return_exceptions=True,
    )

    events = await world.events(EntityKind.PACKAGE)
    deleted = next(e for e in events if e.action is AuditAction.PACKAGE_DELETED)
    last_move = [e for e in events if e.action is AuditAction.PACKAGE_ELEMENTS_MOVED][-1]
    assert deleted.before is not None and last_move.before is not None
    took = deleted.before["cascaded"]["elements"]["count"]
    origin = last_move.before["origins"]
    if took == 1:  # the deletion ran first: it sent the element to the root
        assert origin == {"root": [str(element.id)]}
    else:  # the move ran first: the deletion found nothing to carry
        assert origin == {str(holder.package.id): [str(element.id)]}
