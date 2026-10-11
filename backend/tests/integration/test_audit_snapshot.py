"""An event describes the row that was really replaced (docs/adr/0044).

The service builds the event, but the *before* it holds is read by the
repository inside the transaction of the write, under the row's lock: two
writers of one element cannot both log the same "before".
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AUDIT_LISTED_IDS, AuditAction, AuditFilter, EntityKind
from ea.domain.errors import ElementNotFoundError
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementChange, ElementRemoval
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.audit_store import PostgresAuditRepository
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import an_editor

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

START = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def a_ticking_clock():  # type: ignore[no-untyped-def]
    ticks = count()
    return lambda: START + timedelta(milliseconds=next(ticks))


async def test_two_concurrent_updates_chain_their_snapshots(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    service = ArchitectureService(PostgresArchitectureRepository(sessions), clock=a_ticking_clock())
    trail = PostgresAuditRepository(sessions)
    element = await service.create_element(element_type=E.NODE, name="db-00")

    await asyncio.gather(
        service.update_element(element.id, name="db-01"),
        service.update_element(element.id, name="db-02"),
    )

    page = await trail.page(AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=element.id))
    created, first, second = reversed(page.events)
    assert created.action is AuditAction.ELEMENT_CREATED
    assert first.before is not None and first.after is not None
    assert second.before is not None and second.after is not None
    assert first.before["name"] == "db-00"
    assert second.before["name"] == first.after["name"]  # never two identical "before"
    assert {first.after["name"], second.after["name"]} == {"db-01", "db-02"}


async def test_the_audit_callable_receives_the_row_read_under_the_lock(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    trail = PostgresAuditRepository(sessions)
    left, right = (
        Element.create(element_type=E.NODE, name="a", now=START),
        Element.create(element_type=E.NODE, name="b", now=START),
    )
    await graph.add_element(left, audit=())
    await graph.add_element(right, audit=())
    link = await graph.add_relationship(
        Relationship.between(R.ASSOCIATION, left, right, now=START), audit=()
    )
    seen: list[object] = []

    def on_save(change: ElementChange) -> tuple[()]:
        seen.append(change.before)
        return ()

    def on_removal(before: ElementRemoval) -> tuple[()]:
        seen.append(before)
        return ()

    def on_unlink(before: Relationship) -> tuple[()]:
        seen.append(before)
        return ()

    await graph.update_element(left.id, lambda e: e.rename("a2", now=START), audit=on_save)
    await graph.delete_relationship(link.id, audit=on_unlink)
    await graph.delete_element(right.id, audit=on_removal)

    assert [type(item).__name__ for item in seen] == ["Element", "Relationship", "ElementRemoval"]
    assert seen[0].name == "a"  # type: ignore[attr-defined]
    assert seen[1].id == link.id  # type: ignore[attr-defined]
    assert seen[2].relationships == ()  # type: ignore[attr-defined]  # unlinked just before
    assert (await trail.page(AuditFilter())).events == ()
    # A missing row calls nothing: a refusal writes no event.
    assert not await graph.delete_element(uuid4(), audit=on_removal)
    assert len(seen) == 3
    with pytest.raises(ElementNotFoundError):
        await graph.update_element(uuid4(), lambda e: e.rename("ghost", now=START), audit=on_save)
    assert len(seen) == 3


async def test_a_delete_racing_a_link_lists_exactly_what_it_took(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    service = ArchitectureService(PostgresArchitectureRepository(sessions), clock=a_ticking_clock())
    trail = PostgresAuditRepository(sessions)
    doomed = await service.create_element(element_type=E.NODE, name="doomed")
    other = await service.create_element(element_type=E.NODE, name="other")

    linked, deleted = await asyncio.gather(
        service.connect(relationship_type=R.ASSOCIATION, source_id=doomed.id, target_id=other.id),
        service.delete_element(doomed.id),
        return_exceptions=True,
    )

    page = await trail.page(AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=doomed.id))
    removal = next(e for e in page.events if e.action is AuditAction.ELEMENT_DELETED)
    assert deleted is None
    assert removal.before is not None
    listed = [r["id"] for r in removal.before["cascaded"]["relationships"]["items"]]
    if isinstance(linked, ElementNotFoundError):
        assert listed == []  # the link lost the race and was refused: nothing to list
    else:
        assert listed == [str(linked.id)]  # type: ignore[union-attr]


async def test_concurrent_callers_never_borrow_each_others_identity(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    service = ArchitectureService(PostgresArchitectureRepository(sessions), clock=a_ticking_clock())
    trail = PostgresAuditRepository(sessions)

    async def create_as(name: str) -> None:
        with acting_as(an_editor(subject=f"sub-{name}", username=name)):
            await service.create_element(element_type=E.NODE, name=name)

    await asyncio.gather(*(create_as(f"node-{index}") for index in range(8)))

    events = (await trail.page(AuditFilter())).events
    assert len(events) == 8
    for event in events:
        assert event.after is not None
        assert event.actor_name == event.after["name"]
        assert event.actor_sub == f"sub-{event.after['name']}"


async def test_an_event_that_cannot_be_built_leaves_no_write(engine_at_head: AsyncEngine) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    element = Element.create(element_type=E.NODE, name="a", now=START)
    await graph.add_element(element, audit=())

    def refuses(_: ElementChange) -> tuple[()]:
        raise RuntimeError("no caller")

    with pytest.raises(RuntimeError):
        await graph.update_element(
            element.id, lambda e: e.rename("changed", now=START), audit=refuses
        )

    assert (await graph.get_element(element.id)).name == "a"  # type: ignore[union-attr]


async def test_a_deleted_hub_costs_a_bounded_event_and_an_exact_count(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    graph = PostgresArchitectureRepository(sessions)
    hub = Element.create(element_type=E.NODE, name="hub", now=START)
    await graph.add_element(hub, audit=())
    for index in range(AUDIT_LISTED_IDS + 20):
        spoke = Element.create(element_type=E.NODE, name=f"s{index}", now=START)
        await graph.add_element(spoke, audit=())
        await graph.add_relationship(
            Relationship.between(R.ASSOCIATION, hub, spoke, now=START), audit=()
        )
    seen: list[ElementRemoval] = []

    def record(removal: ElementRemoval) -> tuple[()]:
        seen.append(removal)
        return ()

    assert await graph.delete_element(hub.id, audit=record)

    (removal,) = seen
    assert removal.relationship_count == AUDIT_LISTED_IDS + 20
    assert len(removal.relationships) == AUDIT_LISTED_IDS


async def test_paging_the_trail_of_concurrent_creations_loses_and_repeats_nothing(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    service = ArchitectureService(PostgresArchitectureRepository(sessions), clock=a_ticking_clock())
    trail = PostgresAuditRepository(sessions)

    await asyncio.gather(
        *(service.create_element(element_type=E.NODE, name=f"n{i}") for i in range(25))
    )

    seen = []
    cursor = None
    while True:
        page = await trail.page(AuditFilter(limit=7, cursor=cursor))
        seen.extend(page.events)
        cursor = page.next_cursor
        if cursor is None:
            break
    assert len(seen) == len({e.id for e in seen}) == 25
    moments = [(e.at, e.id.int) for e in seen]
    assert moments == sorted(moments, reverse=True)
