"""The parts of the audit store that need no database: row mapping, filter and paging."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from ea.db.models.audit import AuditEventRecord
from ea.domain.audit import (
    AuditAction,
    AuditChannel,
    AuditEvent,
    AuditFilter,
    EntityKind,
    encode_cursor,
)
from ea.domain.errors import ElementNotFoundError, InvalidCursorError
from ea.repositories.audit_store import (
    PostgresAuditRepository,
    _matching,
    event_from_row,
    event_row,
)
from ea.services.architecture import ArchitectureService

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def an_event(**overrides: Any) -> AuditEvent:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "at": T0,
        "actor_sub": "sub",
        "actor_name": "name",
        "action": AuditAction.ELEMENT_UPDATED,
        "entity_kind": EntityKind.ELEMENT,
        "entity_id": uuid4(),
        "before": {"name": "a"},
        "after": {"name": "b"},
        "request_id": "req",
        "channel": AuditChannel.MCP,
        "correlation": "restore:x",
    }
    return AuditEvent(**{**fields, **overrides})


def compiled(criteria: AuditFilter) -> tuple[str, dict[str, object]]:
    statement = select(AuditEventRecord.id).where(*_matching(criteria))
    result = statement.compile(dialect=postgresql.dialect())
    return str(result), dict(result.params)


def test_an_event_survives_the_trip_through_a_row() -> None:
    event = an_event()

    assert event_from_row(AuditEventRecord(**event_row(event))) == event


def test_since_is_inclusive_until_exclusive_and_every_value_is_bound() -> None:
    evil = "x'; DROP TABLE audit_events; --"

    sql, params = compiled(
        AuditFilter(
            entity_kind=EntityKind.ELEMENT,
            entity_id=uuid4(),
            actor=evil,
            action=AuditAction.ELEMENT_CREATED,
            since=T0,
            until=T0 + timedelta(hours=1),
            request_id=evil,
        )
    )

    assert "audit_events.at >=" in sql and "audit_events.at <" in sql
    assert "DROP TABLE" not in sql
    assert evil in params.values()


def test_the_cursor_is_a_keyset_on_the_moment_then_the_id() -> None:
    cursor = encode_cursor(T0, UUID(int=7))

    sql, params = compiled(AuditFilter(cursor=cursor))

    assert "audit_events.at <" in sql and "audit_events.id <" in sql
    assert UUID(int=7) in params.values()


def test_a_forged_cursor_is_refused_before_any_query() -> None:
    with pytest.raises(InvalidCursorError):
        compiled(AuditFilter(cursor="not-a-cursor"))


class FakeSessions:
    """A session factory over fixed rows: `page` asks for `limit + 1` and keeps `limit`."""

    def __init__(self, rows: list[AuditEventRecord]) -> None:
        self.rows = rows
        self.asked: list[str] = []

    @asynccontextmanager
    async def __call__(self) -> Iterator[Any]:  # type: ignore[misc]
        async def scalars(statement: Any) -> Any:
            self.asked.append(str(statement.compile(dialect=postgresql.dialect())))
            return SimpleNamespace(all=lambda: self.rows)

        yield SimpleNamespace(scalars=scalars)


@pytest.mark.asyncio
async def test_a_page_keeps_the_limit_and_hands_back_a_cursor_only_when_there_is_more() -> None:
    events = [an_event(at=T0 - timedelta(seconds=i)) for i in range(3)]
    rows = [AuditEventRecord(**event_row(event)) for event in events]

    more = await PostgresAuditRepository(FakeSessions(rows)).page(AuditFilter(limit=2))  # type: ignore[arg-type]
    last = await PostgresAuditRepository(FakeSessions(rows[:2])).page(AuditFilter(limit=2))  # type: ignore[arg-type]

    assert [e.id for e in more.events] == [events[0].id, events[1].id]
    assert more.next_cursor == encode_cursor(events[1].at, events[1].id)
    assert last.next_cursor is None


@pytest.mark.asyncio
async def test_asking_for_a_relationship_that_is_not_there_is_a_not_found(
    service: ArchitectureService,
) -> None:
    with pytest.raises(ElementNotFoundError):
        await service.get_relationship(uuid4())
