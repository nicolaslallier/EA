"""Writing and reading the trail against a real PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.db.postgres import create_session_factory
from ea.domain.audit import (
    AuditAction,
    AuditChannel,
    AuditEvent,
    AuditFilter,
    EntityKind,
)
from ea.repositories.audit_store import PostgresAuditRepository, write_events

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_event(**overrides: object) -> AuditEvent:
    fields: dict[str, object] = {
        "id": uuid4(),
        "at": T0,
        "actor_sub": "editor-1",
        "actor_name": "editor",
        "action": AuditAction.ELEMENT_CREATED,
        "entity_kind": EntityKind.ELEMENT,
        "entity_id": uuid4(),
        "before": None,
        "after": {"name": "Billing"},
        "request_id": "req-1",
        "channel": AuditChannel.HTTP,
    }
    return AuditEvent(**{**fields, **overrides})  # type: ignore[arg-type]


async def test_an_event_round_trips_and_a_rolled_back_transaction_leaves_none(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresAuditRepository(sessions)
    kept, lost = an_event(), an_event()

    async with sessions.begin() as session:
        write_events(session, [kept])
    with pytest.raises(RuntimeError):
        async with sessions.begin() as session:
            write_events(session, [lost])
            raise RuntimeError

    page = await store.page(AuditFilter())
    assert [event.id for event in page.events] == [kept.id]
    assert page.events[0] == kept


async def test_pagination_has_no_hole_and_no_duplicate_even_when_moments_are_equal(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresAuditRepository(sessions)
    events = [an_event(at=T0 + timedelta(seconds=index // 3)) for index in range(7)]
    async with sessions.begin() as session:
        write_events(session, events)

    seen: list[object] = []
    cursor = None
    while True:
        page = await store.page(AuditFilter(limit=2, cursor=cursor))
        seen.extend(event.id for event in page.events)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert sorted(seen, key=str) == sorted((e.id for e in events), key=str)
    assert len(seen) == len(set(seen)) == 7


async def test_the_filters_combine_and_since_is_inclusive_until_exclusive(
    engine_at_head: AsyncEngine,
) -> None:
    sessions = create_session_factory(engine_at_head)
    store = PostgresAuditRepository(sessions)
    subject = uuid4()
    early = an_event(at=T0, entity_id=subject, actor_name="alice", actor_sub="sub-a")
    late = an_event(
        at=T0 + timedelta(hours=1),
        entity_id=subject,
        action=AuditAction.ELEMENT_UPDATED,
        request_id="req-2",
    )
    other = an_event(entity_id=uuid4())
    async with sessions.begin() as session:
        write_events(session, [early, late, other])

    by_entity = await store.page(AuditFilter(entity_kind=EntityKind.ELEMENT, entity_id=subject))
    by_actor = await store.page(AuditFilter(actor="alice"))
    by_sub = await store.page(AuditFilter(actor="sub-a"))
    by_action = await store.page(AuditFilter(action=AuditAction.ELEMENT_UPDATED))
    by_request = await store.page(AuditFilter(request_id="req-2"))
    window = await store.page(
        AuditFilter(entity_id=subject, since=T0, until=T0 + timedelta(hours=1))
    )

    assert [e.id for e in by_entity.events] == [late.id, early.id]  # newest first
    assert [e.id for e in by_actor.events] == [early.id]
    assert [e.id for e in by_sub.events] == [early.id]
    assert [e.id for e in by_action.events] == [late.id]
    assert [e.id for e in by_request.events] == [late.id]
    assert [e.id for e in window.events] == [early.id]
