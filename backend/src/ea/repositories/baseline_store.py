"""The PostgreSQL implementation of `BaselineRepository` — see docs/adr/0047.

A capture is three `INSERT … SELECT` in one REPEATABLE READ transaction: the copy
never passes through Python, every statement sees the same instant of the model
(a link is never copied without its element), and a baseline is either complete
or absent. Every read that feeds a decision runs at one instant too.

Nothing here references the live model by a foreign key: a baseline outlives the
elements, packages and diagrams it remembers.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import ARRAY, Text, Uuid, and_, any_, delete, func, literal, select, text
from sqlalchemy import cast as sql_cast
from sqlalchemy import update as sql_update
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError, IntegrityError
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
from ea.db.unit_of_work import reading, work
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.audit import AUDIT_LISTED_IDS, events_for
from ea.domain.baselines import (
    Baseline,
    BaselineLimits,
    BoxSnapshot,
    ModelSize,
    ModelSnapshot,
    blocked_message,
    plan_restore,
    refusal_context,
    refuse_oversize,
    restricted_to,
)
from ea.domain.errors import (
    BaselineInUseError,
    BaselineNotFoundError,
    BaselineScopedError,
    DuplicateBaselineError,
    PackageNotFoundError,
    RestoreBlockedError,
    RestoreConflictError,
)
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY
from ea.domain.model import Element, Relationship
from ea.domain.packages import PATH_SEPARATOR, Package, descendants_of, path_of
from ea.domain.timeline import readable_lifespan_properties
from ea.repositories.architecture_store import (
    author_of,
    constraint_of,
    element_from_row,
    element_row,
    frozen_clause,
    rejected,
    relationship_from_row,
    relationship_row,
    touch,
)
from ea.repositories.audit_store import write_events
from ea.repositories.conflicts import is_serialization_failure
from ea.repositories.diagram_revision import bump_diagrams_of, packages_of_diagrams
from ea.repositories.frozen_tables import lock_guarded

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.sql.selectable import CTE

    from ea.domain.audit import AuditOf
    from ea.domain.baselines import RestorePlan

logger = logging.getLogger(__name__)

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
#: applies it (docs/adr/0048).
ProtectedIds = Callable[["AsyncSession"], Awaitable[frozenset["UUID"]]]


async def approved_or_locked(session: AsyncSession) -> frozenset[UUID]:
    """The elements a restoration must not touch: approved, locked, or in a state it does not know.

    Fail closed: the test is "not draft and not in review", so a value outside the
    enumeration protects instead of exposing. A restoration is a bulk rewrite, so
    it respects the approval whoever asks — an approver reopens first — and is
    read inside the SERIALIZABLE transaction that writes, where a lock committed
    after its snapshot makes the update of that row a serialization failure
    (a replayable conflict) rather than a silent overwrite.
    """
    return frozenset(await session.scalars(select(ElementRecord.id).where(frozen_clause())))


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
        scope_package_id=row.scope_package_id,
        scope_path=row.scope_path,
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
        properties=readable_lifespan_properties(row.properties),
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
        properties=readable_lifespan_properties(row.properties),
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


async def _tree(session: AsyncSession) -> dict[UUID, Package]:
    return {
        row.id: Package(
            id=row.id,
            parent_id=row.parent_id,
            name=row.name,
            description=row.description,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
        for row in (await session.execute(select(PackageRecord))).scalars()
    }


async def _subtree(
    session: AsyncSession, package_id: UUID
) -> tuple[frozenset[UUID], frozenset[UUID]]:
    """The packages of this subtree *now* (itself and its descendants), and its diagrams."""
    below = frozenset({package_id, *descendants_of(package_id, await _tree(session))})
    diagrams = frozenset(
        await session.scalars(select(DiagramRecord.id).where(DiagramRecord.package_id.in_(below)))
    )
    return below, diagrams


async def _documents_held(
    session: AsyncSession, element_ids: Sequence[UUID]
) -> dict[UUID, tuple[int, tuple[UUID, ...]]]:
    """`{element: (exact count of documents, the first AUDIT_LISTED_IDS ids)}`, by aggregate.

    One array parameter, not one per id: a long list must not meet the driver's bind limit.
    """
    if not element_ids:
        return {}
    wanted = sql_cast(literal(list(element_ids), ARRAY(Uuid())), ARRAY(Uuid()))
    counts = await session.execute(
        select(ElementDocument.element_id, func.count())
        .where(ElementDocument.element_id == any_(wanted))
        .group_by(ElementDocument.element_id)
    )
    ranked = (
        select(
            ElementDocument.element_id.label("element_id"),
            ElementDocument.id.label("id"),
            func.row_number()
            .over(partition_by=ElementDocument.element_id, order_by=ElementDocument.id)
            .label("rank"),
        )
        .where(ElementDocument.element_id == any_(wanted))
        .subquery()
    )
    listed: dict[UUID, list[UUID]] = {}
    for element_id, document_id in await session.execute(
        select(ranked.c.element_id, ranked.c.id)
        .where(ranked.c.rank <= AUDIT_LISTED_IDS)
        .order_by(ranked.c.element_id, ranked.c.id)
    ):
        listed.setdefault(element_id, []).append(document_id)
    return {element: (int(n), tuple(listed.get(element, ()))) for element, n in counts}


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


async def _measure(
    session: AsyncSession,
    elements: Any,
    links: Any,
    boxes: Any,
    *,
    baseline_id: UUID | None = None,
    diagrams: bool = False,
    clauses: Mapping[Any, Any] | None = None,
) -> ModelSize:
    """Row counts and bytes of text, as aggregates: no row reaches Python.

    The same function measures the live tables and a baseline's copy of them
    (`baseline_id`), so a kind of row cannot be bounded on one side only. The
    packages the plan reads are bounded by the tree's own `MAX_PACKAGES`.
    """

    def where(model: Any) -> list[Any]:
        found = [model.baseline_id == baseline_id] if baseline_id is not None else []
        return [*found, clauses[model]] if clauses and model in clauses else found

    element_count, element_text = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(
                        func.octet_length(elements.name)
                        + func.octet_length(elements.description)
                        + func.octet_length(elements.documentation)
                        + func.octet_length(sql_cast(elements.properties, Text))
                    ),
                    0,
                ),
            ).where(*where(elements))
        )
    ).one()
    link_count, link_text = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(
                    func.sum(
                        func.octet_length(links.name)
                        + func.octet_length(sql_cast(links.properties, Text))
                    ),
                    0,
                ),
            ).where(*where(links))
        )
    ).one()
    box_count = int(
        await session.scalar(select(func.count()).select_from(boxes).where(*where(boxes))) or 0
    )
    diagram_count = (
        int(await session.scalar(select(func.count()).select_from(DiagramRecord)) or 0)
        if diagrams
        else 0
    )
    return ModelSize(
        elements=element_count,
        relationships=link_count,
        boxes=box_count,
        diagrams=diagram_count,
        bytes=int(element_text)
        + int(link_text)
        + ROW_BYTES * (element_count + link_count + box_count),
    )


def _scope_filters(scope: Sequence[UUID]) -> tuple[Any, Any, Any]:
    """The three WHERE clauses of a subtree: its elements, the links with both ends in it,
    and the boxes of the diagrams filed in it."""
    in_scope = ElementRecord.package_id.in_(scope)
    members = select(ElementRecord.id).where(in_scope)
    linked = and_(
        RelationshipRecord.source_id.in_(members), RelationshipRecord.target_id.in_(members)
    )
    boxed = DiagramNodeRecord.diagram_id.in_(
        select(DiagramRecord.id).where(DiagramRecord.package_id.in_(scope))
    )
    return in_scope, linked, boxed


async def scope_size(session: AsyncSession, scope: frozenset[UUID]) -> ModelSize:
    """What a subtree holds, counted by the server before anything is copied.

    Rows and bytes of text, by the same aggregates as the whole model (`_measure`).
    """
    ids = sorted(scope)
    in_scope, linked, boxed = _scope_filters(ids)
    size = await _measure(
        session,
        ElementRecord,
        RelationshipRecord,
        DiagramNodeRecord,
        clauses={ElementRecord: in_scope, RelationshipRecord: linked, DiagramNodeRecord: boxed},
    )
    filed = DiagramRecord.package_id.in_(ids)
    diagrams = int(
        await session.scalar(select(func.count()).select_from(DiagramRecord).where(filed)) or 0
    )
    return replace(size, diagrams=diagrams)


async def capture_scope(
    session: AsyncSession,
    baseline: Baseline,
    scope: frozenset[UUID],
    *,
    package_id: UUID,
    path: str,
) -> Baseline:
    """Copy one subtree under this baseline, in the caller's transaction.

    The subtree must not move while it is copied, and the caller sees to that one of two
    ways (docs/adr/0074). A publication holds the locks that keep it still, since it freezes
    what it copies. A plain scoped capture holds none: it reads in one REPEATABLE READ
    snapshot, so the three `INSERT … SELECT` (the same as a whole-model capture) all see the
    same committed state, which is a consistent copy even if the model moves afterwards.
    """
    id_column = literal(baseline.id, Uuid())
    in_scope, linked, boxed = _scope_filters(sorted(scope))
    paths = _paths()
    session.add(
        BaselineRecord(
            id=baseline.id,
            name=baseline.name,
            description=baseline.description,
            created_by_sub=baseline.created_by_sub,
            created_by=baseline.created_by,
            created_at=baseline.created_at,
            scope_package_id=package_id,
            scope_path=path,
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
            )
            .outerjoin(paths, paths.c.id == ElementRecord.package_id)
            .where(in_scope),
        )
    )
    for target, source, columns, clause in (
        (BaselineRelationshipRecord, RelationshipRecord, _RELATIONSHIP_COLUMNS, linked),
        (BaselineBoxRecord, DiagramNodeRecord, _BOX_COLUMNS, boxed),
    ):
        await session.execute(
            insert(target).from_select(
                ["baseline_id", *columns],
                select(id_column, *(getattr(source, name) for name in columns)).where(clause),
            )
        )
    stored = baseline.with_counts(*await _counts(session, baseline.id))
    return replace(stored, scope_package_id=package_id, scope_path=path)


class PostgresBaselineRepository:
    """Baselines, stored beside the model they copy."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        limits: BaselineLimits = BaselineLimits(),  # noqa: B008 - frozen, shared on purpose
        protected: ProtectedIds = approved_or_locked,
    ) -> None:
        self._sessions = sessions
        self._limits = limits
        self._protected = protected

    async def _guard_live(self, session: AsyncSession, *, diagrams: bool) -> None:
        """Refuse the live model if it is past a bound — by counting, never by loading."""
        refuse_oversize(
            await _measure(
                session, ElementRecord, RelationshipRecord, DiagramNodeRecord, diagrams=diagrams
            ),
            self._limits,
        )

    async def _guard_baseline(self, session: AsyncSession, baseline_id: UUID) -> None:
        """The same bounds on a copy, before it is read back whole."""
        refuse_oversize(
            await _measure(
                session,
                BaselineElementRecord,
                BaselineRelationshipRecord,
                BaselineBoxRecord,
                baseline_id=baseline_id,
            ),
            self._limits,
            what="the baseline",
        )

    @asynccontextmanager
    async def _at_one_instant(self) -> AsyncIterator[AsyncSession]:
        """A read-only session whose every statement sees the same snapshot."""
        async with reading(self._sessions, alone=True) as session:
            await session.connection(
                execution_options={
                    "isolation_level": "REPEATABLE READ",
                    "postgresql_readonly": True,
                }
            )
            yield session

    async def list_all(self) -> tuple[Baseline, ...]:
        statement = _counted().order_by(BaselineRecord.name, BaselineRecord.id)
        async with reading(self._sessions) as session:
            return tuple(_baseline(*row) for row in await session.execute(statement))

    async def get(self, baseline_id: UUID) -> Baseline | None:
        statement = _counted().where(BaselineRecord.id == baseline_id)
        async with reading(self._sessions) as session:
            found = (await session.execute(statement)).first()
            return _baseline(*found) if found is not None else None

    async def _create_scoped(
        self, baseline: Baseline, audit: AuditOf[Baseline], package_id: UUID
    ) -> Baseline:
        async with work(self._sessions, alone=True) as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            tree = await _tree(session)
            if package_id not in tree:
                raise PackageNotFoundError(str(package_id))
            scope = frozenset({package_id, *descendants_of(package_id, tree)})
            refuse_oversize(await scope_size(session, scope), self._limits, what="the package")
            stored = await capture_scope(
                session,
                baseline,
                scope,
                package_id=package_id,
                path=path_of(tree[package_id], tree),
            )
            write_events(session, events_for(audit, stored))
        return stored

    async def create(
        self, baseline: Baseline, *, audit: AuditOf[Baseline], scope: UUID | None = None
    ) -> Baseline:
        if scope is not None:
            return await self._create_scoped(baseline, audit, scope)
        id_column = literal(baseline.id, Uuid())
        paths = _paths()
        async with work(self._sessions, alone=True) as session:
            # Before any statement: the snapshot every `INSERT … SELECT` below shares.
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            await self._guard_live(session, diagrams=False)
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
        async with work(self._sessions) as session:
            row = await session.get(BaselineRecord, baseline_id, with_for_update=True)
            if row is None:
                return False
            events = events_for(audit, _baseline(row, *await _counts(session, baseline_id)))
            try:
                await session.execute(
                    delete(BaselineRecord).where(BaselineRecord.id == baseline_id)
                )
                await session.flush()
            except IntegrityError as error:
                if constraint_of(error) != "fk_package_versions_baseline_id_baselines":
                    raise
                msg = "this baseline is the copy of a published version"
                raise BaselineInUseError(msg) from error
            write_events(session, events)
        return True

    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot:
        async with self._at_one_instant() as session:
            await self._guard_baseline(session, baseline_id)
            return await _snapshot_of(session, baseline_id)

    async def current_snapshot(
        self, scope: UUID | None = None, *, known: frozenset[UUID] = frozenset()
    ) -> ModelSnapshot:
        async with self._at_one_instant() as session:
            await self._guard_live(session, diagrams=False)
            current = await _current_snapshot(session)
            if scope is None:
                return current
            below, diagrams = await _subtree(session, scope)
            return restricted_to(current, packages=below, diagrams=diagrams, known=known)

    async def _plan(
        self, session: AsyncSession, baseline_id: UUID, *, keep_new: bool
    ) -> RestorePlan:
        """The plan, from one instant of the model, the protection and the documents."""
        record = await session.get(BaselineRecord, baseline_id)
        if record is None:
            msg = f"no baseline with id {baseline_id}"
            raise BaselineNotFoundError(msg)
        scope = record.scope_package_id
        packages_present = frozenset(await session.scalars(select(PackageRecord.id)))
        if scope is not None and scope not in packages_present:
            msg = "the package this baseline copies no longer exists: it cannot be restored"
            raise BaselineScopedError(msg)
        # Counted before any row is loaded, in the instant the plan reads.
        await self._guard_baseline(session, baseline_id)
        await self._guard_live(session, diagrams=True)
        snapshot = await _snapshot_of(session, baseline_id)
        current = await _current_snapshot(session)
        deletable = None
        if scope is not None:
            # It deletes only what is filed in its subtree now and writes no box outside it;
            # the plan still sees the whole model, so a protected element anywhere a deletion
            # cascades to blocks it.
            below, diagrams_present = await _subtree(session, scope)
            deletable = frozenset(e.id for e in current.elements if e.package_id in below)
        else:
            diagrams_present = frozenset(await session.scalars(select(DiagramRecord.id)))
        plan = plan_restore(
            snapshot,
            current,
            keep_new=keep_new,
            packages_present=packages_present,
            protected_ids=await self._protected(session),
            diagrams_present=diagrams_present,
            deletable=deletable,
        )
        if scope is not None:
            # The diagrams it writes are packages it writes too, for the freeze prediction.
            plan = replace(
                plan,
                packages_written=plan.packages_written
                | await packages_of_diagrams(
                    session,
                    element_ids=[e.id for e in plan.elements_to_delete],
                    relationship_ids=[r.id for r in plan.relationships_to_delete],
                    diagram_ids={b.diagram_id for b in plan.boxes_to_restore},
                ),
            )
        held = await _documents_held(session, [e.id for e in plan.elements_to_delete])
        return replace(
            plan,
            documents_lost=sum(count for count, _ in held.values()),
            cascades=tuple(
                replace(
                    cascade,
                    document_count=held.get(cascade.element_id, (0, ()))[0],
                    document_ids=held.get(cascade.element_id, (0, ()))[1],
                )
                for cascade in plan.cascades
            ),
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
        """One SERIALIZABLE transaction: the plan, its boxes and its events, or nothing.

        The plan the caller confirmed was computed before this transaction
        opened, so it is computed again here, at the instant the writes will
        see, and applied only if it is the same plan: a model that moved since
        the dry run, a protected element (read here, not before) or a clash is
        a refusal, and a serialization failure is the same replayable
        `RestoreConflictError` — read the plan again and retry.
        """
        blocked: tuple[Element, ...] = ()
        try:
            try:
                async with work(self._sessions, alone=True) as session:
                    await session.connection(execution_options={"isolation_level": "SERIALIZABLE"})
                    # The very first statement, so before the snapshot: it waits for a
                    # publication in flight (SHARE), whose freeze the triggers then see. Without
                    # it the snapshot predates the freeze and the writes slip past it.
                    await session.execute(text(lock_guarded("ROW EXCLUSIVE")))
                    plan = await self._plan(session, baseline_id, keep_new=keep_new)
                    blocked = plan.blocked
                    if plan.is_blocked:
                        msg = blocked_message(plan.blocked)
                        raise RestoreBlockedError(msg)
                    if plan != expected:
                        msg = "the model changed since the plan was read: read the plan again"
                        raise RestoreConflictError(msg)
                    # The plan read rows as objects; the writes below are statements.
                    session.expunge_all()
                    events = events_for(audit, plan)
                    await _write(session, plan, now, author_of(events))
                    write_events(session, events)
            except DBAPIError as error:
                if is_serialization_failure(error):
                    msg = "the catalogue changed during the restoration: read the plan again"
                    raise RestoreConflictError(msg) from error
                if isinstance(error, IntegrityError) and (constraint_of(error) or "").startswith(
                    "fk_"
                ):
                    msg = "something the plan relies on was removed meanwhile: read the plan again"
                    raise RestoreConflictError(msg) from error
                raise
        except (RestoreBlockedError, RestoreConflictError) as refusal:
            # The transaction is rolled back, so there is no audit event: the log is the trace.
            logger.warning(
                "restoration refused: %s",
                refusal,
                extra={
                    "action": (
                        "restore_blocked"
                        if isinstance(refusal, RestoreBlockedError)
                        else "restore_conflict"
                    ),
                    **refusal_context(baseline_id, blocked),
                },
            )
            raise


async def _flush_restoring(session: AsyncSession, element: Element) -> None:
    """Flush, and turn a uniqueness refusal into the sentence that names the element."""
    try:
        await session.flush()
    except IntegrityError as error:
        refusal = rejected(element, constraint_of(error))
        if refusal is None:
            raise
        msg = (
            f"restoring {element.name!r} conflicts with the current catalogue: {refusal}. "
            "Resolve the clash first: replaying the same plan fails the same way"
        )
        raise RestoreConflictError(msg) from error


async def _write(
    session: AsyncSession, plan: RestorePlan, now: datetime, author: str | None
) -> None:
    """Links out, elements out (their documents, boxes and links follow by foreign key),
    then fields, elements and links back, then boxes."""
    # A link that appears, disappears or goes with a deleted element is a change to the
    # elements at its other end (docs/adr/0048): each is a new revision, by `author`. The
    # plan has already refused any of them that is approved or locked (`touched`).
    gone = {e.id for e in plan.elements_to_delete}
    rewritten = {u.current.id for u in plan.elements_to_update}
    ends = {
        end
        for link in (*plan.relationships_to_delete, *plan.relationships_to_create)
        for end in (link.source_id, link.target_id)
    }
    if gone:
        cascaded = await session.execute(
            select(RelationshipRecord.source_id, RelationshipRecord.target_id).where(
                RelationshipRecord.source_id.in_(gone) | RelationshipRecord.target_id.in_(gone)
            )
        )
        ends |= {end for pair in cascaded for end in pair}
    await touch(
        session, sorted((ends - gone - rewritten) - {e.id for e in plan.elements_to_create}), author
    )
    await bump_diagrams_of(
        session,
        element_ids=[e.id for e in plan.elements_to_delete],
        relationship_ids=[r.id for r in plan.relationships_to_delete],
    )
    for link in plan.relationships_to_delete:
        await session.execute(delete(RelationshipRecord).where(RelationshipRecord.id == link.id))
    for element in plan.elements_to_delete:
        await session.execute(delete(ElementRecord).where(ElementRecord.id == element.id))
    # Two passes. The unique rules (type and name, address, prefix) are immediate,
    # and a restoration is judged by its end state: element A takes the name element
    # B holds *now* but gives up in this same plan. So every element to put back first
    # steps aside under a name nobody can hold (its own id) with no address and no
    # prefix, then takes its final values. That holds a chain, a swap and a cycle,
    # whatever the order.
    rows = []
    for update in plan.elements_to_update:
        row = await session.get(ElementRecord, update.restored.id)
        if row is None:  # pragma: no cover - the plan was just verified in this snapshot
            msg = f"{update.current.name!r} is already gone: read the plan again"
            raise RestoreConflictError(msg)
        row.name = str(update.restored.id)
        row.properties = {
            key: value
            for key, value in row.properties.items()
            if key not in (ADDRESS_PROPERTY, PREFIX_PROPERTY)
        }
        rows.append(row)
    if rows:
        await session.flush()
    for row, update in zip(rows, plan.elements_to_update, strict=True):
        row.name = update.restored.name
        row.description = update.restored.description
        row.documentation = update.restored.documentation
        row.properties = dict(update.restored.properties)
        row.package_id = update.restored.package_id
        row.updated_at = now
        # A restoration changes the content: a new revision, and the review state
        # the plan computed (which a baseline does not copy).
        row.revision = update.current.revision + 1
        row.approval_state = update.restored.approval_state.value
        row.approved_by_sub = update.restored.approved_by_sub
        row.approved_at = update.restored.approved_at
        row.last_modified_by_sub = author
        await _flush_restoring(session, update.restored)
    for element in plan.elements_to_create:
        session.add(
            ElementRecord(
                **element_row(replace(element, last_modified_by_sub=author)),
                package_id=element.package_id,
            )
        )
        await _flush_restoring(session, element)
    for link in plan.relationships_to_create:
        session.add(RelationshipRecord(**relationship_row(link)))
    await session.flush()
    for box in plan.boxes_to_restore:
        statement = upsert(DiagramNodeRecord).values(
            diagram_id=box.diagram_id,
            element_id=box.element_id,
            x=box.x,
            y=box.y,
            width=box.width,
            height=box.height,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=[DiagramNodeRecord.diagram_id, DiagramNodeRecord.element_id],
                set_={
                    "x": statement.excluded.x,
                    "y": statement.excluded.y,
                    "width": statement.excluded.width,
                    "height": statement.excluded.height,
                },
            )
        )
    moved = {box.diagram_id for box in plan.boxes_to_restore}
    if moved:
        # Whoever has one of these diagrams open holds boxes that no longer exist:
        # moving the revision on makes their next save a 409 instead of an overwrite.
        await session.execute(
            sql_update(DiagramRecord)
            .where(DiagramRecord.id.in_(moved))
            .values(revision=DiagramRecord.revision + 1)
        )
