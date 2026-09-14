"""The PostgreSQL implementation of `DiagramRepository` — see docs/adr/0031.

It takes the session factory, like the document store, and opens one unit of
work per call. `replace_layout` is the one call that writes several rows: the
old nodes are deleted and the new ones inserted in the same transaction, so a
reader never sees a diagram half-way between two layouts, and a refused insert
leaves the previous layout in place.

Every value is bound: SQLAlchemy constructs compile to parameters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import Select, delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.errors import DiagramNotFoundError, DuplicateDiagramError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _summaries() -> Select[tuple[DiagramRecord, int]]:
    """Diagrams with their node count, computed by the server rather than stored."""
    count = (
        select(func.count())
        .where(DiagramNodeRecord.diagram_id == DiagramRecord.id)
        .scalar_subquery()
    )
    return select(DiagramRecord, count)


def _to_diagram(row: DiagramRecord, node_count: int) -> Diagram:
    return Diagram(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        node_count=node_count,
    )


def _taken(diagram: Diagram) -> DuplicateDiagramError:
    return DuplicateDiagramError(f"a diagram is already named {diagram.name!r}")


class PostgresDiagramRepository:
    """Saved diagrams, stored beside the graph they are views of."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> tuple[Diagram, ...]:
        statement = _summaries().order_by(DiagramRecord.name, DiagramRecord.id)
        async with self._sessions() as session:
            return tuple(_to_diagram(row, count) for row, count in await session.execute(statement))

    async def get(self, diagram_id: UUID) -> Diagram | None:
        statement = _summaries().where(DiagramRecord.id == diagram_id)
        async with self._sessions() as session:
            found = (await session.execute(statement)).first()
            return _to_diagram(found[0], found[1]) if found is not None else None

    async def nodes_of(self, diagram_id: UUID) -> tuple[DiagramNode, ...]:
        statement = (
            select(DiagramNodeRecord.element_id, DiagramNodeRecord.x, DiagramNodeRecord.y)
            .where(DiagramNodeRecord.diagram_id == diagram_id)
            .order_by(DiagramNodeRecord.element_id)
        )
        async with self._sessions() as session:
            rows = await session.execute(statement)
            return tuple(DiagramNode(element_id=row[0], x=row[1], y=row[2]) for row in rows)

    async def add(self, diagram: Diagram) -> Diagram:
        """The unique constraint refuses a taken name — race-free, unlike a lookup."""
        async with self._sessions.begin() as session:
            session.add(
                DiagramRecord(
                    id=diagram.id,
                    name=diagram.name,
                    description=diagram.description,
                    created_at=diagram.created_at,
                    updated_at=diagram.updated_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                raise _taken(diagram) from error
        return diagram

    async def save(self, diagram: Diagram) -> Diagram:
        async with self._sessions.begin() as session:
            row = await session.get(DiagramRecord, diagram.id)
            if row is None:
                # Deleted between the service reading it and this write.
                msg = f"no diagram with id {diagram.id}"
                raise DiagramNotFoundError(msg)
            row.name = diagram.name
            row.description = diagram.description
            row.updated_at = diagram.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                raise _taken(diagram) from error
        return diagram

    async def delete(self, diagram_id: UUID) -> bool:
        """Its nodes follow by the foreign key."""
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(DiagramRecord).where(DiagramRecord.id == diagram_id)
            )
        return bool(cast("CursorResult[Any]", deleted).rowcount)

    async def replace_layout(
        self, diagram_id: UUID, nodes: Sequence[DiagramNode], *, now: datetime
    ) -> bool:
        async with self._sessions.begin() as session:
            # `FOR UPDATE` queues concurrent saves of one diagram (autosave fires
            # on every drag): unlocked, the second DELETE cannot see the rows the
            # first INSERT committed, and its own INSERT hits the primary key.
            # It also makes a concurrent DELETE of the diagram wait, so the
            # insert never meets a vanished foreign key.
            row = await session.get(DiagramRecord, diagram_id, with_for_update=True)
            if row is None:
                return False
            await session.execute(
                delete(DiagramNodeRecord).where(DiagramNodeRecord.diagram_id == diagram_id)
            )
            session.add_all(
                DiagramNodeRecord(diagram_id=diagram_id, element_id=n.element_id, x=n.x, y=n.y)
                for n in nodes
            )
            row.updated_at = now
        return True
