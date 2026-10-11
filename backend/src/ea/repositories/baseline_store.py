"""The PostgreSQL implementation of `BaselineRepository` — see docs/adr/0045.

A capture is three `INSERT … SELECT` in one REPEATABLE READ transaction: the copy
never passes through Python, every statement sees the same instant of the model
(a link is never copied without its element), and a baseline is either complete
or absent. Every read that feeds a decision runs at one instant too.

Nothing here references the live model by a foreign key: a baseline outlives the
elements, packages and diagrams it remembers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import ARRAY, Text, Uuid, any_, delete, func, literal, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import insert

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.db.models.baseline import (
    BaselineBoxRecord,
    BaselineElementRecord,
    BaselineRecord,
    BaselineRelationshipRecord,
)
from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.db.models.document import ElementDocument
from ea.db.models.package import PackageRecord
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.audit import events_for
from ea.domain.baselines import (
    Baseline,
    BaselineLimits,
    BoxSnapshot,
    ModelSnapshot,
    plan_restore,
)
from ea.domain.errors import BaselineNotFoundError, BaselineTooLargeError, DuplicateBaselineError
from ea.domain.model import Element, Relationship
from ea.domain.packages import PATH_SEPARATOR
from ea.repositories.architecture_store import (
    constraint_of,
    element_from_row,
    relationship_from_row,
)
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.sql.selectable import CTE

    from ea.domain.audit import AuditOf
    from ea.domain.baselines import RestorePlan

NAME_TAKEN: Final = "uq_baselines_name"
#: Per row, on top of its text: so a model of many small rows is bounded too.
ROW_BYTES: Final = 64

_ELEMENT_COLUMNS = (
    "id",
    "element_type",
    "layer",
    "aspect",
    "name",
    "description",
    "documentation",
    "properties",
    "package_id",
    "created_at",
    "updated_at",
)
_RELATIONSHIP_COLUMNS = (
    "id",
    "relationship_type",
    "source_id",
    "target_id",
    "source_type",
    "target_type",
    "name",
    "access_type",
    "directed",
    "properties",
    "created_at",
)
_BOX_COLUMNS = ("diagram_id", "element_id", "x", "y", "width", "height")

#: Which elements are protected from a restoration, read in the transaction that
#: applies it. Nothing protects an element until the approval lot (docs/adr/0045).
ProtectedIds = Callable[["AsyncSession"], Awaitable[frozenset["UUID"]]]


async def no_element_is_protected(session: AsyncSession) -> frozenset[UUID]:
    return frozenset()


def _paths() -> CTE:
    """`{package id: 'A / B'}` for the whole tree, by a recursive CTE."""
    roots = (
        select(PackageRecord.id.label("id"), PackageRecord.name.label("path"))
        .where(PackageRecord.parent_id.is_(None))
        .cte("package_paths", recursive=True)
    )
    below = select(
        PackageRecord.id, roots.c.path + literal(PATH_SEPARATOR, Text) + PackageRecord.name
    ).join(roots, PackageRecord.parent_id == roots.c.id)
    return roots.union_all(below)


def _baseline(row: BaselineRecord, elements: int, relationships: int, boxes: int) -> Baseline:
    return Baseline(
        id=row.id,
        name=row.name,
        description=row.description,
        created_by_sub=row.created_by_sub,
        created_by=row.created_by,
        created_at=row.created_at,
        element_count=elements,
        relationship_count=relationships,
        box_count=boxes,
    )


def _counted() -> Any:
    """Baselines with the counts of their copy, computed by the server rather than stored."""

    def count(model: Any) -> Any:
        return select(func.count()).where(model.baseline_id == BaselineRecord.id).scalar_subquery()

    return select(
        BaselineRecord,
        count(BaselineElementRecord),
        count(BaselineRelationshipRecord),
        count(BaselineBoxRecord),
    )


def _copied_element(row: BaselineElementRecord) -> Element:
    return Element(
        id=row.id,
        element_type=ElementType(row.element_type),
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        description=row.description,
        documentation=row.documentation,
        properties=dict(row.properties),
        package_id=row.package_id,
    )


def _copied_relationship(row: BaselineRelationshipRecord) -> Relationship:
    return Relationship(
        id=row.id,
        relationship_type=RelationshipType(row.relationship_type),
        source_id=row.source_id,
        target_id=row.target_id,
        source_type=ElementType(row.source_type),
        target_type=ElementType(row.target_type),
        created_at=row.created_at,
        name=row.name,
        access_type=AccessType(row.access_type) if row.access_type else None,
        directed=row.directed,
        properties=dict(row.properties),
    )


def _box(row: DiagramNodeRecord | BaselineBoxRecord) -> BoxSnapshot:
    return BoxSnapshot(row.diagram_id, row.element_id, row.x, row.y, row.width, row.height)


def _rows_affected(result: object) -> int:
    return cast("CursorResult[Any]", result).rowcount


async def _snapshot_of(session: AsyncSession, baseline_id: UUID) -> ModelSnapshot:
    elements = list(
        await session.scalars(
            select(BaselineElementRecord).where(BaselineElementRecord.baseline_id == baseline_id)
        )
    )
    links = await session.scalars(
        select(BaselineRelationshipRecord).where(
            BaselineRelationshipRecord.baseline_id == baseline_id
        )
    )
    boxes = await session.scalars(
        select(BaselineBoxRecord).where(BaselineBoxRecord.baseline_id == baseline_id)
    )
    return ModelSnapshot(
        elements=tuple(_copied_element(row) for row in elements),
        relationships=tuple(_copied_relationship(row) for row in links),
        boxes=tuple(_box(b) for b in boxes),
        package_paths={
            row.package_id: row.package_path
            for row in elements
            if row.package_id is not None and row.package_path is not None
        },
    )


async def _current_snapshot(session: AsyncSession) -> ModelSnapshot:
    elements = await session.scalars(select(ElementRecord))
    links = await session.scalars(select(RelationshipRecord))
    boxes = await session.scalars(select(DiagramNodeRecord))
    paths = _paths()
    package_paths = dict((await session.execute(select(paths.c.id, paths.c.path))).tuples().all())
    return ModelSnapshot(
        elements=tuple(element_from_row(row) for row in elements),
        relationships=tuple(relationship_from_row(row) for row in links),
        boxes=tuple(_box(b) for b in boxes),
        package_paths=package_paths,
    )


async def _count_documents(session: AsyncSession, element_ids: Sequence[UUID]) -> int:
    if not element_ids:
        return 0
    # One array parameter, not one per id: a long list must not meet the driver's bind limit.
    wanted = sql_cast(literal(list(element_ids), ARRAY(Uuid())), ARRAY(Uuid()))
    return int(
        await session.scalar(select(func.count()).where(ElementDocument.element_id == any_(wanted)))
        or 0
    )


async def _counts(session: AsyncSession, baseline_id: UUID) -> tuple[int, int, int]:
    return cast(
        "tuple[int, int, int]",
        tuple(
            [
                int(
                    await session.scalar(
                        select(func.count()).where(model.baseline_id == baseline_id)
                    )
                    or 0
                )
                for model in (BaselineElementRecord, BaselineRelationshipRecord, BaselineBoxRecord)
            ]
        ),
    )


class PostgresBaselineRepository:
    """Baselines, stored beside the model they copy."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        limits: BaselineLimits = BaselineLimits(),  # noqa: B008 - frozen, shared on purpose
        protected: ProtectedIds = no_element_is_protected,
    ) -> None:
        self._sessions = sessions
        self._limits = limits
        self._protected = protected

    @asynccontextmanager
    async def _at_one_instant(self) -> AsyncIterator[AsyncSession]:
        """A read-only session whose every statement sees the same snapshot."""
        async with self._sessions() as session:
            await session.connection(
                execution_options={
                    "isolation_level": "REPEATABLE READ",
                    "postgresql_readonly": True,
                }
            )
            yield session

    async def list_all(self) -> tuple[Baseline, ...]:
        statement = _counted().order_by(BaselineRecord.name, BaselineRecord.id)
        async with self._sessions() as session:
            return tuple(_baseline(*row) for row in await session.execute(statement))

    async def get(self, baseline_id: UUID) -> Baseline | None:
        statement = _counted().where(BaselineRecord.id == baseline_id)
        async with self._sessions() as session:
            found = (await session.execute(statement)).first()
            return _baseline(*found) if found is not None else None

    async def _refuse_a_model_too_large(self, session: AsyncSession) -> None:
        """Measured at the instant the copy reads, before a single row is written."""
        elements, element_bytes = (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(
                        func.sum(
                            func.octet_length(ElementRecord.name)
                            + func.octet_length(ElementRecord.description)
                            + func.octet_length(ElementRecord.documentation)
                            + func.octet_length(sql_cast(ElementRecord.properties, Text))
                        ),
                        0,
                    ),
                )
            )
        ).one()
        links, link_bytes = (
            await session.execute(
                select(
                    func.count(),
                    func.coalesce(
                        func.sum(
                            func.octet_length(RelationshipRecord.name)
                            + func.octet_length(sql_cast(RelationshipRecord.properties, Text))
                        ),
                        0,
                    ),
                )
            )
        ).one()
        boxes = int(await session.scalar(select(func.count()).select_from(DiagramNodeRecord)) or 0)
        size = int(element_bytes) + int(link_bytes) + ROW_BYTES * (elements + links + boxes)
        if elements > self._limits.elements:
            msg = (
                f"the model holds {elements} elements, a baseline copies at most "
                f"{self._limits.elements}"
            )
            raise BaselineTooLargeError(msg)
        if size > self._limits.bytes:
            msg = (
                f"the model weighs about {size} bytes, "
                f"a baseline copies at most {self._limits.bytes}"
            )
            raise BaselineTooLargeError(msg)

    async def create(self, baseline: Baseline, *, audit: AuditOf[Baseline]) -> Baseline:
        id_column = literal(baseline.id, Uuid())
        paths = _paths()
        async with self._sessions.begin() as session:
            # Before any statement: the snapshot every `INSERT … SELECT` below shares.
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            await self._refuse_a_model_too_large(session)
            session.add(
                BaselineRecord(
                    id=baseline.id,
                    name=baseline.name,
                    description=baseline.description,
                    created_by_sub=baseline.created_by_sub,
                    created_by=baseline.created_by,
                    created_at=baseline.created_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                if constraint_of(error) != NAME_TAKEN:
                    raise
                msg = f"a baseline is already named {baseline.name!r}"
                raise DuplicateBaselineError(msg) from error
            await session.execute(
                insert(BaselineElementRecord).from_select(
                    ["baseline_id", *_ELEMENT_COLUMNS, "package_path"],
                    select(
                        id_column,
                        *(getattr(ElementRecord, name) for name in _ELEMENT_COLUMNS),
                        paths.c.path,
                    ).outerjoin(paths, paths.c.id == ElementRecord.package_id),
                )
            )
            for target, source, columns in (
                (BaselineRelationshipRecord, RelationshipRecord, _RELATIONSHIP_COLUMNS),
                (BaselineBoxRecord, DiagramNodeRecord, _BOX_COLUMNS),
            ):
                await session.execute(
                    insert(target).from_select(
                        ["baseline_id", *columns],
                        select(id_column, *(getattr(source, name) for name in columns)),
                    )
                )
            stored = baseline.with_counts(*await _counts(session, baseline.id))
            write_events(session, events_for(audit, stored))
        return stored

    async def delete(self, baseline_id: UUID, *, audit: AuditOf[Baseline]) -> bool:
        async with self._sessions.begin() as session:
            row = await session.get(BaselineRecord, baseline_id, with_for_update=True)
            if row is None:
                return False
            events = events_for(audit, _baseline(row, *await _counts(session, baseline_id)))
            await session.execute(delete(BaselineRecord).where(BaselineRecord.id == baseline_id))
            write_events(session, events)
        return True

    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot:
        async with self._at_one_instant() as session:
            return await _snapshot_of(session, baseline_id)

    async def current_snapshot(self) -> ModelSnapshot:
        async with self._at_one_instant() as session:
            return await _current_snapshot(session)

    async def _plan(
        self, session: AsyncSession, baseline_id: UUID, *, keep_new: bool
    ) -> RestorePlan:
        """The plan, from one instant of the model, the protection and the documents."""
        if await session.get(BaselineRecord, baseline_id) is None:
            msg = f"no baseline with id {baseline_id}"
            raise BaselineNotFoundError(msg)
        plan = plan_restore(
            await _snapshot_of(session, baseline_id),
            await _current_snapshot(session),
            keep_new=keep_new,
            packages_present=frozenset(await session.scalars(select(PackageRecord.id))),
            protected_ids=await self._protected(session),
            diagrams_present=frozenset(await session.scalars(select(DiagramRecord.id))),
        )
        return replace(
            plan,
            documents_lost=await _count_documents(session, [e.id for e in plan.elements_to_delete]),
        )

    async def restore_plan(self, baseline_id: UUID, *, keep_new: bool) -> RestorePlan:
        async with self._at_one_instant() as session:
            return await self._plan(session, baseline_id, keep_new=keep_new)

    async def apply_restore(
        self,
        baseline_id: UUID,
        *,
        keep_new: bool,
        expected: RestorePlan,
        now: datetime,
        audit: AuditOf[RestorePlan],
    ) -> None:
        raise NotImplementedError  # Task 17
