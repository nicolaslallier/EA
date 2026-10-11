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

from sqlalchemy import ColumnElement, Text, cast, exists, func, select
from sqlalchemy.exc import DBAPIError, StatementError
from sqlalchemy.orm import aliased

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.db.models.package import PackageRecord
from ea.db.unit_of_work import apply_rows, in_unit, reading, shared_transaction
from ea.domain.archimate import RelationshipType
from ea.domain.errors import UnknownScopeError
from ea.domain.exchange import SOURCE_ID, ExportedBox, ExportedView, PackageNode
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import (
    _matching,
    element_from_row,
    lock_elements,
    relationship_from_row,
)
from ea.repositories.conflicts import replayable
from ea.repositories.package_store import TREE_LOCK

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


def _scope_clauses(scope: ExportScope) -> list[ColumnElement[bool]]:
    """What an element must satisfy to be in the export: the package subtree resolved by the
    service, or the saved query's filter through the one SQL path every listing uses."""
    if scope.package_ids is not None:
        return [ElementRecord.package_id.in_(scope.package_ids)]
    if scope.query is not None:
        return _matching(ElementFilter.from_query(scope.query))
    return []


def _leaving_the_scope(inside: Any) -> ColumnElement[bool]:
    """A diagram with a box on an element that is not in scope."""
    return exists().where(
        DiagramNodeRecord.diagram_id == DiagramRecord.id,
        DiagramNodeRecord.element_id.not_in(inside),
    )


class _Snapshot:
    def __init__(self, session: AsyncSession, scope: ExportScope) -> None:
        self._session = session
        self._scope = scope
        self._clauses = _scope_clauses(scope)

    def _inside(self) -> Any:
        return select(ElementRecord.id).where(*self._clauses)

    async def elements_page(self, offset: int, limit: int) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord)
            .where(*self._clauses)
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
            # A link is exported only if both of its ends are: never an end outside the scope.
            .where(*self._ends_inside())
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

    async def skipped_views(self) -> int:
        if not self._clauses:
            return 0
        statement = (
            select(func.count())
            .select_from(DiagramRecord)
            .where(_leaving_the_scope(self._inside()))
        )
        return int((await self._session.execute(statement)).scalar_one())

    async def check_scope(self) -> None:
        try:
            await self._session.execute(select(ElementRecord.id).where(*self._clauses).limit(1))
        except (DBAPIError, StatementError):
            # Never the database's words: the filter is a person's, and so is what it quotes.
            msg = "the saved query cannot be run against the catalogue"
            raise UnknownScopeError(msg) from None

    def _ends_inside(self) -> list[ColumnElement[bool]]:
        if not self._clauses:
            return []
        inside = self._inside()
        return [
            RelationshipRecord.source_id.in_(inside),
            RelationshipRecord.target_id.in_(inside),
        ]

    async def packages(self) -> tuple[PackageNode, ...]:
        statement = select(PackageRecord.id, PackageRecord.parent_id, PackageRecord.name)
        if self._scope.package_ids is not None:
            statement = statement.where(PackageRecord.id.in_(self._scope.package_ids))
        rows = await self._session.execute(statement)
        return tuple(PackageNode(*row) for row in rows)

    async def views_page(self, offset: int, limit: int) -> tuple[ExportedView, ...]:
        whole = [~_leaving_the_scope(self._inside())] if self._clauses else []
        diagrams = (
            await self._session.execute(
                select(DiagramRecord.id, DiagramRecord.name, DiagramRecord.description)
                .where(*whole)
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
        async with self._sessions() as session:
            await session.connection(
                execution_options={
                    "isolation_level": "REPEATABLE READ",
                    "postgresql_readonly": True,
                }
            )
            # A snapshot starts at the first statement, not at `connection()`.
            await session.execute(select(1))
            yield _Snapshot(session, scope)

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
    async def lock_package_tree(self) -> None:
        # The lock every write to the tree takes first (docs/adr/0042), taken before the
        # element locks so that an import and a package write meet in the same order.
        self._inside_a_transaction()
        async with reading(self._sessions) as session:
            await session.execute(select(func.pg_advisory_xact_lock(TREE_LOCK)))

    @replayable
    async def lock_elements(self, ids: Iterable[UUID]) -> None:
        # A lock is not a write: no event, hence `reading`. A lock that times out (55P03)
        # still condemns the import, which is what `replayable` is for.
        self._inside_a_transaction()
        async with reading(self._sessions) as session:
            await lock_elements(session, ids, key_only=True)
