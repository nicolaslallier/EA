"""The PostgreSQL implementation of `SavedQueryRepository`.

A listing selects the plain columns and never touches `query`: one stale row
must not make the others unreadable. `get` reads the JSON back through
`from_json`, which refuses what the code can no longer vouch for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from ea.db.models.saved_query import SavedQueryRecord
from ea.db.unit_of_work import reading, work
from ea.domain.audit import events_for, saved_query_record_view
from ea.domain.errors import DuplicateSavedQueryError, SavedQueryNotFoundError, SourceInUseError
from ea.domain.query import from_json, to_json
from ea.domain.saved_queries import SavedQuery, SavedQuerySummary
from ea.repositories.architecture_store import constraint_of
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from collections.abc import Mapping
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf

NAME_TAKEN: Final = "uq_saved_queries_name"


def _record_view(row: SavedQueryRecord) -> dict[str, Any]:
    """The row as it is stored: readable even when `from_json` would refuse it."""
    return saved_query_record_view(
        query_id=row.id,
        name=row.name,
        description=row.description,
        query=row.query,
        created_by=row.created_by,
    )


def _taken(saved: SavedQuery) -> DuplicateSavedQueryError:
    return DuplicateSavedQueryError(f"a saved query is already named {saved.name!r}")


class PostgresSavedQueryRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> tuple[SavedQuerySummary, ...]:
        statement = select(
            SavedQueryRecord.id,
            SavedQueryRecord.name,
            SavedQueryRecord.description,
            SavedQueryRecord.created_by,
            SavedQueryRecord.created_at,
            SavedQueryRecord.updated_at,
        ).order_by(func.lower(SavedQueryRecord.name), SavedQueryRecord.id)
        async with reading(self._sessions) as session:
            return tuple(
                SavedQuerySummary(*row) for row in (await session.execute(statement)).tuples()
            )

    async def get(self, query_id: UUID) -> SavedQuery | None:
        async with reading(self._sessions) as session:
            row = await session.get(SavedQueryRecord, query_id)
            if row is None:
                return None
            return SavedQuery(
                id=row.id,
                name=row.name,
                description=row.description,
                query=from_json(row.query),
                created_by=row.created_by,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def add(self, saved: SavedQuery, *, audit: AuditOf[SavedQuery]) -> SavedQuery:
        async with work(self._sessions) as session:
            session.add(
                SavedQueryRecord(
                    id=saved.id,
                    name=saved.name,
                    description=saved.description,
                    query=to_json(saved.query),
                    created_by=saved.created_by,
                    created_at=saved.created_at,
                    updated_at=saved.updated_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                if constraint_of(error) != NAME_TAKEN:
                    raise
                raise _taken(saved) from error
            write_events(session, events_for(audit, saved))
        return saved

    async def save(self, saved: SavedQuery, *, audit: AuditOf[Mapping[str, Any]]) -> SavedQuery:
        async with work(self._sessions) as session:
            row = await session.get(SavedQueryRecord, saved.id, with_for_update=True)
            if row is None:
                msg = f"no saved query with id {saved.id}"
                raise SavedQueryNotFoundError(msg)
            before = _record_view(row)
            row.name = saved.name
            row.description = saved.description
            row.query = to_json(saved.query)
            row.updated_at = saved.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                if constraint_of(error) != NAME_TAKEN:
                    raise
                raise _taken(saved) from error
            write_events(session, events_for(audit, before))
        return saved

    async def delete(self, query_id: UUID, *, audit: AuditOf[Mapping[str, Any]]) -> bool:
        async with work(self._sessions) as session:
            row = await session.get(SavedQueryRecord, query_id, with_for_update=True)
            if row is None:
                return False
            events = events_for(audit, _record_view(row))
            try:
                await session.execute(
                    delete(SavedQueryRecord).where(SavedQueryRecord.id == query_id)
                )
            except IntegrityError as error:
                # RESTRICT: a generated view reads this query (docs/adr/0065).
                if constraint_of(error) == "fk_diagrams_source_query_id_saved_queries":
                    msg = "a generated view reads this query: delete the view first"
                    raise SourceInUseError(msg) from error
                raise
            write_events(session, events)
        return True
