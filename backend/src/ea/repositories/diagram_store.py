"""The PostgreSQL implementation of `DiagramRepository` — see docs/adr/0031.

It takes the session factory, like the document store, and opens one unit of
work per call. `replace_layout` is the one call that writes several rows: the
old nodes are deleted and the new ones inserted in the same transaction, so a
reader never sees a diagram half-way between two layouts, and a refused insert
leaves the previous layout in place.

Every value is bound: SQLAlchemy constructs compile to parameters.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError

from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.db.unit_of_work import reading, work
from ea.domain.audit import events_for
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.errors import DiagramNotFoundError, DuplicateDiagramError, PackageNotFoundError
from ea.repositories.architecture_store import constraint_of
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf


def _summaries() -> Select[tuple[DiagramRecord, int]]:
    """Diagrams with their node count, computed by the server rather than stored."""
    count = (
        select(func.count())
        .where(DiagramNodeRecord.diagram_id == DiagramRecord.id)
        .scalar_subquery()
    )
    return select(DiagramRecord, count)


FOREIGN_KEY_TO_PACKAGE: Final = "fk_diagrams_package_id_packages"


def _to_diagram(row: DiagramRecord, node_count: int) -> Diagram:
    return Diagram(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        node_count=node_count,
        package_id=row.package_id,
    )


def _refused(error: IntegrityError, diagram: Diagram) -> Exception:
    """A missing package is a 404; any other refusal here is the unique name."""
    if constraint_of(error) == FOREIGN_KEY_TO_PACKAGE:
        return PackageNotFoundError(f"no package with id {diagram.package_id}")
    return _taken(diagram)


def _taken(diagram: Diagram) -> DuplicateDiagramError:
    return DuplicateDiagramError(f"a diagram is already named {diagram.name!r}")


class PostgresDiagramRepository:
    """Saved diagrams, stored beside the graph they are views of."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(
        self, *, package_ids: Collection[UUID] | None = None, root_only: bool = False
    ) -> tuple[Diagram, ...]:
        statement = _summaries().order_by(DiagramRecord.name, DiagramRecord.id)
        if root_only:
            statement = statement.where(DiagramRecord.package_id.is_(None))
        elif package_ids is not None:
            statement = statement.where(DiagramRecord.package_id.in_(package_ids))
        async with reading(self._sessions) as session:
            return tuple(_to_diagram(row, count) for row, count in await session.execute(statement))

    async def get(self, diagram_id: UUID) -> Diagram | None:
        statement = _summaries().where(DiagramRecord.id == diagram_id)
        async with reading(self._sessions) as session:
            found = (await session.execute(statement)).first()
            return _to_diagram(found[0], found[1]) if found is not None else None

    async def nodes_of(self, diagram_id: UUID) -> tuple[DiagramNode, ...]:
        statement = (
            select(
                DiagramNodeRecord.element_id,
                DiagramNodeRecord.x,
                DiagramNodeRecord.y,
                DiagramNodeRecord.width,
                DiagramNodeRecord.height,
            )
            .where(DiagramNodeRecord.diagram_id == diagram_id)
            .order_by(DiagramNodeRecord.element_id)
        )
        async with reading(self._sessions) as session:
            rows = await session.execute(statement)
            return tuple(
                DiagramNode(element_id=row[0], x=row[1], y=row[2], width=row[3], height=row[4])
                for row in rows
            )

    async def add(self, diagram: Diagram, *, audit: AuditOf[Diagram]) -> Diagram:
        """The unique constraint refuses a taken name — race-free, unlike a lookup."""
        async with work(self._sessions) as session:
            session.add(
                DiagramRecord(
                    id=diagram.id,
                    name=diagram.name,
                    description=diagram.description,
                    created_at=diagram.created_at,
                    updated_at=diagram.updated_at,
                    package_id=diagram.package_id,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                raise _refused(error, diagram) from error
            write_events(session, events_for(audit, diagram))
        return diagram

    async def save(self, diagram: Diagram, *, audit: AuditOf[Diagram]) -> Diagram:
        async with work(self._sessions) as session:
            row = await session.get(DiagramRecord, diagram.id, with_for_update=True)
            if row is None:
                # Deleted between the service reading it and this write.
                msg = f"no diagram with id {diagram.id}"
                raise DiagramNotFoundError(msg)
            before = _to_diagram(row, 0)
            row.name = diagram.name
            row.description = diagram.description
            row.package_id = diagram.package_id
            row.updated_at = diagram.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                raise _refused(error, diagram) from error
            write_events(session, events_for(audit, before))
        return diagram

    async def delete(self, diagram_id: UUID, *, audit: AuditOf[Diagram]) -> bool:
        """Its nodes follow by the foreign key."""
        async with work(self._sessions) as session:
            row = await session.get(DiagramRecord, diagram_id, with_for_update=True)
            if row is None:
                return False
            events = events_for(audit, _to_diagram(row, 0))
            await session.execute(delete(DiagramRecord).where(DiagramRecord.id == diagram_id))
            write_events(session, events)
        return True

    async def replace_layout(
        self,
        diagram_id: UUID,
        nodes: Sequence[DiagramNode],
        *,
        now: datetime,
        audit: AuditOf[tuple[DiagramNode, ...]],
    ) -> bool:
        async with work(self._sessions) as session:
            # `FOR UPDATE` queues concurrent saves of one diagram (autosave fires
            # on every drag): unlocked, the second DELETE cannot see the rows the
            # first INSERT committed, and its own INSERT hits the primary key.
            # It also makes a concurrent DELETE of the diagram wait, so the
            # insert never meets a vanished foreign key.
            row = await session.get(DiagramRecord, diagram_id, with_for_update=True)
            if row is None:
                return False
            kept = await session.execute(
                select(
                    DiagramNodeRecord.element_id,
                    DiagramNodeRecord.x,
                    DiagramNodeRecord.y,
                    DiagramNodeRecord.width,
                    DiagramNodeRecord.height,
                ).where(DiagramNodeRecord.diagram_id == diagram_id)
            )
            before = tuple(
                DiagramNode(element_id=r[0], x=r[1], y=r[2], width=r[3], height=r[4]) for r in kept
            )
            await session.execute(
                delete(DiagramNodeRecord).where(DiagramNodeRecord.diagram_id == diagram_id)
            )
            session.add_all(
                DiagramNodeRecord(
                    diagram_id=diagram_id,
                    element_id=n.element_id,
                    x=n.x,
                    y=n.y,
                    width=n.width,
                    height=n.height,
                )
                for n in nodes
            )
            row.updated_at = now
            write_events(session, events_for(audit, before))
        return True
