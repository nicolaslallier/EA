"""The PostgreSQL implementation of the architecture, IPAM and analysis repositories.

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
from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final, cast

from sqlalchemy import (
    ColumnElement,
    Integer,
    Numeric,
    Text,
    and_,
    case,
    delete,
    exists,
    func,
    literal_column,
    not_,
    or_,
    select,
    update,
)
from sqlalchemy import cast as sql_cast
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import aliased

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.db.models.diagram import DiagramNodeRecord
from ea.db.models.document import ElementDocument
from ea.db.models.package import PackageRecord
from ea.db.unit_of_work import is_query_canceled, reading, work
from ea.domain.analytics import (
    MAX_NUMBER_INPUT,
    NUMBER_PATTERN,
    RAW_GROUP_CEILING,
    Dimension,
    DimensionKind,
    RawGroup,
)
from ea.domain.approval import ApprovalState
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.audit import AUDIT_LISTED_IDS, events_for
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    AnalysisTimeoutError,
    DomainError,
    DuplicateElementError,
    DuplicateNetworkError,
    ElementNotFoundError,
    ScopeTooLargeError,
)
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, VRF_PROPERTY, read_vrf
from ea.domain.model import Element, Relationship
from ea.domain.ports import (
    ElementChange,
    ElementFilter,
    ElementGuard,
    ElementRemoval,
    EndsReview,
    GraphView,
    Held,
)
from ea.domain.query import Direction, PropertyOp, PropertyTest, QueryOrder, RelationTest
from ea.repositories.audit_store import write_events
from ea.repositories.conflicts import replayable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    from sqlalchemy.sql.selectable import CTE

    from ea.domain.audit import AuditEvent, AuditOf

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
        "approval_state": element.approval_state.value,
        "locked": element.locked,
        "approved_by_sub": element.approved_by_sub,
        "approved_at": element.approved_at,
        "revision": element.revision,
        "last_modified_by_sub": element.last_modified_by_sub,
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
        package_id=row.package_id,
        approval_state=ApprovalState(row.approval_state),
        locked=row.locked,
        approved_by_sub=row.approved_by_sub,
        approved_at=row.approved_at,
        revision=row.revision,
        last_modified_by_sub=row.last_modified_by_sub,
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
        constraint = constraint_of(error)
        refusal = rejected(element, constraint)
        if refusal is None:
            raise
        # No traceback: the refusal is anticipated and answered as a 409, and
        # the constraint's name is the whole story.
        logger.info(
            "element rejected by a uniqueness constraint",
            extra={"constraint": constraint, "element_type": element.element_type.value},
        )
        raise refusal from error


def _rows_affected(result: object) -> int:
    return cast("CursorResult[Any]", result).rowcount


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------


def _subtree(package_id: UUID) -> CTE:
    """The package and every package below it, by a recursive CTE on `parent_id`.

    The id is a bound parameter; the depth is bounded by `MAX_PACKAGE_DEPTH`
    on write, so the walk always ends.
    """
    tree = (
        select(PackageRecord.id.label("id"))
        .where(PackageRecord.id == package_id)
        .cte("subtree", recursive=True)
    )
    below = select(PackageRecord.id).join(tree, PackageRecord.parent_id == tree.c.id)
    return tree.union(below)


def _property_clause(test: PropertyTest) -> ColumnElement[bool]:
    """One test on `properties`. The key and the value are bound parameters, never text."""
    if test.op is PropertyOp.EXISTS:
        return _has(test.key)
    if test.op is PropertyOp.ABSENT:
        return not_(_has(test.key))
    value = test.value or ""
    if test.op is PropertyOp.EQ:
        return _text_of(test.key) == value
    # `strpos` like the name search: `%` and `_` are characters, not wildcards.
    return func.strpos(func.lower(_text_of(test.key)), func.lower(value)) > 0


def _relation_clause(test: RelationTest) -> ColumnElement[bool]:
    """A correlated `EXISTS` on `relationships`: the element has — or has not — such a link."""
    link = aliased(RelationshipRecord)
    far = test.other_type.value if test.other_type is not None else None
    leaving = and_(
        link.source_id == ElementRecord.id,
        *([link.target_type == far] if far is not None else []),
    )
    arriving = and_(
        link.target_id == ElementRecord.id,
        *([link.source_type == far] if far is not None else []),
    )
    side = {
        Direction.OUT: leaving,
        Direction.IN: arriving,
        Direction.ANY: or_(leaving, arriving),
    }[test.direction]
    found = exists().where(link.relationship_type == test.relationship_type.value, side)
    return found if test.present else not_(found)


def _ordering(order: QueryOrder) -> tuple[Any, ...]:
    """The id breaks every tie, so two pages of one query never share a row."""
    if order is QueryOrder.CREATED_AT:
        return (ElementRecord.created_at.desc(), ElementRecord.id)
    if order is QueryOrder.UPDATED_AT:
        return (ElementRecord.updated_at.desc(), ElementRecord.id)
    return (ElementRecord.name, ElementRecord.id)


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
    if criteria.root_only:
        clauses.append(ElementRecord.package_id.is_(None))
    if criteria.package_id is not None:
        if criteria.include_subpackages:
            clauses.append(ElementRecord.package_id.in_(select(_subtree(criteria.package_id).c.id)))
        else:
            clauses.append(ElementRecord.package_id == criteria.package_id)
    clauses.extend(_property_clause(test) for test in criteria.properties)
    clauses.extend(_relation_clause(test) for test in criteria.relations)
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


#: A read-only analysis that runs longer than this is cancelled by PostgreSQL,
#: local to its transaction. Its cancellation surfaces as a 500 `internal_error`
#: with the traceback in the logs (docs/adr/0058).
ANALYSIS_TIMEOUT_MS: Final = 5000

#: What `read_number` tolerates, spelt for PostgreSQL: blanks (space, U+00A0, U+202F)
#: dropped, `,` read as `.`, then a plain decimal. Both are bound parameters.
_BLANKS: Final = "[ \u00a0\u202f]"
_NUMBER: Final = NUMBER_PATTERN


def _raw_key(dimension: Dimension) -> Any:
    match dimension.kind:
        case DimensionKind.ELEMENT_TYPE:
            return ElementRecord.element_type
        case DimensionKind.LAYER:
            return ElementRecord.layer
        case DimensionKind.PACKAGE:
            return func.coalesce(sql_cast(ElementRecord.package_id, Text), "")
        case DimensionKind.PROPERTY:
            return func.coalesce(_text_of(dimension.key or ""), "")


def _number_of(key: str) -> tuple[ColumnElement[bool], ColumnElement[bool], ColumnElement[Any]]:
    """(readable, present, the number) for a property read as a decimal.

    The cast sits behind the `CASE`, so an unreadable value is counted as such
    instead of aborting the whole query with `invalid input syntax`.
    """
    raw = func.coalesce(_text_of(key), "")
    normalised = func.replace(func.regexp_replace(raw, _BLANKS, "", "g"), ",", ".")
    # Judged on what the python reference judges: a bounded length, then a bounded figure.
    readable = and_(
        func.length(raw) <= MAX_NUMBER_INPUT,
        normalised.op("~", is_comparison=True)(_NUMBER),
    )
    present = func.btrim(raw) != ""
    number = case((readable, sql_cast(normalised, Numeric)), else_=None)
    return readable, present, number


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


async def _removal_of(session: AsyncSession, row: ElementRecord) -> ElementRemoval:
    """What deleting this (locked) element takes along, in the caller's transaction."""
    link = RelationshipRecord
    touching = or_(link.source_id == row.id, link.target_id == row.id)
    links = await session.scalars(
        select(link).where(touching).order_by(link.created_at, link.id).limit(AUDIT_LISTED_IDS)
    )
    return ElementRemoval(
        element=element_from_row(row),
        relationship_count=int(await session.scalar(select(func.count()).where(touching)) or 0),
        relationships=tuple(relationship_from_row(found) for found in links),
        documents=await _held(session, ElementDocument.id, ElementDocument.element_id == row.id),
        diagrams=await _held(
            session, DiagramNodeRecord.diagram_id, DiagramNodeRecord.element_id == row.id
        ),
    )


async def _held(session: AsyncSession, column: Any, where: ColumnElement[bool]) -> Held:
    """The exact count of rows matching `where`, and the first few of `column`."""
    total = await session.scalar(select(func.count()).where(where))
    ids = await session.scalars(
        select(column).where(where).order_by(column).limit(AUDIT_LISTED_IDS)
    )
    return Held(count=int(total or 0), ids=tuple(ids))


#: The review states an element can be changed in; every other value is frozen.
_EDITABLE_STATES: Final = ("draft", "in_review")


def frozen_clause() -> ColumnElement[bool]:
    """Approved, locked, or in a state this code does not know — fail closed.

    The same test as `ea.domain.approval.is_frozen`, spelled for SQL: "not draft
    and not in review" rather than "approved", so a value outside the enumeration
    protects instead of exposing.
    """
    return or_(
        ElementRecord.locked.is_(True), ElementRecord.approval_state.not_in(_EDITABLE_STATES)
    )


#: The columns a review or a touch changes.
_REVIEW_COLUMNS: Final = frozenset(
    {
        "approval_state",
        "locked",
        "approved_by_sub",
        "approved_at",
        "revision",
        "last_modified_by_sub",
    }
)


def author_of(events: Sequence[AuditEvent]) -> str | None:
    """The subject that did a write: the actor of the events it writes, set by the service."""
    return events[0].actor_sub if events else None


#: How many element rows one locking statement takes. Statements follow each other in
#: id order, so the order holds across chunks.
LOCK_CHUNK: Final = 100


def lock_order(ids: Iterable[UUID]) -> list[UUID]:
    """THE order in which element rows are locked, everywhere: ascending ids, each once.

    Two writers that lock overlapping sets of elements — a link between two of them,
    a batch of filings, the neighbours of a deleted element — queue behind one
    another instead of each holding what the other wants (docs/adr/0048).
    """
    return sorted(set(ids))


async def lock_elements(
    session: AsyncSession,
    ids: Iterable[UUID],
    *,
    key_only: bool,
    after_chunk: Callable[[int], Awaitable[None]] | None = None,
) -> dict[UUID, ElementRecord]:
    """Lock the rows of these elements in `lock_order`, and give back the ones that exist.

    `key_only` takes `FOR NO KEY UPDATE`, which a link insert (a `FOR KEY SHARE` on
    its ends) does not wait for; otherwise `FOR UPDATE`. `after_chunk` is the seam
    a test uses to interleave two writers between two chunks.
    """
    ordered = lock_order(ids)
    rows: dict[UUID, ElementRecord] = {}
    for chunk, start in enumerate(range(0, len(ordered), LOCK_CHUNK)):
        found = await session.scalars(
            select(ElementRecord)
            .where(ElementRecord.id.in_(ordered[start : start + LOCK_CHUNK]))
            .order_by(ElementRecord.id)
            .with_for_update(key_share=key_only)
            .execution_options(populate_existing=True)
        )
        rows |= {row.id: row for row in found}
        if after_chunk is not None:
            await after_chunk(chunk)
    return rows


async def _other_ends(session: AsyncSession, element_id: UUID) -> set[UUID]:
    """The elements this one is linked to (a plain read: the caller locks them)."""
    touching = or_(
        RelationshipRecord.source_id == element_id, RelationshipRecord.target_id == element_id
    )
    found = await session.execute(
        select(RelationshipRecord.source_id, RelationshipRecord.target_id).where(touching)
    )
    return {end for pair in found for end in pair} - {element_id}


async def _review_rows(
    session: AsyncSession,
    rows: dict[UUID, ElementRecord],
    ends: EndsReview,
    author: str | None,
) -> list[ElementChange]:
    """Review the (locked) elements at the ends of a link change, and write what changed.

    Each is a new revision last changed by `author`; `ends.change` raising refuses the
    whole write.
    """
    changes = [
        ElementChange(c.before, replace(c.after, last_modified_by_sub=author))
        for c in ends.apply(element_from_row(row) for row in rows.values())
    ]
    for change in changes:
        _write_review(rows[change.after.id], change.after)
    await session.flush()
    return changes


async def _review_ends(
    session: AsyncSession, end_ids: Sequence[UUID], ends: EndsReview, author: str | None
) -> list[ElementChange]:
    """Lock the two ends of a link and review them. A missing end is the refusal an insert gives."""
    rows = await lock_elements(session, end_ids, key_only=True)
    if rows.keys() != set(end_ids):
        raise ElementNotFoundError("cannot link: an element at one end does not exist")
    return await _review_rows(session, rows, ends, author)


def _write_review(row: ElementRecord, after: Element) -> None:
    """The columns a review or a touch changes — and nothing the element says about itself."""
    for column, value in element_row(after).items():
        if column in _REVIEW_COLUMNS:
            setattr(row, column, value)


async def touch(session: AsyncSession, element_ids: Sequence[UUID], author: str | None) -> None:
    """Record, in the writing transaction, that these elements were changed by `author`.

    A new revision each, and the author the separation of duties looks at. The
    rows are already locked by the caller.
    """
    await session.execute(
        update(ElementRecord)
        .where(ElementRecord.id.in_(list(element_ids)))
        .values(revision=ElementRecord.revision + 1, last_modified_by_sub=author)
    )


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------


class PostgresArchitectureRepository:
    """`ArchitectureRepository` and `IpamRepository` over two tables."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    # --- Elements ---------------------------------------------------------

    @replayable
    async def add_element(self, element: Element, *, audit: AuditOf[Element]) -> Element:
        events = events_for(audit, element)
        element = replace(element, last_modified_by_sub=author_of(events))
        async with work(self._sessions) as session:
            session.add(ElementRecord(**element_row(element)))
            await _flush_refusing(session, element)
            write_events(session, events)
        return element

    async def get_element(self, element_id: UUID) -> Element | None:
        async with reading(self._sessions) as session:
            row = await session.get(ElementRecord, element_id)
            return element_from_row(row) if row is not None else None

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        statement = (
            select(ElementRecord)
            .where(*_matching(criteria))
            .order_by(*_ordering(criteria.order))
            .offset(criteria.offset)
            .limit(criteria.limit)
        )
        async with reading(self._sessions) as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def count_elements(self, criteria: ElementFilter) -> int:
        statement = select(func.count()).select_from(ElementRecord).where(*_matching(criteria))
        async with reading(self._sessions) as session:
            return int((await session.execute(statement)).scalar_one())

    @replayable
    async def update_element(
        self,
        element_id: UUID,
        change: Callable[[Element], Element],
        *,
        audit: AuditOf[ElementChange],
    ) -> Element:
        return await self._change(element_id, change, audit, content=True)

    @replayable
    async def transition_element(
        self,
        element_id: UUID,
        change: Callable[[Element], Element],
        *,
        audit: AuditOf[ElementChange],
    ) -> Element:
        # A transition is not a change to the content: no new revision, no new author.
        return await self._change(element_id, change, audit, content=False)

    async def _change(
        self,
        element_id: UUID,
        change: Callable[[Element], Element],
        audit: AuditOf[ElementChange],
        *,
        content: bool,
    ) -> Element:
        async with work(self._sessions) as session:
            # `FOR UPDATE`: the row the patch is applied to — its content, its review
            # state, its revision, its last author — is the latest committed one, and a
            # concurrent writer or transition waits for our commit.
            row = await session.get(ElementRecord, element_id, with_for_update=True)
            if row is None:
                msg = f"no element with id {element_id}"
                raise ElementNotFoundError(msg)
            before = element_from_row(row)
            after = change(before)
            wanted, stored = element_row(after), element_row(before)
            changed = {
                column: value
                for column, value in wanted.items()
                if column != "updated_at" and stored[column] != value
            }
            if not changed:
                return before
            events = events_for(audit, ElementChange(before, after))
            if content:  # a new revision, by whoever the event says
                after = replace(
                    after, revision=before.revision + 1, last_modified_by_sub=author_of(events)
                )
                changed["revision"] = after.revision
                changed["last_modified_by_sub"] = after.last_modified_by_sub
            for column, value in {**changed, "updated_at": after.updated_at}.items():
                setattr(row, column, value)
            await _flush_refusing(session, after)
            write_events(session, events)
        return after

    @replayable
    async def delete_element(
        self,
        element_id: UUID,
        *,
        audit: AuditOf[ElementRemoval],
        guard: ElementGuard | None = None,
        ends: EndsReview | None = None,
    ) -> bool:
        """Its relationships follow by the foreign keys — and so is a change to their other ends.

        What they take is read here, under the element's lock: a link or a
        document filed on it meanwhile waits on the foreign key, so the list the
        event carries is exactly what the cascade removes. The elements at the
        other ends are locked with it, in `lock_order`, and put to `ends` like the
        ends of a `disconnect`: a locked one refuses the whole deletion.
        """
        async with work(self._sessions) as session:
            neighbours = await _other_ends(session, element_id)
            rows = await lock_elements(session, {element_id, *neighbours}, key_only=False)
            row = rows.get(element_id)
            if row is None:
                return False
            late = await _other_ends(session, element_id) - neighbours  # linked before our lock
            rows |= await lock_elements(session, late, key_only=False)
            if guard is not None:
                guard(element_from_row(row))
            removal = await _removal_of(session, row)
            events = events_for(audit, removal)
            reviewed = (
                await _review_rows(
                    session,
                    {i: rows[i] for i in neighbours | late if i in rows},
                    ends,
                    author_of(events),
                )
                if ends is not None
                else []
            )
            await session.execute(delete(ElementRecord).where(ElementRecord.id == element_id))
            write_events(session, events)
            for end in reviewed:
                write_events(session, cast("EndsReview", ends).audit(end))
        return True

    # --- Relationships ----------------------------------------------------

    @replayable
    async def add_relationship(
        self,
        relationship: Relationship,
        *,
        audit: AuditOf[Relationship],
        ends: EndsReview | None = None,
    ) -> Relationship:
        events = events_for(audit, relationship)
        async with work(self._sessions) as session:
            reviewed = (
                await _review_ends(
                    session,
                    (relationship.source_id, relationship.target_id),
                    ends,
                    author_of(events),
                )
                if ends is not None
                else []
            )
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
            write_events(session, events)
            for end in reviewed:
                write_events(session, cast("EndsReview", ends).audit(end))
        return relationship

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None:
        async with reading(self._sessions) as session:
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
        async with reading(self._sessions) as session:
            return tuple(relationship_from_row(row) for row in await session.scalars(statement))

    @replayable
    async def delete_relationship(
        self,
        relationship_id: UUID,
        *,
        audit: AuditOf[Relationship],
        ends: EndsReview | None = None,
    ) -> bool:
        async with work(self._sessions) as session:
            # The ends first, then the link: deleting an element locks its row and
            # then its links, so this is the one order that cannot deadlock.
            found = await session.get(RelationshipRecord, relationship_id)
            if found is None:
                return False
            events = events_for(audit, relationship_from_row(found))
            reviewed = (
                await _review_ends(
                    session, (found.source_id, found.target_id), ends, author_of(events)
                )
                if ends is not None
                else []
            )
            row = await session.get(
                RelationshipRecord, relationship_id, with_for_update=True, populate_existing=True
            )
            if row is None:
                return False
            await session.execute(
                delete(RelationshipRecord).where(RelationshipRecord.id == relationship_id)
            )
            write_events(session, events)
            for end in reviewed:
                write_events(session, cast("EndsReview", ends).audit(end))
        return True

    # --- Traversals -------------------------------------------------------

    async def relations_of(
        self,
        element_id: UUID,
        *,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        async with reading(self._sessions) as session:
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
        async with reading(self._sessions) as session:
            found = await session.scalars(
                select(ElementRecord)
                .where(ElementRecord.id.in_(list(element_ids)))
                .order_by(ElementRecord.name, ElementRecord.id)
            )
            elements = tuple(element_from_row(row) for row in found)
            links = await _links_within(session, [element.id for element in elements], ())
        return GraphView(elements=elements, relationships=links)

    # --- Analysis: the reads behind the matrix and the checks ---------------

    async def elements_by_ids(
        self, element_ids: Sequence[UUID], *, limit: int | None = None
    ) -> tuple[Element, ...]:
        if not element_ids:
            return ()
        statement = (
            select(ElementRecord)
            .where(ElementRecord.id.in_(list(element_ids)))
            .order_by(ElementRecord.name, ElementRecord.id)
        )
        async with reading(self._sessions) as session:
            found = await session.scalars(statement.limit(limit) if limit else statement)
            return tuple(element_from_row(row) for row in found)

    async def links_between(
        self, source_ids: Sequence[UUID], target_ids: Sequence[UUID], *, limit: int | None = None
    ) -> tuple[Relationship, ...]:
        if not source_ids or not target_ids:
            return ()
        statement = (
            select(RelationshipRecord)
            .where(
                RelationshipRecord.source_id.in_(list(source_ids)),
                RelationshipRecord.target_id.in_(list(target_ids)),
            )
            .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
        )
        if limit:
            statement = statement.limit(limit)
        async with reading(self._sessions) as session:
            return tuple(relationship_from_row(row) for row in await session.scalars(statement))

    async def links_touching(
        self, element_ids: Sequence[UUID], *, limit: int | None = None
    ) -> tuple[Relationship, ...]:
        if not element_ids:
            return ()
        ids = list(element_ids)
        statement = (
            select(RelationshipRecord)
            .where(
                or_(RelationshipRecord.source_id.in_(ids), RelationshipRecord.target_id.in_(ids))
            )
            .order_by(RelationshipRecord.created_at, RelationshipRecord.id)
        )
        if limit:
            statement = statement.limit(limit)
        async with reading(self._sessions) as session:
            return tuple(relationship_from_row(row) for row in await session.scalars(statement))

    async def grouped(
        self,
        dimensions: Sequence[Dimension],
        metric_key: str | None,
        *,
        scope: Sequence[UUID] | None = None,
    ) -> tuple[RawGroup, ...]:
        columns: list[Any] = [
            *(_raw_key(dimension).label(f"k{index}") for index, dimension in enumerate(dimensions)),
            func.count().label("n"),
        ]
        if metric_key is not None:
            readable, present, number = _number_of(metric_key)
            columns += [
                func.count().filter(readable).label("valid"),
                func.coalesce(func.sum(number), 0).label("total"),
                func.min(number).label("low"),
                func.max(number).label("high"),
                func.count().filter(and_(present, not_(readable))).label("invalid"),
            ]
        statement = select(*columns)
        if scope is not None:
            statement = statement.where(ElementRecord.id.in_(list(scope)))
        # GROUP BY by ordinal: the same bound parameter would otherwise appear
        # twice under two numbers, and PostgreSQL then refuses the select list.
        statement = statement.group_by(
            *(literal_column(str(position + 1)) for position in range(len(dimensions)))
        ).limit(RAW_GROUP_CEILING + 1)
        async with reading(self._sessions) as session:
            await session.execute(
                select(func.set_config("statement_timeout", str(ANALYSIS_TIMEOUT_MS), True))
            )
            try:
                rows = (await session.execute(statement)).all()
            except DBAPIError as error:
                if not is_query_canceled(error):
                    raise
                msg = "the analysis took too long and was cancelled: narrow the scope"
                raise AnalysisTimeoutError(msg) from error
        if len(rows) > RAW_GROUP_CEILING:
            # Never folded from a cut list: sums over a part would read as the whole.
            msg = (
                f"more than {RAW_GROUP_CEILING} distinct values to group by: "
                "choose a dimension with fewer"
            )
            raise ScopeTooLargeError(msg)
        return tuple(
            RawGroup(
                keys=tuple(str(getattr(row, f"k{index}")) for index in range(len(dimensions))),
                count=int(row.n),
                valid=int(row.valid) if metric_key is not None else 0,
                total=float(row.total) if metric_key is not None else 0.0,
                low=float(row.low) if metric_key is not None and row.low is not None else None,
                high=float(row.high) if metric_key is not None and row.high is not None else None,
                invalid=int(row.invalid) if metric_key is not None else 0,
            )
            for row in rows
        )

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
        async with reading(self._sessions) as session:
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
        async with reading(self._sessions) as session:
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
        async with reading(self._sessions) as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def addressed_elements(self) -> tuple[Element, ...]:
        statement = select(ElementRecord).where(_has(ADDRESS_PROPERTY)).order_by(ElementRecord.name)
        async with reading(self._sessions) as session:
            return tuple(element_from_row(row) for row in await session.scalars(statement))

    async def element_at(self, address: str, *, vrf: str) -> Element | None:
        """At most one row: `uq_elements_vrf_ip_address` says so, and answers it."""
        statement = select(ElementRecord).where(
            _has(VRF_PROPERTY),
            _has(ADDRESS_PROPERTY),
            _text_of(VRF_PROPERTY) == vrf,
            _text_of(ADDRESS_PROPERTY) == address,
        )
        async with reading(self._sessions) as session:
            row = (await session.scalars(statement)).first()
            return element_from_row(row) if row is not None else None
