"""The classification tree of the catalogue: packages, as in Sparx EA's Project Browser.

A package *files* elements and diagrams, it models nothing: it has no
relationship and no ArchiMate type, and it never appears in a traversal. That
is what separates it from the ArchiMate `grouping`, which is a fact of the
graph. An element sits in at most one package; outside any, it is "at the root".

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

from ea.domain.errors import CyclicPackageError, PackageTooDeepError

#: A root package is level 1.
MAX_PACKAGE_DEPTH: Final = 12
#: The tree is served flat and in one piece (spec §11, Q6).
MAX_PACKAGES: Final = 2000
#: Elements moved in one call.
MAX_MOVE_BATCH: Final = 100
MAX_PACKAGE_NAME_LENGTH: Final = 200
PATH_SEPARATOR: Final = " / "


class Unset:
    """'Not given' — distinct from `None`, which means 'the root'.

    A `PATCH` body and `?move_contents_to=` need three answers: leave it,
    put it at the root, put it there.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


UNSET: Final = Unset()


def _no_nul(value: str, what: str) -> None:
    # PostgreSQL cannot store a NUL in `text`: refused here, not at INSERT.
    if "\x00" in value:
        msg = f"a package {what} cannot contain a NUL character"
        raise ValueError(msg)


def _clean_description(description: str) -> str:
    _no_nul(description, "description")
    return description.strip()


def validate_package_name(name: str) -> str:
    """Trimmed, 1..200 characters, no `/` so that a path reads `A / B / C`."""
    _no_nul(name, "name")
    cleaned = name.strip()
    if not cleaned:
        msg = "a package name cannot be blank"
        raise ValueError(msg)
    if len(cleaned) > MAX_PACKAGE_NAME_LENGTH:
        msg = f"a package name is at most {MAX_PACKAGE_NAME_LENGTH} characters"
        raise ValueError(msg)
    if "/" in cleaned:
        msg = "a package name cannot contain '/'"
        raise ValueError(msg)
    return cleaned


def name_key(name: str) -> str:
    """The key sibling names are compared by in a database with a Unicode locale: its `lower()`.

    The unique indexes are on `lower(name)` — PostgreSQL's, not Python's: `İ` lowers to `i`
    there and to `i` plus a combining dot here, `Σ` lowers the same everywhere but Python's
    `lower()` of a whole word picks the final sigma by context and the database does not, and
    `casefold()` would merge `ß` with `SS`, which the database keeps apart. Lowered one character
    at a time, with the one exception, it is the database's. The deployed database is the
    authority and an import asks it (`PackageRepository.fold_names`); this stands in where there
    is none (the in-memory double, the pure planning of folders in tests).
    """
    return "".join("i" if character == "\u0130" else character.lower() for character in name)


@dataclass(frozen=True, slots=True)
class Package:
    id: UUID
    parent_id: UUID | None
    name: str
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        name: str,
        now: datetime,
        parent_id: UUID | None = None,
        description: str = "",
    ) -> Package:
        return cls(
            id=uuid4(),
            parent_id=parent_id,
            name=validate_package_name(name),
            description=_clean_description(description),
            created_at=now,
            updated_at=now,
        )

    def revise(
        self, *, now: datetime, name: str | None = None, description: str | None = None
    ) -> Package:
        """The same package under a new name or description; an omitted one is kept."""
        return replace(
            self,
            name=self.name if name is None else validate_package_name(name),
            description=self.description
            if description is None
            else _clean_description(description),
            updated_at=now,
        )

    def moved_to(self, parent_id: UUID | None, *, now: datetime) -> Package:
        return replace(self, parent_id=parent_id, updated_at=now)


@dataclass(frozen=True, slots=True)
class PackageCounts:
    """What a package holds directly. Read, never written: the rows are the truth."""

    elements: int = 0
    diagrams: int = 0
    children: int = 0


@dataclass(frozen=True, slots=True)
class PackageSummary:
    package: Package
    path: str
    counts: PackageCounts


def check_tree(packages: Mapping[UUID, Package]) -> None:
    """Refuse a cycle or a tree deeper than `MAX_PACKAGE_DEPTH`.

    Run on the *resulting* tree, before it is written: the same call guards a
    creation, a move and the re-parenting a deletion does. A parent that is not
    in the mapping ends the walk — the database's foreign key is what owns that.
    """
    for package in packages.values():
        seen = {package.id}
        depth = 1
        current = package
        while current.parent_id is not None:
            if current.parent_id in seen:
                msg = f"{package.name!r} would end up inside itself"
                raise CyclicPackageError(msg)
            parent = packages.get(current.parent_id)
            if parent is None:
                break
            seen.add(parent.id)
            depth += 1
            if depth > MAX_PACKAGE_DEPTH:
                msg = f"packages nest at most {MAX_PACKAGE_DEPTH} levels deep"
                raise PackageTooDeepError(msg)
            current = parent


def path_of(package: Package, packages: Mapping[UUID, Package]) -> str:
    """`Finance / Budget` — names from the root down."""
    names = [package.name]
    current = package
    for _ in range(MAX_PACKAGE_DEPTH):
        if current.parent_id is None:
            break
        parent = packages.get(current.parent_id)
        if parent is None:
            break
        names.append(parent.name)
        current = parent
    return PATH_SEPARATOR.join(reversed(names))


def descendants_of(package_id: UUID, packages: Mapping[UUID, Package]) -> frozenset[UUID]:
    """Every package below this one, at any depth."""
    children: dict[UUID, list[UUID]] = {}
    for package in packages.values():
        if package.parent_id is not None:
            children.setdefault(package.parent_id, []).append(package.id)
    found: set[UUID] = set()
    frontier = [package_id]
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in found:
                found.add(child)
                frontier.append(child)
    return frozenset(found)
