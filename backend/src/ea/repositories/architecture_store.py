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

from sqlalchemy import (
    ColumnElement,
    Integer,
    Text,
    and_,
    case,
    delete,
    func,
    literal_column,
    not_,
    or_,
    select,
)
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
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, VRF_PROPERTY, read_vrf
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementFilter, GraphView

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.sql.selectable import CTE

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
        # characters, never wildcards.
        clauses.append(func.strpos(func.lower(ElementRecord.name), func.lower(criteria.search)) > 0)
    return clauses


def _of_types(types: Sequence[RelationshipType]) -> list[ColumnElement[bool]]:
    if not types:
        return []
    return [RelationshipRecord.relationship_type.in_([t.value for t in types])]


def _has(key: str) -> ColumnElement[bool]:
    """`properties ? key` — the predicate of the two partial unique indexes."""
    return ElementRecord.properties.op("?", is_comparison=True)(key)


def _text_of(key: str) -> ColumnElement[str]:
    """`properties ->> key`, the expression the two unique indexes are built on."""
    return ElementRecord.properties.op("->>", return_type=Text)(key)


#: The relationships along which dependency runs source → target; every other
#: type carries it target → source. Read from the metamodel, never restated.
ALONG_THE_ARROW: Final = tuple(
    relationship.value for relationship in RelationshipType if relationship.impact_follows_direction
)

#: Relationships that build the containment tree, and so must stay acyclic.
_CONTAINMENT: Final = (RelationshipType.COMPOSITION.value, RelationshipType.AGGREGATION.value)


def _clamp_depth(depth: int) -> int:
    return max(1, min(depth, MAX_TRAVERSAL_DEPTH))


def _seed(element_id: UUID) -> CTE:
    """The start of a walk: the element itself at zero hops, or nothing if absent."""
    return (
        select(ElementRecord.id.label("id"), literal_column("0", Integer).label("hops"))
        .where(ElementRecord.id == element_id)
        .cte("reached", recursive=True)
    )


def _neighbourhood_walk(element_id: UUID, depth: int, types: Sequence[RelationshipType]) -> CTE:
    """Every element within `depth` hops, whichever way each link points."""
    reached = _seed(element_id)
    link = RelationshipRecord
    step = (
        select(
            case((link.source_id == reached.c.id, link.target_id), else_=link.source_id).label(
                "id"
            ),
            (reached.c.hops + 1).label("hops"),
        )
        .select_from(reached)
        .join(link, or_(link.source_id == reached.c.id, link.target_id == reached.c.id))
        .where(reached.c.hops < depth, *_of_types(types))
    )
    return reached.union(step)


def _impact_walk(element_id: UUID, depth: int, types: Sequence[RelationshipType]) -> CTE:
    """Everything that depends on an element, each hop taken the way dependency runs.

    A hop along a type of `ALONG_THE_ARROW` leaves from its source; any other
    leaves from its target. That lets one walk mix both — forwards for
    `serving`, backwards for `composition` — without answering a different
    question halfway through.
    """
    reached = _seed(element_id)
    link = RelationshipRecord
    along = link.relationship_type.in_(ALONG_THE_ARROW)
    step = (
        select(
            case((along, link.target_id), else_=link.source_id).label("id"),
            (reached.c.hops + 1).label("hops"),
        )
        .select_from(reached)
        .join(
            link,
            or_(
                and_(along, link.source_id == reached.c.id),
                and_(not_(along), link.target_id == reached.c.id),
            ),
        )
        .where(reached.c.hops < depth, *_of_types(types))
    )
    return reached.union(step)


async def _links_within(
    session: AsyncSession, ids: Sequence[UUID], types: Sequence[RelationshipType]
) -> tuple[Relationship, ...]:
    """The relationships whose two ends are both among `ids`: a drawable sub-graph."""
    if not ids:
        return ()
    statement = (
        select(RelationshipRecord)
        .where(
            RelationshipRecord.source_id.in_(ids),
            RelationshipRecord.target_id.in_(ids),
            *_of_types(types),
        )
        .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
    )
    return tuple(relationship_from_row(row) for row in await session.scalars(statement))


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

    # --- Traversals -------------------------------------------------------

    async def relations_of(
        self,
        element_id: UUID,
        *,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        async with self._sessions() as session:
            row = await session.get(ElementRecord, element_id)
            if row is None:
                return GraphView(elements=(), relationships=())
            statement = (
                select(RelationshipRecord)
                .where(
                    or_(
                        RelationshipRecord.source_id == element_id,
                        RelationshipRecord.target_id == element_id,
                    ),
                    *_of_types(relationship_types),
                )
                .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
            )
            links = tuple(relationship_from_row(link) for link in await session.scalars(statement))
            # A self-association names the element at both ends: it is listed once, first.
            others = {end for link in links for end in (link.source_id, link.target_id)}
            others.discard(element_id)
            around: tuple[Element, ...] = ()
            if others:
                found = await session.scalars(
                    select(ElementRecord)
                    .where(ElementRecord.id.in_(list(others)))
                    .order_by(ElementRecord.name, ElementRecord.id)
                )
                around = tuple(element_from_row(other) for other in found)
        return GraphView(elements=(element_from_row(row), *around), relationships=links)

    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        walk = _neighbourhood_walk(element_id, _clamp_depth(depth), relationship_types)
        return await self._view_of_walk(walk, relationship_types)

    async def impacted_by(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        walk = _impact_walk(element_id, _clamp_depth(depth), relationship_types)
        return await self._view_of_walk(walk, relationship_types)

    async def view_of(self, element_ids: Sequence[UUID]) -> GraphView:
        if not element_ids:
            return GraphView(elements=(), relationships=())
        async with self._sessions() as session:
            found = await session.scalars(
                select(ElementRecord)
                .where(ElementRecord.id.in_(list(element_ids)))
                .order_by(ElementRecord.name, ElementRecord.id)
            )
            elements = tuple(element_from_row(row) for row in found)
            links = await _links_within(session, [element.id for element in elements], ())
        return GraphView(elements=elements, relationships=links)

    async def would_close_a_containment_cycle(self, source_id: UUID, target_id: UUID) -> bool:
        """Whether `source` is already contained, at any depth, under `target`.

        The walk has no depth: it stops because `UNION` discards a row it has
        already produced, so a cycle already in the data cannot loop it.
        """
        if source_id == target_id:
            return True
        below = (
            select(ElementRecord.id.label("id"))
            .where(ElementRecord.id == target_id)
            .cte("below", recursive=True)
        )
        below = below.union(
            select(RelationshipRecord.target_id.label("id"))
            .select_from(below)
            .join(RelationshipRecord, RelationshipRecord.source_id == below.c.id)
            .where(RelationshipRecord.relationship_type.in_(_CONTAINMENT))
        )
        statement = select(func.count()).select_from(below).where(below.c.id == source_id)
        async with self._sessions() as session:
            return bool((await session.execute(statement)).scalar_one())

    async def _view_of_walk(
        self, walk: CTE, relationship_types: Sequence[RelationshipType]
    ) -> GraphView:
        """The elements a walk reached, nearest first, and the links between them."""
        statement = (
            select(ElementRecord)
            .join(walk, walk.c.id == ElementRecord.id)
            .group_by(ElementRecord.id)
            .order_by(func.min(walk.c.hops), ElementRecord.name, ElementRecord.id)
        )
        async with self._sessions() as session:
            elements = tuple(element_from_row(row) for row in await session.scalars(statement))
            links = await _links_within(
                session, [element.id for element in elements], relationship_types
            )
        return GraphView(elements=elements, relationships=links)

    # --- IP address management (docs/adr/0020) ----------------------------
    # Deliberately unbounded: an occupancy figure computed from a page of the
    # inventory would be wrong, silently, and the inventory is bounded by how
    # many machines are modelled rather than by how large the graph is.

    async def networks(self) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord)
            .where(_has(PREFIX_PROPERTY))
            .order_by(_text_of(PREFIX_PROPERTY), ElementRecord.name)
        )
        async with self._sessions() as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def addressed_elements(self) -> tuple[Element, ...]:
        statement = select(ElementRecord).where(_has(ADDRESS_PROPERTY)).order_by(ElementRecord.name)
        async with self._sessions() as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def element_at(self, address: str, *, vrf: str) -> Element | None:
        """At most one row: `uq_elements_vrf_ip_address` says so, and answers it."""
        statement = select(ElementRecord).where(
            _has(VRF_PROPERTY),
            _has(ADDRESS_PROPERTY),
            _text_of(VRF_PROPERTY) == vrf,
            _text_of(ADDRESS_PROPERTY) == address,
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).first()
            return element_from_row(row) if row is not None else None
