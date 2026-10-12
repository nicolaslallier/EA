"""The PostgreSQL side of the audit trail — see docs/adr/0046.

Two things live here and they are deliberately not one class. `write_events` is
a function over a session that *another* repository already holds open: an
event is inserted in the transaction of the write it describes, so it is there
if the write is committed and gone if it is not. `PostgresAuditRepository` is
the read side and opens its own units of work, like every other store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, and_, or_, select

from ea.db.models.audit import AuditEventRecord
from ea.domain.audit import (
    AuditAction,
    AuditChannel,
    AuditEvent,
    AuditFilter,
    AuditPage,
    EntityKind,
    decode_cursor,
    encode_cursor,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def event_row(event: AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "at": event.at,
        "actor_sub": event.actor_sub,
        "actor_name": event.actor_name,
        "action": event.action.value,
        "entity_kind": event.entity_kind.value,
        "entity_id": event.entity_id,
        "before": dict(event.before) if event.before is not None else None,
        "after": dict(event.after) if event.after is not None else None,
        "request_id": event.request_id,
        "channel": event.channel.value,
        "correlation": event.correlation,
    }


def event_from_row(row: AuditEventRecord) -> AuditEvent:
    return AuditEvent(
        id=row.id,
        at=row.at,
        actor_sub=row.actor_sub,
        actor_name=row.actor_name,
        action=AuditAction(row.action),
        entity_kind=EntityKind(row.entity_kind),
        entity_id=row.entity_id,
        before=row.before,
        after=row.after,
        request_id=row.request_id,
        channel=AuditChannel(row.channel),
        correlation=row.correlation,
    )


def write_events(session: AsyncSession, events: Sequence[AuditEvent]) -> None:
    """Add the events to the unit of work the caller holds open — flushed at its commit.

    No flush here: the caller has already flushed the write itself (that is where
    a refusal surfaces), so an event is only ever added behind a write that held.
    """
    for event in events:
        # Fail closed: an event nobody can be held to is worse than none, and the
        # write behind it must not commit. `AuditTrail` cannot build one (it asks
        # `require_caller()`); this is the last door, for any other route.
        for field in ("actor_sub", "actor_name", "request_id"):
            if not getattr(event, field):
                msg = f"an audit event needs a non-empty {field}"
                raise ValueError(msg)
    session.add_all([AuditEventRecord(**event_row(event)) for event in events])


def _matching(criteria: AuditFilter) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if criteria.entity_kind is not None:
        clauses.append(AuditEventRecord.entity_kind == criteria.entity_kind.value)
    if criteria.entity_id is not None:
        clauses.append(AuditEventRecord.entity_id == criteria.entity_id)
    if criteria.actor is not None:
        clauses.append(
            or_(
                AuditEventRecord.actor_sub == criteria.actor,
                AuditEventRecord.actor_name == criteria.actor,
            )
        )
    if criteria.action is not None:
        clauses.append(AuditEventRecord.action == criteria.action.value)
    if criteria.since is not None:
        clauses.append(AuditEventRecord.at >= criteria.since)
    if criteria.until is not None:
        clauses.append(AuditEventRecord.at < criteria.until)
    if criteria.request_id is not None:
        clauses.append(AuditEventRecord.request_id == criteria.request_id)
    if criteria.cursor is not None:
        at, event_id = decode_cursor(criteria.cursor)
        clauses.append(
            or_(
                AuditEventRecord.at < at,
                and_(AuditEventRecord.at == at, AuditEventRecord.id < event_id),
            )
        )
    return clauses


def to_database(
    sessions: async_sessionmaker[AsyncSession],
) -> Callable[[Sequence[AuditEvent]], Awaitable[None]]:
    """A sink that writes events in a transaction of their own (the refusal of a frozen write).

    The refused write rolled back, so its trace cannot ride in its unit.
    """

    async def write(events: Sequence[AuditEvent]) -> None:
        async with sessions.begin() as session:
            write_events(session, events)

    return write


class PostgresAuditRepository:
    """`AuditRepository` over the append-only table."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def page(self, criteria: AuditFilter) -> AuditPage:
        statement = (
            select(AuditEventRecord)
            .where(*_matching(criteria))
            .order_by(AuditEventRecord.at.desc(), AuditEventRecord.id.desc())
            .limit(criteria.limit + 1)
        )
        async with self._sessions() as session:
            rows = list((await session.scalars(statement)).all())
        shown = rows[: criteria.limit]
        events = tuple(event_from_row(row) for row in shown)
        more = len(rows) > criteria.limit
        return AuditPage(
            events=events,
            next_cursor=encode_cursor(shown[-1].at, shown[-1].id) if more else None,
        )
