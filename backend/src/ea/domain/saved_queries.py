"""A saved query: a named `ElementQuery`, shared by every reader of the catalogue.

Shared on purpose (spec §1): there is no private owner, `created_by` only says
who wrote it, and any editor may change it. A query is data, not behaviour — it
names a package by id with no foreign key, so a package that goes away leaves
the query intact and the run says so (`UnknownPackageError`).

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final
from uuid import UUID, uuid4

from ea.domain.query import ElementQuery, check_storable

MAX_SAVED_QUERY_NAME_LENGTH: Final = 200
MAX_SAVED_QUERY_DESCRIPTION: Final = 2000


def _no_nul(value: str, what: str) -> None:
    # PostgreSQL cannot store a NUL in `text`: refused here, as a 422, not at INSERT.
    if "\x00" in value:
        msg = f"a saved query {what} cannot contain a NUL character"
        raise ValueError(msg)


def _clean_name(name: str) -> str:
    _no_nul(name, "name")
    cleaned = name.strip()
    if not cleaned or len(cleaned) > MAX_SAVED_QUERY_NAME_LENGTH:
        msg = f"a saved query name is 1 to {MAX_SAVED_QUERY_NAME_LENGTH} characters"
        raise ValueError(msg)
    return cleaned


def _clean_description(description: str) -> str:
    _no_nul(description, "description")
    cleaned = description.strip()
    if len(cleaned) > MAX_SAVED_QUERY_DESCRIPTION:
        msg = f"a saved query description is at most {MAX_SAVED_QUERY_DESCRIPTION} characters"
        raise ValueError(msg)
    return cleaned


@dataclass(frozen=True, slots=True)
class SavedQuerySummary:
    """What a listing shows: everything but the query, which is never parsed for a list."""

    id: UUID
    name: str
    description: str
    created_by: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SavedQuery:
    id: UUID
    name: str
    description: str
    query: ElementQuery
    created_by: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        name: str,
        query: ElementQuery,
        created_by: str,
        now: datetime,
        description: str = "",
    ) -> SavedQuery:
        check_storable(query)
        return cls(
            id=uuid4(),
            name=_clean_name(name),
            description=_clean_description(description),
            query=query,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )

    def revise(
        self,
        *,
        now: datetime,
        name: str | None = None,
        description: str | None = None,
        query: ElementQuery | None = None,
    ) -> SavedQuery:
        """The same saved query with something changed; an omitted field is kept."""
        if query is not None:
            check_storable(query)
        return replace(
            self,
            name=self.name if name is None else _clean_name(name),
            description=(
                self.description if description is None else _clean_description(description)
            ),
            query=self.query if query is None else query,
            updated_at=now,
        )

    @property
    def summary(self) -> SavedQuerySummary:
        return SavedQuerySummary(
            id=self.id,
            name=self.name,
            description=self.description,
            created_by=self.created_by,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )
