"""The PostgreSQL implementation of `ExternalReferenceRepository` (docs/adr/0073).

Like the other stores it takes the session factory and opens one unit of work per call, and a
write and its audit events are one transaction. Like `document_store`, it locks the element a
reference hangs from before writing and puts the locked row to `guard_element_write` with
`WriteKind.EXTERNAL_REFERENCE`: a locked element accepts nothing, an approved one nothing until
an approver reopens it (docs/adr/0065). Unlike a document, it does not `touch` the element: a
pointer to a ticket is not content a reviewer approved, so the revision does not move.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from ea.db.models.architecture import ElementRecord
from ea.db.models.external import ExternalReferenceRecord as Row
from ea.db.unit_of_work import reading, work
from ea.domain.approval import WriteKind, guard_element_write
from ea.domain.audit import events_for
from ea.domain.errors import DuplicateExternalReferenceError, ElementNotFoundError
from ea.domain.external import (
    RESOLVED_KINDS,
    ExternalKind,
    ExternalReference,
    ExternalSnapshot,
    ExternalStatus,
)
from ea.repositories.architecture_store import constraint_of, element_from_row
from ea.repositories.audit_store import write_events
from ea.repositories.conflicts import replayable

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf

#: Named by the frozen naming convention of `db/base.py`.
ONE_CITATION: str = "uq_external_references_element_id_target_external_id"


async def _hold_the_element(session: AsyncSession, element_id: UUID) -> ElementRecord | None:
    """Lock the element a reference hangs from, in the reference's own transaction, before writing.

    `FOR KEY SHARE` is enough to hold the element's review state still (a transition's `UPDATE`
    waits for it); a missing element is left to the foreign key.
    """
    return await session.get(
        ElementRecord, element_id, with_for_update={"key_share": True}, populate_existing=True
    )


def _guard_the_element(element: ElementRecord | None) -> None:
    """`guard_element_write` for a reference write. No reference is written without it:
    `tests/unit/test_review_guard.py` fails on a writer that does not call it."""
    if element is not None:
        guard_element_write(element_from_row(element), WriteKind.EXTERNAL_REFERENCE)


def _to_reference(row: Row) -> ExternalReference:
    try:
        kind = ExternalKind(row.kind)
    except ValueError:  # a kind this version does not know: kept readable, never resolved
        kind = ExternalKind.URL
    try:
        status = ExternalStatus(row.status)
    except ValueError:
        status = ExternalStatus.UNKNOWN
    return ExternalReference(
        id=row.id,
        element_id=row.element_id,
        kind=kind,
        target=row.target,
        external_id=row.external_id,
        url=row.url,
        title=row.title,
        status=status,
        synced_at=row.synced_at,
        created_by=row.created_by,
        created_at=row.created_at,
    )


class PostgresExternalReferenceRepository:
    """Links to objects in other tools, and the title and state cached beside them."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @replayable
    async def add(
        self, reference: ExternalReference, *, audit: AuditOf[ExternalReference]
    ) -> ExternalReference:
        row = Row(
            id=reference.id,
            element_id=reference.element_id,
            kind=reference.kind.value,
            target=reference.target,
            external_id=reference.external_id,
            url=reference.url,
            title=reference.title,
            status=reference.status.value,
            synced_at=reference.synced_at,
            created_by=reference.created_by,
            created_at=reference.created_at,
        )
        events = events_for(audit, reference)
        async with work(self._sessions) as session:
            _guard_the_element(await _hold_the_element(session, reference.element_id))
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as error:
                if constraint_of(error) == ONE_CITATION:
                    msg = "this element already cites that object"
                    raise DuplicateExternalReferenceError(msg) from error
                if constraint_of(error) == "fk_external_references_element_id_elements":
                    msg = f"no element with id {reference.element_id}"
                    raise ElementNotFoundError(msg) from error
                raise
            write_events(session, events)
        return reference

    async def get(self, reference_id: UUID) -> ExternalReference | None:
        async with reading(self._sessions) as session:
            row = await session.get(Row, reference_id)
            return _to_reference(row) if row is not None else None

    async def list_for(self, element_id: UUID) -> tuple[ExternalReference, ...]:
        statement = select(Row).where(Row.element_id == element_id).order_by(Row.created_at, Row.id)
        async with reading(self._sessions) as session:
            return tuple(_to_reference(row) for row in (await session.scalars(statement)))

    async def list_stale(
        self, *, before: datetime, limit: int, targets: Collection[str]
    ) -> tuple[ExternalReference, ...]:
        """Oldest attempt (else sync) first, never-tried first; free links are never stale."""
        statement = (
            select(Row)
            .where(
                Row.kind.in_([kind.value for kind in RESOLVED_KINDS]),
                Row.target.in_(list(targets)),
                (Row.synced_at.is_(None)) | (Row.synced_at < before),
            )
            .order_by(func.coalesce(Row.attempted_at, Row.synced_at).asc().nulls_first(), Row.id)
            .limit(limit)
        )
        async with reading(self._sessions) as session:
            return tuple(_to_reference(row) for row in (await session.scalars(statement)))

    @replayable
    async def delete(self, reference_id: UUID, *, audit: AuditOf[ExternalReference]) -> bool:
        async with work(self._sessions) as session:
            known = await session.get(Row, reference_id)  # only to learn the element
            if known is None:
                return False
            _guard_the_element(await _hold_the_element(session, known.element_id))
            row = await session.get(Row, reference_id, with_for_update=True, populate_existing=True)
            if row is None:
                return False
            events = events_for(audit, _to_reference(row))
            await session.execute(delete(Row).where(Row.id == reference_id))
            write_events(session, events)
        return True

    @replayable
    async def note_attempt(
        self, reference_id: UUID, *, now: datetime, audit: AuditOf[ExternalReference]
    ) -> None:
        """A clock for the queue, not content: no review guard, no revision."""
        async with work(self._sessions) as session:
            row = await session.get(Row, reference_id, with_for_update=True)
            if row is None:
                return
            events = events_for(audit, _to_reference(row))
            row.attempted_at = now
            write_events(session, events)

    @replayable
    async def update_snapshot(
        self,
        reference_id: UUID,
        snapshot: ExternalSnapshot,
        *,
        now: datetime,
        audit: AuditOf[ExternalReference],
    ) -> ExternalReference | None:
        async with work(self._sessions) as session:
            known = await session.get(Row, reference_id)  # only to learn the element
            if known is None:
                return None
            element = await _hold_the_element(session, known.element_id)
            row = await session.get(Row, reference_id, with_for_update=True, populate_existing=True)
            if row is None:
                return None
            if (row.title, row.status) != (snapshot.title, snapshot.status.value):
                _guard_the_element(element)  # a changed cache is a write; a clock is not
            events = events_for(audit, _to_reference(row))
            row.title = snapshot.title
            row.status = snapshot.status.value
            row.synced_at = now
            row.attempted_at = now
            write_events(session, events)
            return _to_reference(row)
