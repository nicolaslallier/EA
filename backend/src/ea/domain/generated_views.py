"""Generated views: a diagram that is "everything that answers this query".

A view stays a view (docs/adr/0069) — it owns no fact. *Live*, it
stores no box at all: who is on it is recomputed at every opening, and its
layout is the SPA's deterministic one. *Frozen*, the layout the SPA computed
is stored and the diagram is edited like a hand-drawn one; the reference to
its source is kept so a refresh can show what changed. The filter itself is
sub-project 1's (`ElementQuery`): nothing here restates it.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final
from uuid import UUID

from ea.domain.errors import GeneratedViewTooLargeError

if TYPE_CHECKING:
    from ea.domain.model import Element

#: Mirrors `api.schemas.MAX_DIAGRAM_NODES` (a test holds the two together): a diagram
#: a person can still read. Past this it is an export, not a view.
MAX_GENERATED_MEMBERS: Final = 500


class Generation(StrEnum):
    LIVE = "live"
    FROZEN = "frozen"


@dataclass(frozen=True, slots=True)
class QuerySource:
    query_id: UUID


@dataclass(frozen=True, slots=True)
class PackageSource:
    package_id: UUID
    recursive: bool = False


type ViewSource = QuerySource | PackageSource


@dataclass(frozen=True, slots=True)
class SourceMembers:
    """What a source answers: the first elements, and how many there are in all."""

    ids: tuple[UUID, ...]
    total: int


@dataclass(frozen=True, slots=True)
class SourceDiff:
    """What a frozen view would gain and lose if it followed its source again."""

    added: tuple[Element, ...]
    removed: tuple[UUID, ...]


def resolve_members(
    found: SourceMembers, *, limit: int = MAX_GENERATED_MEMBERS
) -> tuple[UUID, ...]:
    """The elements of a view, once the source is known to fit on one."""
    if found.total > limit:
        msg = (
            f"the source yields {found.total} elements; a diagram holds at most {limit} — "
            "narrow the saved query or the package it follows, or delete this view"
        )
        raise GeneratedViewTooLargeError(msg)
    return tuple(dict.fromkeys(found.ids))


def diff_members(
    stored: Iterable[UUID], resolved: Iterable[UUID]
) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
    """`(added, removed)`: what the source now holds that the view lacks, and the reverse."""
    before, after = set(stored), set(resolved)
    return tuple(sorted(after - before, key=str)), tuple(sorted(before - after, key=str))
