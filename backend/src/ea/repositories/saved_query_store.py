"""The PostgreSQL implementation of `SavedQueryRepository`.

A listing selects the plain columns and never touches `query`: one stale row
must not make the others unreadable. `get` reads the JSON back through
`from_json`, which refuses what the code can no longer vouch for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.saved_query import SavedQueryRecord
from ea.domain.errors import DuplicateSavedQueryError, SavedQueryNotFoundError
from ea.domain.query import from_json, to_json
from ea.domain.saved_queries import SavedQuery, SavedQuerySummary
from ea.repositories.architecture_store import constraint_of

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

NAME_TAKEN: Final = "uq_saved_queries_name"


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
        async with self._sessions() as session:
            return tuple(
                SavedQuerySummary(*row) for row in (await session.execute(statement)).tuples()
            )

    async def get(self, query_id: UUID) -> SavedQuery | None:
        async with self._sessions() as session:
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

    async def add(self, saved: SavedQuery) -> SavedQuery:
        async with self._sessions.begin() as session:
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
        return saved

    async def save(self, saved: SavedQuery) -> SavedQuery:
        async with self._sessions.begin() as session:
            row = await session.get(SavedQueryRecord, saved.id)
            if row is None:
                msg = f"no saved query with id {saved.id}"
                raise SavedQueryNotFoundError(msg)
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
        return saved

    async def delete(self, query_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(SavedQueryRecord).where(SavedQueryRecord.id == query_id)
            )
        return bool(cast("CursorResult[Any]", deleted).rowcount)
