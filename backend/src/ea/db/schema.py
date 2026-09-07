"""The Neo4j schema, and the statements that bring a database up to it.

Neo4j has no Alembic. What it has instead is a set of constraints and indexes
that are declared with `IF NOT EXISTS`, so re-running the whole list is a no-op.
This module *is* the migration: adding a statement here and shipping it is the
graph equivalent of `alembic revision` — see `docs/adr/0004`.

How the graph is shaped, and why:

* every element is one `:Element` node, with its ArchiMate type as the
  `element_type` property rather than as a label. An indexed property filters
  exactly as fast as a label, and it keeps 61 labels — and the dynamic Cypher
  needed to write them — out of the codebase;
* every relationship carries its ArchiMate type as the real Neo4j relationship
  type, because that *is* what a traversal pattern-matches on. There are eleven
  of them and they are a closed set, so they are listed literally below;
* user-defined attributes are stored flat under a `p_` prefix, so
  `MATCH (e:Element) WHERE e.p_owner = 'finance'` works without unpacking JSON.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, LiteralString

if TYPE_CHECKING:
    from neo4j import AsyncDriver

#: Prefix isolating user-defined attributes from the fields the model owns.
PROPERTY_PREFIX: Final = "p_"

#: Every relationship type, as a Cypher pattern alternation. Built from literals
#: so no user input can ever reach the shape of a query.
ANY_RELATIONSHIP: Final[LiteralString] = (
    "COMPOSITION|AGGREGATION|ASSIGNMENT|REALIZATION|SERVING|ACCESS"
    "|INFLUENCE|ASSOCIATION|TRIGGERING|FLOW|SPECIALIZATION"
)

SCHEMA_STATEMENTS: Final[tuple[LiteralString, ...]] = (
    # An element is addressed by its id everywhere; a duplicate would split it.
    "CREATE CONSTRAINT element_id_unique IF NOT EXISTS FOR (e:Element) REQUIRE e.id IS UNIQUE",
    # Two applications called "Billing" are a modelling mistake, not a fact.
    "CREATE CONSTRAINT element_name_unique_per_type IF NOT EXISTS "
    "FOR (e:Element) REQUIRE (e.element_type, e.name) IS UNIQUE",
    "CREATE INDEX element_type_index IF NOT EXISTS FOR (e:Element) ON (e.element_type)",
    "CREATE INDEX element_layer_index IF NOT EXISTS FOR (e:Element) ON (e.layer)",
    # TEXT indexes back the `CONTAINS` search of the catalogue endpoint.
    "CREATE TEXT INDEX element_name_text_index IF NOT EXISTS FOR (e:Element) ON (e.name)",
    # One id index per relationship type: Neo4j Community cannot put a
    # uniqueness constraint on a relationship property, so the index is what
    # keeps `MATCH ()-[r]-() WHERE r.id = $id` from scanning the graph.
    "CREATE INDEX rel_composition_id IF NOT EXISTS FOR ()-[r:COMPOSITION]-() ON (r.id)",
    "CREATE INDEX rel_aggregation_id IF NOT EXISTS FOR ()-[r:AGGREGATION]-() ON (r.id)",
    "CREATE INDEX rel_assignment_id IF NOT EXISTS FOR ()-[r:ASSIGNMENT]-() ON (r.id)",
    "CREATE INDEX rel_realization_id IF NOT EXISTS FOR ()-[r:REALIZATION]-() ON (r.id)",
    "CREATE INDEX rel_serving_id IF NOT EXISTS FOR ()-[r:SERVING]-() ON (r.id)",
    "CREATE INDEX rel_access_id IF NOT EXISTS FOR ()-[r:ACCESS]-() ON (r.id)",
    "CREATE INDEX rel_influence_id IF NOT EXISTS FOR ()-[r:INFLUENCE]-() ON (r.id)",
    "CREATE INDEX rel_association_id IF NOT EXISTS FOR ()-[r:ASSOCIATION]-() ON (r.id)",
    "CREATE INDEX rel_triggering_id IF NOT EXISTS FOR ()-[r:TRIGGERING]-() ON (r.id)",
    "CREATE INDEX rel_flow_id IF NOT EXISTS FOR ()-[r:FLOW]-() ON (r.id)",
    "CREATE INDEX rel_specialization_id IF NOT EXISTS FOR ()-[r:SPECIALIZATION]-() ON (r.id)",
)


async def apply_schema(driver: AsyncDriver, *, database: str) -> None:
    """Bring `database` up to `SCHEMA_STATEMENTS`. Safe to run on every boot."""
    for statement in SCHEMA_STATEMENTS:
        await driver.execute_query(statement, database_=database)
