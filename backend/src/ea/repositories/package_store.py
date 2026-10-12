"""The PostgreSQL implementation of `PackageRepository`.

One unit of work per call, like the other stores. The refusals are translated by
the **name** of the constraint (`constraint_of`), never by its message: the
sibling-name rule is two partial unique indexes, the references to a package are
three `RESTRICT` foreign keys, and a lookup-then-insert would let two callers
both find a name free.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, cast
from uuid import uuid4

from sqlalchemy import ARRAY, Text, bindparam, delete, func, select, text, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.architecture import ElementRecord
from ea.db.models.diagram import DiagramRecord
from ea.db.models.package import PackageRecord
from ea.db.models.package_version import PackageVersionRecord
from ea.db.unit_of_work import reading, work
from ea.domain.approval import WriteKind, guard_all
from ea.domain.audit import AUDIT_LISTED_IDS, events_for
from ea.domain.baselines import Baseline, BaselineLimits, refuse_oversize
from ea.domain.errors import (
    CyclicPackageError,
    DomainError,
    DuplicatePackageError,
    ElementNotFoundError,
    PackageAlreadyFrozenError,
    PackageNotEmptyError,
    PackageNotFoundError,
    PackageNotFrozenError,
    SourceInUseError,
    TooManyPackagesError,
    WriteConflictError,
)
from ea.domain.package_versions import (
    Bump,
    PackageVersion,
    Publication,
    PublicationPreview,
    effective_freezes,
    latest_version,
    next_version,
    parse_version,
)
from ea.domain.packages import (
    MAX_PACKAGES,
    Package,
    PackageCounts,
    check_tree,
    descendants_of,
    path_of,
)
from ea.domain.ports import Held, PackageRemoval
from ea.repositories.architecture_store import (
    author_of,
    constraint_of,
    element_from_row,
    frozen_clause,
    lock_elements,
)
from ea.repositories.audit_store import write_events
from ea.repositories.baseline_store import capture_scope, scope_size
from ea.repositories.conflicts import replayable
from ea.repositories.frozen_tables import lock_guarded

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf

#: Key of the transaction-scoped advisory lock every write to the tree takes
#: (docs/adr/0042). A constant, not a hash of something: one tree, one lock.
TREE_LOCK: Final = 0x45415041  # "EAPA"
#: How many frozen elements a refused deletion spells out; the count is exact.
_FROZEN_SAMPLE: Final = 20
NAME_TAKEN: Final = ("uq_packages_root_name", "uq_packages_child_name")
#: The three foreign keys that name a package.
REFERENCES: Final = (
    "fk_packages_parent_id_packages",
    "fk_elements_package_id_packages",
    "fk_diagrams_package_id_packages",
)


def _to_package(row: PackageRecord) -> Package:
    return Package(
        id=row.id,
        parent_id=row.parent_id,
        name=row.name,
        description=row.description,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _refusal(error: IntegrityError, name: str, *, deleting: bool = False) -> DomainError | None:
    """The refusal this constraint means, or `None` for one we do not know."""
    constraint = constraint_of(error)
    if constraint == "fk_diagrams_source_package_id_packages":
        return SourceInUseError("a generated view reads this package: delete the view first")
    if constraint in NAME_TAKEN:
        return DuplicatePackageError(f"a package is already named {name!r} in that place")
    if constraint in REFERENCES:
        if deleting:
            return PackageNotEmptyError(
                "the package still holds sub-packages, elements or diagrams"
            )
        return PackageNotFoundError("the package does not exist")
    return None


async def _locked_tree(session: AsyncSession) -> dict[UUID, Package]:
    """Take the tree's lock, then read the flat list under it.

    The lock is released at commit or rollback. In READ COMMITTED the read that
    follows sees whatever the previous holder committed, so what `check_tree`
    validates is the tree this transaction is about to modify, not the one the
    service read earlier. Nothing writes a `parent_id` without going through it.
    """
    await session.execute(select(func.pg_advisory_xact_lock(TREE_LOCK)))
    rows = await session.scalars(select(PackageRecord))
    return {row.id: _to_package(row) for row in rows}


# A publication waits for the writers in flight, and stops the new ones, so the copy and the
# freeze see the same state. Deliberate: it may wait longer for them than the API's default.
_WAIT_FOR_WRITERS: Final = "SET LOCAL lock_timeout = '10s'"
# A constant statement over our own table names, nothing a caller wrote.
_LOCK_GUARDED: Final = lock_guarded("SHARE")


async def _latest(session: AsyncSession, package_id: UUID) -> str | None:
    """The highest version ever published for this package, reopened or not."""
    rows = await session.scalars(
        select(PackageVersionRecord.version).where(PackageVersionRecord.package_id == package_id)
    )
    return latest_version(rows)


def _to_version(row: PackageVersionRecord) -> PackageVersion:
    return PackageVersion(
        id=row.id,
        package_id=row.package_id,
        version=row.version,
        bump=Bump(row.bump),
        notes=row.notes,
        adr=row.adr,
        baseline_id=row.baseline_id,
        element_count=row.element_count,
        relationship_count=row.relationship_count,
        box_count=row.box_count,
        published_by_sub=row.published_by_sub,
        published_by=row.published_by,
        published_at=row.published_at,
        publish_audit_event_id=row.publish_audit_event_id,
        reopened_by_sub=row.reopened_by_sub,
        reopened_by=row.reopened_by,
        reopened_at=row.reopened_at,
        reopen_reason=row.reopen_reason,
        reopen_audit_event_id=row.reopen_audit_event_id,
    )


async def _in_force(session: AsyncSession) -> dict[UUID, str]:
    rows = await session.execute(
        select(PackageVersionRecord.package_id, PackageVersionRecord.version).where(
            PackageVersionRecord.reopened_at.is_(None)
        )
    )
    return dict(rows.all())


def _subtree(package_id: UUID, tree: Mapping[UUID, Package]) -> frozenset[UUID]:
    return frozenset({package_id, *descendants_of(package_id, tree)})


async def _count_by(session: AsyncSession, column: Any) -> dict[UUID, int]:
    """`{package id: rows}` for a column that names a package."""
    statement = select(column, func.count()).where(column.is_not(None)).group_by(column)
    return dict((await session.execute(statement)).tuples().all())


async def _held_by(session: AsyncSession, column: Any, package_id: UUID) -> Held:
    """How many rows name this package in `column`, and the first few ids."""
    id_column = column.class_.id
    total = await session.scalar(select(func.count()).where(column == package_id))
    ids = await session.scalars(
        select(id_column).where(column == package_id).order_by(id_column).limit(AUDIT_LISTED_IDS)
    )
    return Held(count=int(total or 0), ids=tuple(ids))


class PostgresPackageRepository:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        limits: BaselineLimits = BaselineLimits(),  # noqa: B008 - frozen, shared on purpose
    ) -> None:
        self._sessions = sessions
        self._limits = limits

    async def list_all(self) -> tuple[Package, ...]:
        statement = select(PackageRecord).order_by(func.lower(PackageRecord.name), PackageRecord.id)
        async with reading(self._sessions) as session:
            return tuple(_to_package(row) for row in await session.scalars(statement))

    async def counts(self) -> dict[UUID, PackageCounts]:
        async with reading(self._sessions) as session:
            elements = await _count_by(session, ElementRecord.package_id)
            diagrams = await _count_by(session, DiagramRecord.package_id)
            children = await _count_by(session, PackageRecord.parent_id)
        return {
            key: PackageCounts(
                elements=elements.get(key, 0),
                diagrams=diagrams.get(key, 0),
                children=children.get(key, 0),
            )
            for key in {*elements, *diagrams, *children}
        }

    async def get(self, package_id: UUID) -> Package | None:
        async with reading(self._sessions) as session:
            row = await session.get(PackageRecord, package_id)
            return _to_package(row) if row is not None else None

    async def fold_names(self, names: Sequence[str]) -> list[str]:
        if not names:
            return []
        # `text()` with one bound array: there is no expression for `unnest ... WITH ORDINALITY`
        # that keeps the order, and the order is the point. Nothing is spliced into the SQL.
        statement = text(
            "SELECT lower(n) FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS t(n, i) "
            "ORDER BY i"
        ).bindparams(bindparam("names", type_=ARRAY(Text)))
        async with reading(self._sessions) as session:
            return list((await session.scalars(statement, {"names": list(names)})).all())

    async def add(self, package: Package, *, audit: AuditOf[Package]) -> Package:
        async with work(self._sessions) as session:
            tree = await _locked_tree(session)
            if len(tree) >= MAX_PACKAGES:
                msg = f"the tree holds at most {MAX_PACKAGES} packages"
                raise TooManyPackagesError(msg)
            if package.parent_id is not None and package.parent_id not in tree:
                msg = f"no package with id {package.parent_id}"
                raise PackageNotFoundError(msg)
            check_tree({**tree, package.id: package})
            session.add(
                PackageRecord(
                    id=package.id,
                    parent_id=package.parent_id,
                    name=package.name,
                    description=package.description,
                    created_at=package.created_at,
                    updated_at=package.updated_at,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                refusal = _refusal(error, package.name)
                if refusal is None:
                    raise
                raise refusal from error
            write_events(session, events_for(audit, package))
        return package

    async def save(self, package: Package, *, audit: AuditOf[Package]) -> Package:
        async with work(self._sessions) as session:
            tree = await _locked_tree(session)
            if package.id not in tree:
                msg = f"no package with id {package.id}"
                raise PackageNotFoundError(msg)
            if package.parent_id is not None and package.parent_id not in tree:
                msg = f"no package with id {package.parent_id}"
                raise PackageNotFoundError(msg)
            check_tree({**tree, package.id: package})
            row = await session.get(PackageRecord, package.id, with_for_update=True)
            if row is None:  # pragma: no cover - the lock excludes a concurrent delete
                msg = f"no package with id {package.id}"
                raise PackageNotFoundError(msg)
            before = tree[package.id]
            row.name = package.name
            row.description = package.description
            row.parent_id = package.parent_id
            row.updated_at = package.updated_at
            try:
                await session.flush()
            except IntegrityError as error:
                refusal = _refusal(error, package.name)
                if refusal is None:
                    raise
                raise refusal from error
            # `before` comes from the tree read under the lock, not from the service.
            write_events(session, events_for(audit, before))
        return package

    @replayable
    async def delete(
        self,
        package_id: UUID,
        *,
        into: UUID | None,
        audit: AuditOf[PackageRemoval],
        require_empty: bool = False,
    ) -> bool:
        async with work(self._sessions) as session:
            tree = await _locked_tree(session)
            if package_id not in tree:
                return False
            if into is not None and into not in tree:
                msg = f"no package with id {into}"
                raise PackageNotFoundError(msg)
            if into is not None and (
                into == package_id or into in descendants_of(package_id, tree)
            ):
                msg = "the contents cannot move into the package being deleted or below it"
                raise CyclicPackageError(msg)
            remaining = {key: value for key, value in tree.items() if key != package_id}
            for child in list(remaining.values()):
                if child.parent_id == package_id:
                    remaining[child.id] = replace(child, parent_id=into)
            check_tree(remaining)
            # `FOR UPDATE`: filing a row here takes a key-share lock on this package
            # (its foreign key), so what is counted below is what the move carries.
            row = await session.get(PackageRecord, package_id, with_for_update=True)
            if row is None:  # pragma: no cover - the lock excludes a concurrent delete
                return False
            # What the move below will carry, counted in this transaction.
            removal = PackageRemoval(
                package=tree[package_id],
                into=into,
                sub_packages=await _held_by(session, PackageRecord.parent_id, package_id),
                elements=await _held_by(session, ElementRecord.package_id, package_id),
                diagrams=await _held_by(session, DiagramRecord.package_id, package_id),
            )
            events = events_for(audit, removal)
            if require_empty:
                held = [
                    await session.scalar(select(func.count()).where(column == package_id))
                    for column in (
                        PackageRecord.parent_id,
                        ElementRecord.package_id,
                        DiagramRecord.package_id,
                    )
                ]
                if any(held):
                    msg = "the package still holds sub-packages, elements or diagrams"
                    raise PackageNotEmptyError(msg)
            try:
                if not require_empty:
                    # Re-filing an element is a change to it: an approved or locked one
                    # stays where it is, so the deletion is refused rather than moving it.
                    # Every element of the package is locked first, unfiltered and in id
                    # order: a row that does not match a `WHERE` is skipped by `FOR UPDATE`,
                    # not waited for, so a lock filtered on "frozen" would miss an approval
                    # in flight. The queries after it are new statements and see it.
                    members = await session.scalars(
                        select(ElementRecord.id).where(ElementRecord.package_id == package_id)
                    )
                    await lock_elements(session, members, key_only=False)
                    refused = (
                        await session.execute(
                            select(ElementRecord, func.count().over())
                            .where(ElementRecord.package_id == package_id, frozen_clause())
                            .order_by(ElementRecord.name, ElementRecord.id)
                            .limit(_FROZEN_SAMPLE)
                        )
                    ).all()
                    guard_all(
                        [element_from_row(found) for found, _ in refused],
                        WriteKind.FILING,
                        total=refused[0][1] if refused else 0,
                    )
                    for table, column in (
                        (PackageRecord, PackageRecord.parent_id),
                        (ElementRecord, ElementRecord.package_id),
                        (DiagramRecord, DiagramRecord.package_id),
                    ):
                        moved: dict[str, Any] = {column.key: into}
                        if table is ElementRecord:  # re-filing is a change to the element
                            moved |= {
                                "revision": ElementRecord.revision + 1,
                                "last_modified_by_sub": author_of(events),
                            }
                        await session.execute(
                            update(table).where(column == package_id).values(moved)
                        )
                # With `require_empty` the RESTRICT keys are the last net: a row filed
                # here after the count above makes the DELETE fail, never re-roots it.
                deleted = await session.execute(
                    delete(PackageRecord).where(PackageRecord.id == package_id)
                )
            except IntegrityError as error:
                refusal = _refusal(error, row.name, deleting=True)
                if refusal is None:
                    raise
                raise refusal from error
            write_events(session, events)
        return bool(cast("CursorResult[Any]", deleted).rowcount)

    @replayable
    async def move_elements(
        self,
        element_ids: Sequence[UUID],
        package_id: UUID | None,
        *,
        audit: AuditOf[Mapping[UUID, UUID | None]],
    ) -> None:
        wanted = tuple(dict.fromkeys(element_ids))
        async with work(self._sessions) as session:
            # The tree's lock first, like every other write that names a package: a
            # deletion or a re-parenting of the destination cannot interleave
            # between the origins read below and the update that follows.
            await session.execute(select(func.pg_advisory_xact_lock(TREE_LOCK)))
            # `FOR UPDATE`: the origins the event names are the ones this move leaves.
            found = list((await lock_elements(session, wanted, key_only=False)).values())
            origins = {row.id: row.package_id for row in found}
            missing = [str(element_id) for element_id in wanted if element_id not in origins]
            if missing:
                msg = f"no element with id {', '.join(missing)}"
                raise ElementNotFoundError(msg)
            # Filing is a change to the element: approved and locked ones stay put (all or none).
            guard_all([element_from_row(row) for row in found], WriteKind.FILING)
            events = events_for(audit, origins)
            try:
                # Only what really changes folder is a change to the element: a new
                # revision, last made by the actor of the event.
                await session.execute(
                    update(ElementRecord)
                    .where(
                        ElementRecord.id.in_(wanted),
                        ElementRecord.package_id.is_distinct_from(package_id),
                    )
                    .values(
                        package_id=package_id,
                        revision=ElementRecord.revision + 1,
                        last_modified_by_sub=author_of(events),
                    )
                    .execution_options(synchronize_session=False)
                )
            except IntegrityError as error:
                refusal = _refusal(error, "")
                if refusal is None:
                    raise
                raise refusal from error
            write_events(session, events)

    async def in_force(self) -> dict[UUID, str]:
        async with reading(self._sessions) as session:
            return await _in_force(session)

    async def versions(self, package_id: UUID) -> tuple[PackageVersion, ...]:
        statement = select(PackageVersionRecord).where(
            PackageVersionRecord.package_id == package_id
        )
        async with reading(self._sessions) as session:
            found = [_to_version(row) for row in await session.scalars(statement)]
        # By number, the latest first: a clock that stepped back must not reorder them.
        return tuple(sorted(found, key=lambda v: parse_version(v.version), reverse=True))

    async def _check_publishable(
        self, session: AsyncSession, package_id: UUID, tree: Mapping[UUID, Package]
    ) -> frozenset[UUID]:
        if package_id not in tree:
            msg = f"no package with id {package_id}"
            raise PackageNotFoundError(msg)
        if package_id in effective_freezes(tree, await _in_force(session)):
            msg = "this package is published, or lies under a package that is: reopen it first"
            raise PackageAlreadyFrozenError(msg)
        return _subtree(package_id, tree)

    async def preview_publication(self, package_id: UUID, *, bump: Bump) -> PublicationPreview:
        async with reading(self._sessions) as session:
            rows = await session.scalars(select(PackageRecord))
            tree = {row.id: _to_package(row) for row in rows}
            scope = await self._check_publishable(session, package_id, tree)
            size = await scope_size(session, scope)
            refuse_oversize(size, self._limits, what="the package")
            last = await _latest(session, package_id)
        return PublicationPreview(
            version=next_version(last, bump),
            bump=bump,
            elements=size.elements,
            relationships=size.relationships,
            boxes=size.boxes,
            diagrams=size.diagrams,
        )

    # `audit` is called again if the write is replayed: it builds a fresh event each time.
    @replayable
    async def publish(
        self,
        package_id: UUID,
        *,
        publication: Publication,
        by_sub: str,
        by: str,
        now: datetime,
        audit: AuditOf[PackageVersion],
    ) -> PackageVersion:
        async with work(self._sessions) as session:
            await session.execute(text(_WAIT_FOR_WRITERS))
            tree = await _locked_tree(session)
            # Waits for the writers in flight, then stops the new ones until this commits: what is
            # copied below is exactly what the freeze will protect.
            await session.execute(text(_LOCK_GUARDED))
            scope = await self._check_publishable(session, package_id, tree)
            refuse_oversize(await scope_size(session, scope), self._limits, what="the package")
            last = await _latest(session, package_id)
            version = next_version(last, publication.bump)
            path = path_of(tree[package_id], tree)
            label = path if len(path) <= 120 else "…" + path[-119:]
            # The name carries a fresh random id: nobody can pre-create a baseline under it
            # (names are unique) and make the approver's publication fail.
            baseline_id = uuid4()
            baseline = Baseline.create(
                name=f"{label} {version} [{baseline_id}]",
                description=f"Publication {version} of {label}",
                created_by_sub=by_sub,
                created_by=by,
                now=now,
            )
            baseline = replace(baseline, id=baseline_id)
            captured = await capture_scope(
                session, baseline, scope, package_id=package_id, path=path
            )
            draft = PackageVersion(
                id=uuid4(),
                package_id=package_id,
                version=version,
                bump=publication.bump,
                notes=publication.notes,
                adr=publication.adr,
                baseline_id=captured.id,
                element_count=captured.element_count,
                relationship_count=captured.relationship_count,
                box_count=captured.box_count,
                published_by_sub=by_sub,
                published_by=by,
                published_at=now,
            )
            events = events_for(audit, draft)
            stored = replace(draft, publish_audit_event_id=events[0].id)
            session.add(
                PackageVersionRecord(
                    id=stored.id,
                    package_id=stored.package_id,
                    version=stored.version,
                    bump=stored.bump.value,
                    notes=stored.notes,
                    adr=stored.adr,
                    baseline_id=stored.baseline_id,
                    element_count=stored.element_count,
                    relationship_count=stored.relationship_count,
                    box_count=stored.box_count,
                    published_by_sub=stored.published_by_sub,
                    published_by=stored.published_by,
                    published_at=stored.published_at,
                    publish_audit_event_id=events[0].id,
                )
            )
            try:
                await session.flush()
            except IntegrityError as error:
                # Two publications of one package: the unique index of the version in force.
                if constraint_of(error) == "uq_package_versions_in_force":
                    msg = "this package was published meanwhile: reopen it first"
                    raise PackageAlreadyFrozenError(msg) from error
                if constraint_of(error) == "uq_package_versions_package_version":
                    msg = "another publication of this package got in the way: read it again"
                    raise WriteConflictError(msg) from error
                raise
            write_events(session, events)
        return stored

    # `audit` is called again if the write is replayed: it builds a fresh event each time.
    @replayable
    async def reopen(
        self,
        package_id: UUID,
        *,
        reason: str,
        by_sub: str,
        by: str,
        now: datetime,
        audit: AuditOf[PackageVersion],
    ) -> PackageVersion:
        async with work(self._sessions) as session:
            tree = await _locked_tree(session)
            if package_id not in tree:
                msg = f"no package with id {package_id}"
                raise PackageNotFoundError(msg)
            row = await session.scalar(
                select(PackageVersionRecord)
                .where(
                    PackageVersionRecord.package_id == package_id,
                    PackageVersionRecord.reopened_at.is_(None),
                )
                .with_for_update()
            )
            if row is None:
                msg = "this package has no version in force"
                raise PackageNotFrozenError(msg)
            closed = replace(
                _to_version(row),
                reopened_by_sub=by_sub,
                reopened_by=by,
                reopened_at=now,
                reopen_reason=reason,
            )
            events = events_for(audit, closed)
            row.reopened_by_sub = by_sub
            row.reopened_by = by
            row.reopened_at = now
            row.reopen_reason = reason
            row.reopen_audit_event_id = events[0].id
            await session.flush()
            write_events(session, events)
        return replace(closed, reopen_audit_event_id=events[0].id)
