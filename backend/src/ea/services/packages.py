"""Use cases over the classification tree.

The rules that need the whole tree live here, on the flat list the service
loads anyway to compute paths: no cycle, no tree deeper than
`MAX_PACKAGE_DEPTH`, no more than `MAX_PACKAGES`. The rules one row can check
alone (a taken sibling name, a package that still holds something) are the
database's, and arrive here as the repository's refusals.

A package is never a fact of the graph: nothing here reads or writes a
relationship, and moving an element changes nothing about it but its folder.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.audit import AuditAction, package_view
from ea.domain.errors import (
    CyclicPackageError,
    PackageNotEmptyError,
    PackageNotFoundError,
    TooManyPackagesError,
)
from ea.domain.packages import (
    MAX_MOVE_BATCH,
    MAX_PACKAGES,
    UNSET,
    Package,
    PackageCounts,
    PackageSummary,
    Unset,
    check_tree,
    descendants_of,
    path_of,
)
from ea.services.audit import AuditTrail
from ea.services.caller import require_caller, require_editor

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ea.domain.audit import AuditEvent
    from ea.domain.ports import Held, PackageRemoval, PackageRepository

Clock = Callable[[], datetime]

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _held(held: Held) -> dict[str, object]:
    return {"count": held.count, "ids": [str(i) for i in held.ids]}


def _not_found(package_id: UUID) -> PackageNotFoundError:
    return PackageNotFoundError(f"no package with id {package_id}")


class PackageService:
    """The single entry point `api/` and `mcp/` use to read and change the tree."""

    def __init__(self, repository: PackageRepository, *, clock: Clock = _utc_now) -> None:
        self._repository = repository
        self._now = clock
        self._trail = AuditTrail(clock)

    async def _tree(self) -> dict[UUID, Package]:
        return {package.id: package for package in await self._repository.list_all()}

    async def _summaries(self) -> dict[UUID, PackageSummary]:
        packages = await self._tree()
        counts = await self._repository.counts()
        return {
            package.id: PackageSummary(
                package=package,
                path=path_of(package, packages),
                counts=counts.get(package.id, PackageCounts()),
            )
            for package in packages.values()
        }

    async def list_packages(self) -> tuple[PackageSummary, ...]:
        require_caller()
        summaries = await self._summaries()
        return tuple(sorted(summaries.values(), key=lambda s: s.path.lower()))

    async def get(self, package_id: UUID) -> PackageSummary:
        require_caller()
        summary = (await self._summaries()).get(package_id)
        if summary is None:
            raise _not_found(package_id)
        return summary

    async def create(
        self, *, name: str, description: str = "", parent_id: UUID | None = None
    ) -> PackageSummary:
        require_editor()
        packages = await self._tree()
        if len(packages) >= MAX_PACKAGES:
            msg = f"the tree holds at most {MAX_PACKAGES} packages"
            raise TooManyPackagesError(msg)
        if parent_id is not None and parent_id not in packages:
            raise _not_found(parent_id)
        package = Package.create(
            name=name, description=description, parent_id=parent_id, now=self._now()
        )
        check_tree({**packages, package.id: package})
        event = self._trail.event(
            AuditAction.PACKAGE_CREATED, entity_id=package.id, after=package_view(package)
        )
        stored = await self._repository.add(package, audit=(event,))
        self._audit("package created: %r", stored, "package_created")
        return await self.get(stored.id)

    async def update(
        self,
        package_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        parent_id: UUID | Unset | None = UNSET,
    ) -> PackageSummary:
        """Rename, describe or move. `parent_id=None` is the root; leaving it out keeps it."""
        require_editor()
        packages = await self._tree()
        current = packages.get(package_id)
        if current is None:
            raise _not_found(package_id)
        now = self._now()
        revised = current.revise(name=name, description=description, now=now)
        if not isinstance(parent_id, Unset):
            if parent_id is not None and parent_id not in packages:
                raise _not_found(parent_id)
            revised = revised.moved_to(parent_id, now=now)
        check_tree({**packages, package_id: revised})

        def events(before: Package) -> tuple[AuditEvent, ...]:
            # `before` is the package as the repository read it under the tree's lock.
            return (
                self._trail.event(
                    AuditAction.PACKAGE_UPDATED,
                    entity_id=package_id,
                    before=package_view(before),
                    after=package_view(revised),
                ),
            )

        stored = await self._repository.save(revised, audit=events)
        self._audit("package updated: %r", stored, "package_updated")
        return await self.get(stored.id)

    async def delete(
        self, package_id: UUID, *, move_contents_to: UUID | Unset | None = UNSET
    ) -> None:
        """Delete a package, never what it holds.

        Without `move_contents_to` a package that still holds anything is
        refused. With it — a package id, or `None` for the root — the direct
        sub-packages, elements and diagrams move there in the same transaction.
        """
        require_editor()
        packages = await self._tree()
        current = packages.get(package_id)
        if current is None:
            raise _not_found(package_id)
        into: UUID | None = None
        if isinstance(move_contents_to, Unset):
            held = (await self._repository.counts()).get(package_id, PackageCounts())
            if held.elements or held.diagrams or held.children:
                msg = (
                    f"{path_of(current, packages)!r} still holds {held.elements} element(s), "
                    f"{held.diagrams} diagram(s) and {held.children} sub-package(s): "
                    "pass move_contents_to to move them elsewhere (or to the root)"
                )
                raise PackageNotEmptyError(msg)
        else:
            into = move_contents_to
            if into is not None and into not in packages:
                raise _not_found(into)
            if into == package_id or (
                into is not None and into in descendants_of(package_id, packages)
            ):
                msg = "the contents cannot move into the package being deleted or below it"
                raise CyclicPackageError(msg)
            remaining = {key: value for key, value in packages.items() if key != package_id}
            now = self._now()
            for child in list(remaining.values()):
                if child.parent_id == package_id:
                    remaining[child.id] = child.moved_to(into, now=now)
            check_tree(remaining)

        def removed(removal: PackageRemoval) -> tuple[AuditEvent, ...]:
            return (
                self._trail.event(
                    AuditAction.PACKAGE_DELETED,
                    entity_id=package_id,
                    before={
                        **package_view(removal.package),
                        "cascaded": {
                            "moved_to": str(removal.into) if removal.into else "root",
                            "sub_packages": _held(removal.sub_packages),
                            "elements": _held(removal.elements),
                            "diagrams": _held(removal.diagrams),
                        },
                    },
                ),
            )

        if not await self._repository.delete(
            package_id,
            into=into,
            audit=removed,
            require_empty=isinstance(move_contents_to, Unset),
        ):
            raise _not_found(package_id)
        logger.info(
            "package deleted: %r",
            current.name,
            extra={
                "action": "package_deleted",
                "package_id": str(package_id),
                "moved_to": str(into) if into is not None else "root",
            },
        )

    async def move_elements(self, element_ids: Sequence[UUID], package_id: UUID | None) -> int:
        """File up to `MAX_MOVE_BATCH` elements in a package (`None`: the root), all or none."""
        require_editor()
        wanted = tuple(dict.fromkeys(element_ids))
        if not wanted:
            return 0
        if len(wanted) > MAX_MOVE_BATCH:
            msg = f"at most {MAX_MOVE_BATCH} elements can be moved in one call"
            raise ValueError(msg)
        if package_id is not None and await self._repository.get(package_id) is None:
            raise _not_found(package_id)

        def moved(origins: Mapping[UUID, UUID | None]) -> tuple[AuditEvent, ...]:
            where: dict[str, list[str]] = {}
            for element_id, origin in origins.items():
                where.setdefault(str(origin) if origin else "root", []).append(str(element_id))
            return (
                self._trail.event(
                    AuditAction.PACKAGE_ELEMENTS_MOVED,
                    entity_id=package_id,
                    before={"origins": {key: sorted(ids) for key, ids in sorted(where.items())}},
                    after={
                        "destination": str(package_id) if package_id else "root",
                        "element_ids": sorted(str(i) for i in wanted),
                        "count": len(wanted),
                    },
                ),
            )

        await self._repository.move_elements(wanted, package_id, audit=moved)
        logger.info(
            "%d element(s) filed",
            len(wanted),
            extra={
                "action": "elements_moved",
                "count": len(wanted),
                "package_id": str(package_id) if package_id is not None else "root",
            },
        )
        return len(wanted)

    @staticmethod
    def _audit(message: str, package: Package, action: str) -> None:
        logger.info(message, package.name, extra={"action": action, "package_id": str(package.id)})
