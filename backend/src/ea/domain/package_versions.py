"""What publishing a package freezes, and how its version number moves (docs/adr/0074).

A published package freezes its whole subtree: the freeze is *derived* from the
rows of `package_versions` that are in force, never copied onto the elements, so
there is one truth to read and nothing to restore on reopening. The database
refuses the writes (triggers, docs/adr/0074); this module is the pure half: the
number, the words the caller must give, who is offered what.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Final
from uuid import UUID

from ea.domain.auth import Caller
from ea.domain.errors import AdrRequiredError, InvalidPublicationError
from ea.domain.packages import MAX_PACKAGE_DEPTH, Package, path_of

MAX_NOTES_LENGTH: Final = 4000
MAX_ADR_LENGTH: Final = 200
MAX_REASON_LENGTH: Final = 500
FIRST_VERSION: Final = "v1.0.0"

_VERSION: Final = re.compile(r"v(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})")


class Bump(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"


def next_version(last: str | None, bump: Bump) -> str:
    """The version that follows `last` (the latest ever published, reopened or not)."""
    if last is None:
        return FIRST_VERSION
    found = _VERSION.fullmatch(last)
    if found is None:
        msg = "the last version of this package does not read as vMAJOR.MINOR.PATCH"
        raise InvalidPublicationError(msg)
    major, minor, patch = (int(part) for part in found.groups())
    if bump is Bump.MAJOR:
        return f"v{major + 1}.0.0"
    if bump is Bump.MINOR:
        return f"v{major}.{minor + 1}.0"
    return f"v{major}.{minor}.{patch + 1}"


def _clean(text: str, what: str, limit: int) -> str:
    # The message names the field and the rule, never the value.
    if "\x00" in text:
        msg = f"{what} cannot contain a NUL character"
        raise InvalidPublicationError(msg)
    cleaned = text.strip()
    if not cleaned:
        msg = f"{what} cannot be blank"
        raise InvalidPublicationError(msg)
    if len(cleaned) > limit:
        msg = f"{what} is at most {limit} characters"
        raise InvalidPublicationError(msg)
    return cleaned


@dataclass(frozen=True, slots=True)
class Publication:
    bump: Bump
    notes: str
    adr: str | None


def publication(*, bump: Bump, notes: str, adr: str | None) -> Publication:
    """What a caller must say to publish: notes always, an ADR for a MAJOR."""
    cleaned_notes = _clean(notes, "the release notes", MAX_NOTES_LENGTH)
    cleaned_adr = (
        _clean(adr, "the ADR reference", MAX_ADR_LENGTH)
        if adr is not None and adr.strip()
        else None
    )
    if adr is not None and "\x00" in adr:
        msg = "the ADR reference cannot contain a NUL character"
        raise InvalidPublicationError(msg)
    if bump is Bump.MAJOR and cleaned_adr is None:
        msg = "a MAJOR version names the ADR that decides it"
        raise AdrRequiredError(msg)
    return Publication(bump=bump, notes=cleaned_notes, adr=cleaned_adr)


def reopening_reason(reason: str) -> str:
    return _clean(reason, "the reason", MAX_REASON_LENGTH)


def digest(text: str) -> str:
    """What the audit trail keeps of a text: its SHA-256, never the text (docs/adr/0046)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class FrozenBy:
    """The package whose publication freezes another (or itself)."""

    package_id: UUID
    path: str
    version: str


@dataclass(frozen=True, slots=True)
class FrozenRefusal:
    """What the database said when it refused a write: ids and words, nothing of the content.

    Every field is `None` when the refusal's detail could not be read: the write is
    refused all the same (fail closed).
    """

    package_id: UUID | None
    version: str | None
    table: str | None
    operation: str | None


@dataclass(frozen=True, slots=True)
class PackageVersion:
    id: UUID
    package_id: UUID
    version: str
    bump: Bump
    notes: str
    adr: str | None
    baseline_id: UUID
    element_count: int
    relationship_count: int
    box_count: int
    published_by_sub: str
    published_by: str
    published_at: datetime
    publish_audit_event_id: UUID | None = None
    reopened_by_sub: str | None = None
    reopened_by: str | None = None
    reopened_at: datetime | None = None
    reopen_reason: str | None = None
    reopen_audit_event_id: UUID | None = None

    @property
    def in_force(self) -> bool:
        return self.reopened_at is None


@dataclass(frozen=True, slots=True)
class PublicationPreview:
    """What publishing would do now: the number and the size of what it would freeze."""

    version: str
    bump: Bump
    elements: int
    relationships: int
    boxes: int
    diagrams: int


@dataclass(frozen=True, slots=True)
class PublishOutcome:
    preview: PublicationPreview
    published: PackageVersion | None


def effective_freezes(
    packages: Mapping[UUID, Package], in_force: Mapping[UUID, str]
) -> dict[UUID, FrozenBy]:
    """For each package that is published or lies under one: the nearest publication."""
    found: dict[UUID, FrozenBy] = {}
    for package in packages.values():
        node: Package | None = package
        for _ in range(MAX_PACKAGE_DEPTH + 1):
            if node is None:
                break
            if node.id in in_force:
                found[package.id] = FrozenBy(node.id, path_of(node, packages), in_force[node.id])
                break
            node = packages.get(node.parent_id) if node.parent_id is not None else None
    return found


def allowed_actions(
    caller: Caller, *, frozen_by: FrozenBy | None, own_version: str | None
) -> tuple[str, ...]:
    """The gestures to offer: one per button, decided here and drawn by the SPA."""
    if not caller.can_approve:
        return ()
    if own_version is not None:
        return ("reopen",)
    if frozen_by is not None:
        return ()
    return ("publish",)
