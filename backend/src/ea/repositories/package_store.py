"""The PostgreSQL implementation of `PackageRepository`.

One unit of work per call, like the other stores. The refusals are translated by
the **name** of the constraint (`constraint_of`), never by its message: the
sibling-name rule is two partial unique indexes, the references to a package are
three `RESTRICT` foreign keys, and a lookup-then-insert would let two callers
both find a name free.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.architecture import ElementRecord
from ea.db.models.diagram import DiagramRecord
from ea.db.models.package import PackageRecord
from ea.domain.approval import check_all_untouched
from ea.domain.audit import AUDIT_LISTED_IDS, events_for
from ea.domain.errors import (
    CyclicPackageError,
    DomainError,
    DuplicatePackageError,
    ElementNotFoundError,
    PackageNotEmptyError,
    PackageNotFoundError,
    TooManyPackagesError,
)
from ea.domain.packages import MAX_PACKAGES, Package, PackageCounts, check_tree, descendants_of
from ea.domain.ports import Held, PackageRemoval
from ea.repositories.architecture_store import constraint_of, element_from_row, frozen_clause
from ea.repositories.audit_store import write_events

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditOf

#: Key of the transaction-scoped advisory lock every write to the tree takes
#: (docs/adr/0042). A constant, not a hash of something: one tree, one lock.
TREE_LOCK: Final = 0x45415041  # "EAPA"
#: How many frozen elements a refused deletion spells out (one more is read, to say "others").
_FROZEN_SAMPLE: Final = 5
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
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_all(self) -> tuple[Package, ...]:
        statement = select(PackageRecord).order_by(func.lower(PackageRecord.name), PackageRecord.id)
        async with self._sessions() as session:
            return tuple(_to_package(row) for row in await session.scalars(statement))

    async def counts(self) -> dict[UUID, PackageCounts]:
        async with self._sessions() as session:
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
        async with self._sessions() as session:
            row = await session.get(PackageRecord, package_id)
            return _to_package(row) if row is not None else None

    async def add(self, package: Package, *, audit: AuditOf[Package]) -> Package:
        async with self._sessions.begin() as session:
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
        async with self._sessions.begin() as session:
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

    async def delete(
        self,
        package_id: UUID,
        *,
        into: UUID | None,
        audit: AuditOf[PackageRemoval],
        require_empty: bool = False,
    ) -> bool:
        async with self._sessions.begin() as session:
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
                    sample = await session.scalars(
                        select(ElementRecord)
                        .where(ElementRecord.package_id == package_id, frozen_clause())
                        .order_by(ElementRecord.name, ElementRecord.id)
                        .limit(_FROZEN_SAMPLE + 1)
                        .with_for_update()
                    )
                    frozen = [element_from_row(found) for found in sample]
                    check_all_untouched(frozen[:_FROZEN_SAMPLE], more=len(frozen) > _FROZEN_SAMPLE)
                    for table, column in (
                        (PackageRecord, PackageRecord.parent_id),
                        (ElementRecord, ElementRecord.package_id),
                        (DiagramRecord, DiagramRecord.package_id),
                    ):
                        await session.execute(
                            update(table).where(column == package_id).values({column.key: into})
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
            write_events(session, events_for(audit, removal))
        return bool(cast("CursorResult[Any]", deleted).rowcount)

    async def move_elements(
        self,
        element_ids: Sequence[UUID],
        package_id: UUID | None,
        *,
        audit: AuditOf[Mapping[UUID, UUID | None]],
    ) -> None:
        wanted = tuple(dict.fromkeys(element_ids))
        async with self._sessions.begin() as session:
            # The tree's lock first, like every other write that names a package: a
            # deletion or a re-parenting of the destination cannot interleave
            # between the origins read below and the update that follows.
            await session.execute(select(func.pg_advisory_xact_lock(TREE_LOCK)))
            # `FOR UPDATE`: the origins the event names are the ones this move leaves.
            found = (
                await session.scalars(
                    select(ElementRecord).where(ElementRecord.id.in_(wanted)).with_for_update()
                )
            ).all()
            origins = {row.id: row.package_id for row in found}
            missing = [str(element_id) for element_id in wanted if element_id not in origins]
            if missing:
                msg = f"no element with id {', '.join(missing)}"
                raise ElementNotFoundError(msg)
            # Filing is a change to the element: approved and locked ones stay put (all or none).
            check_all_untouched([element_from_row(row) for row in found])
            try:
                await session.execute(
                    update(ElementRecord)
                    .where(ElementRecord.id.in_(wanted))
                    .values(package_id=package_id)
                    .execution_options(synchronize_session=False)
                )
            except IntegrityError as error:
                refusal = _refusal(error, "")
                if refusal is None:
                    raise
                raise refusal from error
            write_events(session, events_for(audit, origins))
