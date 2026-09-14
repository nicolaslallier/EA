"""The PostgreSQL implementation of `ArchitectureRepository` and `IpamRepository`.

The graph is two tables (docs/adr/0033): an element is a row of `elements`, a
relationship a row of `relationships` whose two ends are foreign keys, so
deleting an element takes its links — and, since revision 0006, its documents
and its boxes on diagrams — by DDL rather than by code.

It takes the session factory, like the document and diagram stores, and opens
one unit of work per call. Every value is bound, the depth of a traversal
included: SQLAlchemy constructs compile to parameters.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import ColumnElement, delete, func, or_, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    DomainError,
    DuplicateElementError,
    DuplicateNetworkError,
    ElementNotFoundError,
)
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, read_vrf
from ea.domain.model import Element, Relationship

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.ports import ElementFilter

logger = logging.getLogger(__name__)

#: A traversal deeper than this is a full-graph dump wearing a filter.
MAX_TRAVERSAL_DEPTH: Final = 10

#: The three uniqueness rules an element can break, by the name the server gives.
NAME_TAKEN: Final = "uq_elements_element_type_name"
ADDRESS_TAKEN: Final = "uq_elements_vrf_ip_address"
PREFIX_TAKEN: Final = "uq_elements_vrf_cidr"

#: A relationship insert is refused by a foreign key only when an end is missing.
_MISSING_END_PREFIX: Final = "fk_relationships_"


# --------------------------------------------------------------------------
# Mapping between the domain entities and their rows
# --------------------------------------------------------------------------


def element_row(element: Element) -> dict[str, Any]:
    """Every column of an element. Complete, so a save replaces the whole row."""
    return {
        "id": element.id,
        "element_type": element.element_type.value,
        "layer": element.element_type.layer.value,
        "aspect": element.element_type.aspect.value,
        "name": element.name,
        "description": element.description,
        "documentation": element.documentation,
        "properties": dict(element.properties),
        "created_at": element.created_at,
        "updated_at": element.updated_at,
    }


def element_from_row(row: ElementRecord) -> Element:
    return Element(
        id=row.id,
        element_type=ElementType(row.element_type),
        name=row.name,
        created_at=row.created_at,
        updated_at=row.updated_at,
        description=row.description,
        documentation=row.documentation,
        properties=dict(row.properties),
    )


def relationship_row(relationship: Relationship) -> dict[str, Any]:
    return {
        "id": relationship.id,
        "relationship_type": relationship.relationship_type.value,
        "source_id": relationship.source_id,
        "target_id": relationship.target_id,
        "source_type": relationship.source_type.value,
        "target_type": relationship.target_type.value,
        "name": relationship.name,
        "access_type": relationship.access_type.value if relationship.access_type else None,
        "directed": relationship.directed,
        "properties": dict(relationship.properties),
        "created_at": relationship.created_at,
    }


def relationship_from_row(row: RelationshipRecord) -> Relationship:
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


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def constraint_of(error: IntegrityError) -> str | None:
    """The name of the constraint that refused a write, as the server gave it.

    asyncpg carries it on its own exception; SQLAlchemy wraps that exception in
    a DBAPI adapter (`error.orig`), whose `__cause__` it is.
    """
    cause = getattr(error.orig, "__cause__", None)
    return cast("str | None", getattr(cause, "constraint_name", None))


def rejected(element: Element, constraint: str | None) -> DomainError | None:
    """The refusal a caller must be handed for this constraint, or `None`.

    `None` for a constraint this module does not know: an unexpected refusal is
    re-raised as it is and ends as a typed 500, never as a guess.
    """
    scope = read_vrf(element.properties)
    if constraint == ADDRESS_TAKEN:
        address = element.properties.get(ADDRESS_PROPERTY)
        return AddressAlreadyAssignedError(
            f"{address} is already assigned to another element in VRF {scope!r}"
        )
    if constraint == PREFIX_TAKEN:
        cidr = element.properties.get(PREFIX_PROPERTY)
        return DuplicateNetworkError(f"{cidr} is already declared in VRF {scope!r}")
    if constraint == NAME_TAKEN:
        return DuplicateElementError(
            f"an element of type {element.element_type.value} is already named {element.name!r}"
        )
    return None


async def _flush_refusing(session: AsyncSession, element: Element) -> None:
    try:
        await session.flush()
    except IntegrityError as error:
        refusal = rejected(element, constraint_of(error))
        if refusal is None:
            raise
        logger.info("element rejected by a uniqueness constraint", exc_info=error)
        raise refusal from error


def _rows_affected(result: object) -> int:
    return cast("CursorResult[Any]", result).rowcount


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def _matching(criteria: ElementFilter) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = []
    if criteria.element_types:
        clauses.append(ElementRecord.element_type.in_([t.value for t in criteria.element_types]))
    if criteria.layers:
        clauses.append(ElementRecord.layer.in_([layer.value for layer in criteria.layers]))
    if criteria.search:
        # `strpos` and not `ILIKE`: `%` and `_` typed in the search box stay
        # characters, as they were under Cypher's `CONTAINS`.
        clauses.append(func.strpos(func.lower(ElementRecord.name), func.lower(criteria.search)) > 0)
    return clauses


def _of_types(types: Sequence[RelationshipType]) -> list[ColumnElement[bool]]:
    if not types:
        return []
    return [RelationshipRecord.relationship_type.in_([t.value for t in types])]


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------


class PostgresArchitectureRepository:
    """`ArchitectureRepository` and `IpamRepository` over two tables."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # --- Elements ---------------------------------------------------------

    async def add_element(self, element: Element) -> Element:
        async with self._sessions.begin() as session:
            session.add(ElementRecord(**element_row(element)))
            await _flush_refusing(session, element)
        return element

    async def get_element(self, element_id: UUID) -> Element | None:
        async with self._sessions() as session:
            row = await session.get(ElementRecord, element_id)
            return element_from_row(row) if row is not None else None

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord)
            .where(*_matching(criteria))
            .order_by(ElementRecord.name, ElementRecord.id)
            .offset(criteria.offset)
            .limit(criteria.limit)
        )
        async with self._sessions() as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def count_elements(self, criteria: ElementFilter) -> int:
        statement = select(func.count()).select_from(ElementRecord).where(*_matching(criteria))
        async with self._sessions() as session:
            return int((await session.execute(statement)).scalar_one())

    async def save_element(self, element: Element) -> Element:
        async with self._sessions.begin() as session:
            row = await session.get(ElementRecord, element.id)
            if row is None:
                msg = f"no element with id {element.id}"
                raise ElementNotFoundError(msg)
            for column, value in element_row(element).items():
                setattr(row, column, value)
            await _flush_refusing(session, element)
        return element

    async def delete_element(self, element_id: UUID) -> bool:
        """Its relationships follow by the foreign keys."""
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementRecord).where(ElementRecord.id == element_id)
            )
        return bool(_rows_affected(deleted))

    # --- Relationships ----------------------------------------------------

    async def add_relationship(self, relationship: Relationship) -> Relationship:
        async with self._sessions.begin() as session:
            session.add(RelationshipRecord(**relationship_row(relationship)))
            try:
                await session.flush()
            except IntegrityError as error:
                if not (constraint_of(error) or "").startswith(_MISSING_END_PREFIX):
                    raise
                msg = (
                    f"cannot link {relationship.source_id} to {relationship.target_id}: "
                    "one of them does not exist"
                )
                raise ElementNotFoundError(msg) from error
        return relationship

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None:
        async with self._sessions() as session:
            row = await session.get(RelationshipRecord, relationship_id)
            return relationship_from_row(row) if row is not None else None

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Relationship, ...]:
        statement = select(RelationshipRecord).where(*_of_types(relationship_types))
        if element_id is not None:
            statement = statement.where(
                or_(
                    RelationshipRecord.source_id == element_id,
                    RelationshipRecord.target_id == element_id,
                )
            )
        statement = (
            statement.order_by(RelationshipRecord.created_at, RelationshipRecord.id)
            .offset(offset)
            .limit(limit)
        )
        async with self._sessions() as session:
            return tuple(relationship_from_row(row) for row in await session.scalars(statement))

    async def delete_relationship(self, relationship_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(RelationshipRecord).where(RelationshipRecord.id == relationship_id)
            )
        return bool(_rows_affected(deleted))
