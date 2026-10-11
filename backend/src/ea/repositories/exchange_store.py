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
from uuid import UUID

from sqlalchemy import ColumnElement, Text, cast, func, select
from sqlalchemy.orm import aliased

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.db.unit_of_work import apply_rows, in_unit, reading, shared_transaction
from ea.domain.archimate import RelationshipType
from ea.domain.exchange import SOURCE_ID, ExportedBox, ExportedView
from ea.repositories.architecture_store import (
    element_from_row,
    lock_elements,
    relationship_from_row,
)
from ea.repositories.conflicts import replayable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
    from contextlib import AbstractAsyncContextManager

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.exchange import ExportScope
    from ea.domain.model import Element, Relationship
    from ea.domain.ports import RowRefusal, Transaction


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

    async def views_page(self, offset: int, limit: int) -> tuple[ExportedView, ...]:
        diagrams = (
            await self._session.execute(
                select(DiagramRecord.id, DiagramRecord.name, DiagramRecord.description)
                .order_by(DiagramRecord.name, DiagramRecord.id)
                .offset(offset)
                .limit(limit)
            )
        ).all()
        if not diagrams:
            return ()
        boxes = await self._session.execute(
            select(
                DiagramNodeRecord.diagram_id,
                DiagramNodeRecord.element_id,
                DiagramNodeRecord.x,
                DiagramNodeRecord.y,
                DiagramNodeRecord.width,
                DiagramNodeRecord.height,
            )
            .join(ElementRecord, ElementRecord.id == DiagramNodeRecord.element_id)
            .where(DiagramNodeRecord.diagram_id.in_([d.id for d in diagrams]))
            .order_by(DiagramNodeRecord.diagram_id, _identifier(ElementRecord), ElementRecord.id)
        )
        by_diagram: dict[UUID, list[ExportedBox]] = {d.id: [] for d in diagrams}
        for diagram_id, element_id, x, y, width, height in boxes:
            by_diagram[diagram_id].append(ExportedBox(element_id, x, y, width, height))
        return tuple(
            ExportedView(d.id, d.name, d.description, tuple(by_diagram[d.id])) for d in diagrams
        )


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

    def transaction(self, *, commit: bool) -> AbstractAsyncContextManager[Transaction]:
        return shared_transaction(self._sessions, commit=commit, exclusive=True)

    async def apply_rows[Row](
        self,
        rows: Sequence[Row],
        apply: Callable[[Row], Awaitable[None]],
        *,
        max_refused: int | None = None,
    ) -> list[RowRefusal]:
        if max_refused is None:
            return await apply_rows(rows, apply)
        return await apply_rows(rows, apply, max_refused=max_refused)

    @staticmethod
    def _inside_a_transaction() -> None:
        if not in_unit():
            msg = "this needs the shared transaction of an import: open one with `transaction()`"
            raise RuntimeError(msg)

    async def known_elements(self) -> tuple[Element, ...]:
        self._inside_a_transaction()
        async with reading(self._sessions) as session:
            rows = await session.scalars(select(ElementRecord))
            return tuple(element_from_row(row) for row in rows)

    async def known_relationship_keys(
        self,
    ) -> frozenset[tuple[RelationshipType, UUID, UUID]]:
        self._inside_a_transaction()
        async with reading(self._sessions) as session:
            rows = await session.execute(
                select(
                    RelationshipRecord.relationship_type,
                    RelationshipRecord.source_id,
                    RelationshipRecord.target_id,
                )
            )
            return frozenset(
                (RelationshipType(kind), source, target) for kind, source, target in rows
            )

    @replayable
    async def lock_elements(self, ids: Iterable[UUID]) -> None:
        # A lock is not a write: no event, hence `reading`. A lock that times out (55P03)
        # still condemns the import, which is what `replayable` is for.
        self._inside_a_transaction()
        async with reading(self._sessions) as session:
            await lock_elements(session, ids, key_only=True)
