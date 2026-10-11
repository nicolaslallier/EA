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

from ea.db.models.diagram import (
    DiagramEdgeRecord,
    DiagramNodeRecord,
    DiagramObjectRecord,
    DiagramRecord,
)
from ea.db.unit_of_work import reading, work
from ea.domain.audit import events_for
from ea.domain.diagram_objects import DiagramEdge, DiagramObject, DiagramScene, ObjectKind, Point
from ea.domain.diagrams import MAX_DIAGRAMS, Diagram, DiagramNode
from ea.domain.errors import (
    DiagramLimitError,
    DiagramLiveError,
    DiagramNotFoundError,
    DiagramNotLiveError,
    DiagramRevisionConflictError,
    DuplicateDiagramError,
    PackageNotFoundError,
    SavedQueryNotFoundError,
)
from ea.domain.generated_views import Generation, PackageSource, QuerySource, ViewSource
from ea.repositories.architecture_store import constraint_of
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf


#: Serialises the count-then-insert of `add`: two writers cannot both see the last free place.
_LIBRARY_LOCK = 0x45_41_00_07


def _summaries() -> Select[DiagramRecord, int]:
    """Diagrams with their node count, computed by the server rather than stored."""
    count = (
        select(func.count())
        .where(DiagramNodeRecord.diagram_id == DiagramRecord.id)
        .scalar_subquery()
    )
    return select(DiagramRecord, count)


FOREIGN_KEY_TO_PACKAGE: Final = "fk_diagrams_package_id_packages"


def _source_of(row: DiagramRecord) -> ViewSource | None:
    if row.source_query_id is not None:
        return QuerySource(row.source_query_id)
    if row.source_package_id is not None:
        return PackageSource(row.source_package_id, row.source_recursive)
    return None


def _to_diagram(row: DiagramRecord, node_count: int) -> Diagram:
    return Diagram(
        id=row.id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
        node_count=node_count,
        package_id=row.package_id,
        revision=row.revision,
        generation=Generation(row.generation) if row.generation else None,
        source=_source_of(row),
    )


def _refused(error: IntegrityError, diagram: Diagram) -> Exception:
    """A missing package is a 404; any other refusal here is the unique name."""
    constraint = constraint_of(error)
    if constraint == FOREIGN_KEY_TO_PACKAGE:
        return PackageNotFoundError(f"no package with id {diagram.package_id}")
    if constraint == "fk_diagrams_source_package_id_packages":
        return PackageNotFoundError("the package this view reads no longer exists")
    if constraint == "fk_diagrams_source_query_id_saved_queries":
        return SavedQueryNotFoundError("the saved query this view reads no longer exists")
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
        # One past the cap, so the service can tell "exactly the cap" from "more": a list never
        # loads an unbounded table, and never cuts silently.
        statement = (
            _summaries().order_by(DiagramRecord.name, DiagramRecord.id).limit(MAX_DIAGRAMS + 1)
        )
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
        async with reading(self._sessions) as session:
            return await _nodes(session, diagram_id)

    async def objects_of(self, diagram_id: UUID) -> tuple[DiagramObject, ...]:
        async with reading(self._sessions) as session:
            return await _objects(session, diagram_id)

    async def edges_of(self, diagram_id: UUID) -> tuple[DiagramEdge, ...]:
        async with reading(self._sessions) as session:
            return await _edges(session, diagram_id)

    async def add(self, diagram: Diagram, *, audit: AuditOf[Diagram]) -> Diagram:
        """The unique constraint refuses a taken name — race-free, unlike a lookup."""
        async with work(self._sessions) as session:
            await session.execute(select(func.pg_advisory_xact_lock(_LIBRARY_LOCK)))
            held = await session.scalar(select(func.count()).select_from(DiagramRecord))
            if (held or 0) >= MAX_DIAGRAMS:
                msg = f"the catalogue holds {MAX_DIAGRAMS} diagrams at most"
                raise DiagramLimitError(msg)
            session.add(
                DiagramRecord(
                    id=diagram.id,
                    name=diagram.name,
                    description=diagram.description,
                    created_at=diagram.created_at,
                    updated_at=diagram.updated_at,
                    package_id=diagram.package_id,
                    generation=diagram.generation.value if diagram.generation else None,
                    source_query_id=(
                        diagram.source.query_id if isinstance(diagram.source, QuerySource) else None
                    ),
                    source_package_id=(
                        diagram.source.package_id
                        if isinstance(diagram.source, PackageSource)
                        else None
                    ),
                    source_recursive=(
                        diagram.source.recursive
                        if isinstance(diagram.source, PackageSource)
                        else False
                    ),
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
        scene: DiagramScene,
        *,
        revision: int,
        now: datetime,
        audit: AuditOf[DiagramScene],
    ) -> int | None:
        async with work(self._sessions) as session:
            # `FOR UPDATE` queues concurrent saves of one diagram (autosave fires
            # on every drag) *and* makes the revision comparison race-free: two
            # savers on revision N cannot both read N. It also makes a
            # concurrent DELETE of the diagram wait.
            row = await session.get(DiagramRecord, diagram_id, with_for_update=True)
            if row is None:
                return None
            if row.revision != revision:
                msg = f"the diagram is at revision {row.revision}, not {revision}"
                raise DiagramRevisionConflictError(msg)
            if row.generation == Generation.LIVE.value:
                # Judged under the lock: a freeze or a thaw that raced with this save has
                # already been committed or is waiting behind it.
                msg = "a live view is recomputed at every opening and cannot be edited: freeze it"
                raise DiagramLiveError(msg)
            before = DiagramScene(
                nodes=await _nodes(session, diagram_id),
                objects=await _objects(session, diagram_id),
                edges=await _edges(session, diagram_id),
            )
            events = events_for(audit, before)
            for table in (DiagramNodeRecord, DiagramEdgeRecord, DiagramObjectRecord):
                await session.execute(delete(table).where(table.diagram_id == diagram_id))
            # Parents first: the mappers are related by a foreign key and no
            # `relationship()`, so the unit of work would insert in any order.
            for level in _by_depth(scene.objects):
                session.add_all(
                    DiagramObjectRecord(
                        id=o.id,
                        diagram_id=diagram_id,
                        kind=o.kind.value,
                        parent_id=o.parent_id,
                        x=o.x,
                        y=o.y,
                        width=o.width,
                        height=o.height,
                        text=o.text,
                        colour=o.colour,
                        z=o.z,
                        anchor_element_id=o.anchor_element_id,
                    )
                    for o in level
                )
                await session.flush()
            session.add_all(
                DiagramNodeRecord(
                    diagram_id=diagram_id,
                    element_id=n.element_id,
                    x=n.x,
                    y=n.y,
                    width=n.width,
                    height=n.height,
                    parent_object_id=n.parent_object_id,
                    z=n.z,
                )
                for n in scene.nodes
            )
            session.add_all(
                DiagramEdgeRecord(
                    diagram_id=diagram_id,
                    relationship_id=e.relationship_id,
                    waypoints=[[p.x, p.y] for p in e.waypoints],
                )
                for e in scene.edges
            )
            row.revision = revision + 1
            row.updated_at = now
            await session.flush()
            write_events(session, events)
            return row.revision

    async def freeze(
        self,
        diagram_id: UUID,
        nodes: Sequence[DiagramNode],
        *,
        revision: int,
        now: datetime,
        audit: AuditOf[Diagram],
    ) -> int | None:
        async with work(self._sessions) as session:
            row = await session.get(DiagramRecord, diagram_id, with_for_update=True)
            if row is None:
                return None
            if row.revision != revision:
                msg = f"the diagram is at revision {row.revision}, not {revision}"
                raise DiagramRevisionConflictError(msg)
            if row.generation != Generation.LIVE.value:
                msg = "this diagram is not a live view: there is nothing to freeze"
                raise DiagramNotLiveError(msg)
            events = events_for(audit, _to_diagram(row, 0))
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
                    parent_object_id=None,
                    z=n.z,
                )
                for n in nodes
            )
            row.generation = Generation.FROZEN.value
            row.revision += 1
            row.updated_at = now
            await session.flush()
            write_events(session, events)
            return row.revision

    async def unfreeze(
        self, diagram_id: UUID, *, revision: int, now: datetime, audit: AuditOf[Diagram]
    ) -> int | None:
        async with work(self._sessions) as session:
            row = await session.get(DiagramRecord, diagram_id, with_for_update=True)
            if row is None:
                return None
            if row.revision != revision:
                msg = f"the diagram is at revision {row.revision}, not {revision}"
                raise DiagramRevisionConflictError(msg)
            if row.generation != Generation.FROZEN.value:
                msg = "this diagram is not a frozen view: there is nothing to thaw"
                raise DiagramLiveError(msg)
            events = events_for(audit, _to_diagram(row, 0))
            for table in (DiagramNodeRecord, DiagramEdgeRecord, DiagramObjectRecord):
                await session.execute(delete(table).where(table.diagram_id == diagram_id))
            row.generation = Generation.LIVE.value
            row.revision += 1
            row.updated_at = now
            await session.flush()
            write_events(session, events)
            return row.revision

    async def names_using_source(
        self, *, query_id: UUID | None = None, package_id: UUID | None = None
    ) -> tuple[str, ...]:
        statement = select(DiagramRecord.name).order_by(DiagramRecord.name).limit(100)
        if query_id is not None:
            statement = statement.where(DiagramRecord.source_query_id == query_id)
        elif package_id is not None:
            statement = statement.where(DiagramRecord.source_package_id == package_id)
        else:
            return ()
        async with reading(self._sessions) as session:
            return tuple(await session.scalars(statement))


async def _nodes(session: AsyncSession, diagram_id: UUID) -> tuple[DiagramNode, ...]:
    statement = (
        select(DiagramNodeRecord)
        .where(DiagramNodeRecord.diagram_id == diagram_id)
        .order_by(DiagramNodeRecord.element_id)
    )
    return tuple(
        DiagramNode(
            element_id=row.element_id,
            x=row.x,
            y=row.y,
            width=row.width,
            height=row.height,
            parent_object_id=row.parent_object_id,
            z=row.z,
        )
        for row in await session.scalars(statement)
    )


async def _objects(session: AsyncSession, diagram_id: UUID) -> tuple[DiagramObject, ...]:
    statement = (
        select(DiagramObjectRecord)
        .where(DiagramObjectRecord.diagram_id == diagram_id)
        .order_by(DiagramObjectRecord.z, DiagramObjectRecord.id)
    )
    return tuple(
        DiagramObject(
            id=row.id,
            kind=ObjectKind(row.kind),
            x=row.x,
            y=row.y,
            width=row.width,
            height=row.height,
            text=row.text,
            colour=row.colour,
            z=row.z,
            parent_id=row.parent_id,
            anchor_element_id=row.anchor_element_id,
        )
        for row in await session.scalars(statement)
    )


async def _edges(session: AsyncSession, diagram_id: UUID) -> tuple[DiagramEdge, ...]:
    statement = (
        select(DiagramEdgeRecord)
        .where(DiagramEdgeRecord.diagram_id == diagram_id)
        .order_by(DiagramEdgeRecord.relationship_id)
    )
    return tuple(
        DiagramEdge(
            relationship_id=row.relationship_id,
            waypoints=tuple(Point(x=p[0], y=p[1]) for p in row.waypoints),
        )
        for row in await session.scalars(statement)
    )


def _by_depth(objects: Sequence[DiagramObject]) -> list[list[DiagramObject]]:
    """The objects grouped by how many groups lie above them, outermost first.

    `check_scene` has already refused a loop, so every chain ends.
    """
    by_id = {o.id: o for o in objects}
    levels: dict[int, list[DiagramObject]] = {}
    for item in objects:
        depth, current = 0, item
        while current.parent_id is not None and current.parent_id in by_id:
            depth += 1
            current = by_id[current.parent_id]
        levels.setdefault(depth, []).append(item)
    return [levels[depth] for depth in sorted(levels)]
