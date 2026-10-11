"""PostgreSQL side of the import and export of exchange files (docs/adr/0048).

The export reads one `REPEATABLE READ`, read-only snapshot, page after page. It
writes nothing — no audit, no lock — and loads whole rows only through the
graph store's own mappers, so the columns of the review workflow (`revision`,
`approval_state`, …) reach the domain object and stop there: the exporter
takes `properties` alone, which holds only what a person declared.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, Text, cast, func, select
from sqlalchemy.orm import aliased

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.domain.exchange import SOURCE_ID
from ea.repositories.architecture_store import element_from_row, relationship_from_row

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.exchange import ExportScope
    from ea.domain.model import Element, Relationship


def _identifier(record: Any) -> ColumnElement[str]:
    """The sort key standing for a row's identifier: its `source_id`, or `id-` + its UUID.

    Only an *ordering*: the identifier written in the file is decided by the
    service (it must be unique, which a sort key need not be). Computed in SQL
    so the order does not depend on a UUID the importing database invents.
    """
    declared = record.properties.op("->>", return_type=Text)(SOURCE_ID)
    return func.coalesce(declared, func.concat("id-", cast(record.id, Text)))


class _Snapshot:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def elements_page(self, offset: int, limit: int) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord)
            .order_by(ElementRecord.element_type, ElementRecord.name, _identifier(ElementRecord))
            .offset(offset)
            .limit(limit)
        )
        return tuple(element_from_row(row) for row in await self._session.scalars(statement))

    async def relationships_page(self, offset: int, limit: int) -> tuple[Relationship, ...]:
        source, target = aliased(ElementRecord), aliased(ElementRecord)
        statement = (
            select(RelationshipRecord)
            .join(source, source.id == RelationshipRecord.source_id)
            .join(target, target.id == RelationshipRecord.target_id)
            .order_by(
                source.element_type,
                source.name,
                target.element_type,
                target.name,
                RelationshipRecord.relationship_type,
                RelationshipRecord.name,
                _identifier(RelationshipRecord),
                RelationshipRecord.id,
            )
            .offset(offset)
            .limit(limit)
        )
        return tuple(relationship_from_row(row) for row in await self._session.scalars(statement))


class PostgresExchangeRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    @asynccontextmanager
    async def snapshot(self, scope: ExportScope) -> AsyncIterator[_Snapshot]:
        # ponytail: OFFSET/LIMIT inside one snapshot — O(n²/page), fine for a catalogue of
        # tens of thousands of rows; switch to a keyset when it grows. The scope is
        # read from lot 4 on; this lot serves the whole model.
        del scope
        async with self._sessions() as session:
            await session.connection(
                execution_options={
                    "isolation_level": "REPEATABLE READ",
                    "postgresql_readonly": True,
                }
            )
            # A snapshot starts at the first statement, not at `connection()`.
            await session.execute(select(1))
            yield _Snapshot(session)
