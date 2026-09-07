"""The Neo4j implementation of `ArchitectureRepository`.

Every value that varies at runtime is a bound Cypher parameter. Two things
cannot be parameters in Cypher — a relationship type and the upper bound of a
variable-length path — and both are built here from a closed enum or from an
integer clamped to a small range, never from anything a caller typed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final, LiteralString, cast
from uuid import UUID

from neo4j.exceptions import ConstraintError

from ea.db.schema import ANY_RELATIONSHIP, PROPERTY_PREFIX
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import DuplicateElementError, ElementNotFoundError
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementFilter, GraphView

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from neo4j import AsyncDriver
    from neo4j.graph import Node
    from neo4j.graph import Relationship as Neo4jRelationship

logger = logging.getLogger(__name__)

#: A traversal deeper than this is a full-graph dump wearing a filter.
MAX_TRAVERSAL_DEPTH: Final = 10


# --------------------------------------------------------------------------
# Mapping between the domain entities and their graph form
# --------------------------------------------------------------------------


def _custom_properties(stored: dict[str, Any]) -> dict[str, str]:
    prefix = len(PROPERTY_PREFIX)
    return {
        key[prefix:]: str(value) for key, value in stored.items() if key.startswith(PROPERTY_PREFIX)
    }


def _prefixed(properties: dict[str, str] | Any) -> dict[str, str]:
    return {f"{PROPERTY_PREFIX}{key}": value for key, value in properties.items()}


def _as_datetime(value: Any) -> datetime:
    """Neo4j hands back its own temporal type; the domain speaks `datetime`."""
    return cast("datetime", value.to_native())


def element_to_properties(element: Element) -> dict[str, Any]:
    """The complete property map of an element node.

    Complete on purpose: writes use `SET e = $properties`, which replaces the
    node's properties wholesale, so a removed attribute actually disappears
    instead of lingering from the previous version.
    """
    return {
        "id": str(element.id),
        "element_type": element.element_type.value,
        "layer": element.element_type.layer.value,
        "aspect": element.element_type.aspect.value,
        "name": element.name,
        "description": element.description,
        "documentation": element.documentation,
        "created_at": element.created_at,
        "updated_at": element.updated_at,
        **_prefixed(element.properties),
    }


def element_from_node(node: Node) -> Element:
    stored = dict(node)
    return Element(
        id=UUID(stored["id"]),
        element_type=ElementType(stored["element_type"]),
        name=stored["name"],
        created_at=_as_datetime(stored["created_at"]),
        updated_at=_as_datetime(stored["updated_at"]),
        description=stored.get("description", ""),
        documentation=stored.get("documentation", ""),
        properties=_custom_properties(stored),
    )


def relationship_to_properties(relationship: Relationship) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "id": str(relationship.id),
        "relationship_type": relationship.relationship_type.value,
        "source_id": str(relationship.source_id),
        "target_id": str(relationship.target_id),
        "source_type": relationship.source_type.value,
        "target_type": relationship.target_type.value,
        "created_at": relationship.created_at,
        "name": relationship.name,
        "directed": relationship.directed,
        **_prefixed(relationship.properties),
    }
    if relationship.access_type is not None:
        properties["access_type"] = relationship.access_type.value
    return properties


def relationship_from_edge(edge: Neo4jRelationship) -> Relationship:
    stored = dict(edge)
    access = stored.get("access_type")
    return Relationship(
        id=UUID(stored["id"]),
        relationship_type=RelationshipType(stored["relationship_type"]),
        source_id=UUID(stored["source_id"]),
        target_id=UUID(stored["target_id"]),
        source_type=ElementType(stored["source_type"]),
        target_type=ElementType(stored["target_type"]),
        created_at=_as_datetime(stored["created_at"]),
        name=stored.get("name", ""),
        access_type=AccessType(access) if access else None,
        directed=bool(stored.get("directed", False)),
        properties=_custom_properties(stored),
    )


# --------------------------------------------------------------------------
# Cypher
# --------------------------------------------------------------------------

_ANY: Final[LiteralString] = ANY_RELATIONSHIP

_ELEMENT_PREDICATE: Final[LiteralString] = """
    (size($element_types) = 0 OR e.element_type IN $element_types)
    AND (size($layers) = 0 OR e.layer IN $layers)
    AND ($search IS NULL OR toLower(e.name) CONTAINS toLower($search))
"""

CREATE_ELEMENT: Final[LiteralString] = "CREATE (e:Element) SET e = $properties RETURN e"

GET_ELEMENT: Final[LiteralString] = "MATCH (e:Element {id: $id}) RETURN e"

SAVE_ELEMENT: Final[LiteralString] = "MATCH (e:Element {id: $id}) SET e = $properties RETURN e"

DELETE_ELEMENT: Final[LiteralString] = (
    "MATCH (e:Element {id: $id}) WITH e, 1 AS found DETACH DELETE e RETURN found"
)

LIST_ELEMENTS: Final[LiteralString] = (
    "MATCH (e:Element) WHERE"
    + _ELEMENT_PREDICATE
    + "RETURN e ORDER BY e.name, e.id SKIP $offset LIMIT $limit"
)

COUNT_ELEMENTS: Final[LiteralString] = (
    "MATCH (e:Element) WHERE" + _ELEMENT_PREDICATE + "RETURN count(e) AS total"
)

GET_RELATIONSHIP: Final[LiteralString] = "MATCH ()-[r:" + _ANY + "]->() WHERE r.id = $id RETURN r"

DELETE_RELATIONSHIP: Final[LiteralString] = (
    "MATCH ()-[r:" + _ANY + "]->() WHERE r.id = $id WITH r, 1 AS found DELETE r RETURN found"
)

LIST_RELATIONSHIPS: Final[LiteralString] = (
    "MATCH (s:Element)-[r:" + _ANY + "]->(t:Element) "
    "WHERE ($element_id IS NULL OR s.id = $element_id OR t.id = $element_id) "
    "AND (size($relationship_types) = 0 OR type(r) IN $relationship_types) "
    "RETURN r ORDER BY r.created_at, r.id SKIP $offset LIMIT $limit"
)

#: The links attached to one element, in either direction, with both endpoints.
#: `OPTIONAL MATCH` so an element with no link still returns itself rather than
#: an empty record, which the caller could not tell from a missing element. The
#: element is put back at the head of the list and filtered out of the others,
#: because a self-association would otherwise return it twice.
RELATIONS_OF: Final[LiteralString] = (
    "MATCH (e:Element {id: $id}) "
    "OPTIONAL MATCH (e)-[edge:" + _ANY + "]-(other:Element) "
    "WHERE size($relationship_types) = 0 OR type(edge) IN $relationship_types "
    "WITH e, collect(DISTINCT other) AS others, collect(DISTINCT edge) AS edges "
    "RETURN [e] + [other IN others WHERE other <> e] AS elements, "
    "edges AS relationships"
)

WOULD_CLOSE_A_CONTAINMENT_CYCLE: Final[LiteralString] = """
    MATCH (source:Element {id: $source_id}), (target:Element {id: $target_id})
    RETURN source.id = target.id
        OR EXISTS { (target)-[:COMPOSITION|AGGREGATION*1..]->(source) } AS closes
"""

#: Collect the sub-graph induced by a set of nodes: every relationship whose two
#: endpoints are both inside the scope, so the result is drawable as it stands.
_EDGES_WITHIN_SCOPE: Final[LiteralString] = (
    """
    UNWIND scope AS anchor
    OPTIONAL MATCH (anchor)-[edge:"""
    + _ANY
    + """]->(other:Element)
    WHERE other IN scope
      AND (size($relationship_types) = 0 OR type(edge) IN $relationship_types)
    RETURN scope AS elements, collect(DISTINCT edge) AS relationships
"""
)


def _neighbourhood_query(depth: int) -> LiteralString:
    """Everything within `depth` hops, in either direction."""
    # `depth` is an int clamped to 1..MAX_TRAVERSAL_DEPTH by the caller: Cypher
    # refuses a parameter as the bound of a variable-length pattern, so this is
    # the one place a query is composed rather than parameterised.
    return (
        f"""
        MATCH (start:Element {{id: $id}})
        OPTIONAL MATCH (start)-[hops:{_ANY}*1..{depth}]-(reached:Element)
        WHERE all(
            hop IN hops
            WHERE size($relationship_types) = 0 OR type(hop) IN $relationship_types
        )
        WITH start, collect(DISTINCT reached) AS reached
        UNWIND ([start] + reached) AS node
        WITH collect(DISTINCT node) AS scope
        """
        + _EDGES_WITHIN_SCOPE
    )


def _impact_query(depth: int) -> LiteralString:
    """Everything that depends on an element, walking each hop the way its
    relationship type carries dependency.

    The `CASE` is the whole trick: a path is followed hop by hop, and a hop only
    counts when it is entered from the end the dependency actually flows from —
    forwards for `SERVING`, backwards for `COMPOSITION`. That lets one traversal
    mix both without answering a different question halfway through.
    """
    return (
        f"""
        MATCH (start:Element {{id: $id}})
        OPTIONAL MATCH path = (start)-[hops:{_ANY}*1..{depth}]-(reached:Element)
        WHERE all(
            step IN range(0, size(hops) - 1)
            WHERE (size($relationship_types) = 0 OR type(hops[step]) IN $relationship_types)
              AND CASE WHEN type(hops[step]) IN $along_the_arrow
                       THEN startNode(hops[step]) = nodes(path)[step]
                       ELSE endNode(hops[step]) = nodes(path)[step]
                  END
        )
        WITH start, collect(DISTINCT reached) AS reached
        UNWIND ([start] + reached) AS node
        WITH collect(DISTINCT node) AS scope
        """
        + _EDGES_WITHIN_SCOPE
    )


# --------------------------------------------------------------------------
# Repository
# --------------------------------------------------------------------------


class Neo4jArchitectureRepository:
    """`ArchitectureRepository` over Bolt. Structurally typed — no base class."""

    def __init__(self, driver: AsyncDriver, *, database: str) -> None:
        self._driver = driver
        self._database = database

    async def _run(self, query: LiteralString, parameters: dict[str, Any]) -> list[Any]:
        result = await self._driver.execute_query(query, parameters, database_=self._database)
        return list(result.records)

    # --- Elements ---------------------------------------------------------

    async def add_element(self, element: Element) -> Element:
        try:
            records = await self._run(
                CREATE_ELEMENT, {"properties": element_to_properties(element)}
            )
        except ConstraintError as error:
            # The message names the constraint and the offending value; the
            # caller gets the generic domain error, the detail goes to the log.
            logger.info("element rejected by a uniqueness constraint", exc_info=error)
            msg = (
                f"an element of type {element.element_type.value} is already named {element.name!r}"
            )
            raise DuplicateElementError(msg) from error
        return element_from_node(records[0]["e"])

    async def get_element(self, element_id: UUID) -> Element | None:
        records = await self._run(GET_ELEMENT, {"id": str(element_id)})
        return element_from_node(records[0]["e"]) if records else None

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        records = await self._run(LIST_ELEMENTS, _filter_parameters(criteria))
        return tuple(element_from_node(record["e"]) for record in records)

    async def count_elements(self, criteria: ElementFilter) -> int:
        parameters = _filter_parameters(criteria)
        parameters.pop("limit")
        parameters.pop("offset")
        records = await self._run(COUNT_ELEMENTS, parameters)
        return int(records[0]["total"])

    async def save_element(self, element: Element) -> Element:
        try:
            records = await self._run(
                SAVE_ELEMENT,
                {"id": str(element.id), "properties": element_to_properties(element)},
            )
        except ConstraintError as error:
            logger.info("element update rejected by a constraint", exc_info=error)
            msg = (
                f"an element of type {element.element_type.value} is already named {element.name!r}"
            )
            raise DuplicateElementError(msg) from error
        if not records:
            msg = f"no element with id {element.id}"
            raise ElementNotFoundError(msg)
        return element_from_node(records[0]["e"])

    async def delete_element(self, element_id: UUID) -> bool:
        records = await self._run(DELETE_ELEMENT, {"id": str(element_id)})
        return bool(records)

    # --- Relationships ----------------------------------------------------

    async def add_relationship(self, relationship: Relationship) -> Relationship:
        records = await self._run(
            _create_relationship_query(relationship.relationship_type),
            {
                "source_id": str(relationship.source_id),
                "target_id": str(relationship.target_id),
                "properties": relationship_to_properties(relationship),
            },
        )
        if not records:
            msg = (
                f"cannot link {relationship.source_id} to {relationship.target_id}: "
                "one of them does not exist"
            )
            raise ElementNotFoundError(msg)
        return relationship_from_edge(records[0]["r"])

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None:
        records = await self._run(GET_RELATIONSHIP, {"id": str(relationship_id)})
        return relationship_from_edge(records[0]["r"]) if records else None

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Relationship, ...]:
        records = await self._run(
            LIST_RELATIONSHIPS,
            {
                "element_id": str(element_id) if element_id else None,
                "relationship_types": [t.label for t in relationship_types],
                "limit": limit,
                "offset": offset,
            },
        )
        return tuple(relationship_from_edge(record["r"]) for record in records)

    async def delete_relationship(self, relationship_id: UUID) -> bool:
        records = await self._run(DELETE_RELATIONSHIP, {"id": str(relationship_id)})
        return bool(records)

    # --- Traversals -------------------------------------------------------

    async def relations_of(
        self,
        element_id: UUID,
        *,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        return await self._traverse(RELATIONS_OF, element_id, relationship_types)

    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        return await self._traverse(
            _neighbourhood_query(_clamp_depth(depth)),
            element_id,
            relationship_types,
        )

    async def impacted_by(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        return await self._traverse(
            _impact_query(_clamp_depth(depth)),
            element_id,
            relationship_types,
            along_the_arrow=[
                relationship.label
                for relationship in RelationshipType
                if relationship.impact_follows_direction
            ],
        )

    async def _traverse(
        self,
        query: LiteralString,
        element_id: UUID,
        relationship_types: Sequence[RelationshipType],
        **extra: Any,
    ) -> GraphView:
        records = await self._run(
            query,
            {
                "id": str(element_id),
                "relationship_types": [t.label for t in relationship_types],
                **extra,
            },
        )
        if not records:
            return GraphView(elements=(), relationships=())
        record = records[0]
        return GraphView(
            elements=tuple(element_from_node(node) for node in record["elements"]),
            relationships=tuple(relationship_from_edge(edge) for edge in record["relationships"]),
        )

    async def would_close_a_containment_cycle(self, source_id: UUID, target_id: UUID) -> bool:
        records = await self._run(
            WOULD_CLOSE_A_CONTAINMENT_CYCLE,
            {"source_id": str(source_id), "target_id": str(target_id)},
        )
        return bool(records) and bool(records[0]["closes"])


def _clamp_depth(depth: int) -> int:
    return max(1, min(depth, MAX_TRAVERSAL_DEPTH))


def _filter_parameters(criteria: ElementFilter) -> dict[str, Any]:
    return {
        "element_types": [t.value for t in criteria.element_types],
        "layers": [layer.value for layer in criteria.layers],
        "search": criteria.search or None,
        "limit": criteria.limit,
        "offset": criteria.offset,
    }


def _create_relationship_query(relationship_type: RelationshipType) -> LiteralString:
    """`CREATE (s)-[r:SERVING]->(t)` — Cypher will not take the type as a parameter.

    `label` comes from a member of a closed enum that the request schema has
    already validated, so nothing a caller sends can reach the query text.
    """
    return (
        "MATCH (s:Element {id: $source_id}), (t:Element {id: $target_id}) "
        f"CREATE (s)-[r:{relationship_type.label}]->(t) "
        "SET r = $properties RETURN r"
    )
