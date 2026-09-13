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

That last point is what the IP address management is built on: an address is
`p_ip_address` on the element that answers on it and a subnet is `p_cidr` on a
`communication_network`, so the IPAM adds no node kind and no label — see
`docs/adr/0020`. It does add the two statements below that turn a convention
into a guarantee — an address may be claimed, and a prefix declared, once per
routing scope — and the database says so rather than a check that two agents
can both pass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, LiteralString

from neo4j import NotificationMinimumSeverity

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
    # --- IP address management (docs/adr/0020) ---
    # Two elements may not answer on one address inside one routing scope. This
    # is the reason an element holds a single address rather than a list: a
    # composite uniqueness constraint is what survives two agents allocating at
    # the same instant, and it can only see a scalar property. A host with two
    # NICs is two `technology_interface` elements, which is how ArchiMate says
    # to model it anyway. Nodes missing either property are untouched by it,
    # which is why the service always writes the scope alongside the address.
    "CREATE CONSTRAINT element_address_unique_per_vrf IF NOT EXISTS "
    "FOR (e:Element) REQUIRE (e.p_vrf, e.p_ip_address) IS UNIQUE",
    # The inventory asks "every element that has an address" and "every element
    # that declares a prefix"; without these each question is a full scan.
    "CREATE INDEX element_ip_address_index IF NOT EXISTS FOR (e:Element) ON (e.p_ip_address)",
    "CREATE INDEX element_cidr_index IF NOT EXISTS FOR (e:Element) ON (e.p_cidr)",
    # A prefix may be declared once per routing scope, for the reason the
    # address constraint above exists: `IpamService.declare_network` looks
    # before it creates, and two callers can both look. The constraint compares
    # stored strings, which is why every write path stores `p_cidr` in one
    # spelling and writes `p_vrf` beside it (`validate_ipam_properties`).
    #
    # It is last on purpose. Unlike every statement before it, it can fail on a
    # database that already has data: a graph written before it existed may
    # hold one prefix twice in one scope. `CREATE CONSTRAINT` then refuses,
    # `apply_schema` raises, and since that runs at boot the API does not start
    # — last, so that everything above is at least in place. Find the
    # duplicates, then rename or merge them before deploying:
    #
    #   MATCH (e:Element) WHERE e.p_cidr IS NOT NULL AND e.p_vrf IS NOT NULL
    #   WITH e.p_vrf AS vrf, e.p_cidr AS cidr, collect(e.name) AS names
    #   WHERE size(names) > 1
    #   RETURN vrf, cidr, names
    #
    # Rows written before prefixes were stored canonically, or without a
    # `p_vrf`, do not make it fail and are not covered by it either:
    # `MATCH (e:Element) WHERE e.p_cidr IS NOT NULL RETURN e.name, e.p_vrf,
    # e.p_cidr` lists them for a check by eye.
    "CREATE CONSTRAINT element_cidr_unique_per_vrf IF NOT EXISTS "
    "FOR (e:Element) REQUIRE (e.p_vrf, e.p_cidr) IS UNIQUE",
)


async def apply_schema(driver: AsyncDriver, *, database: str) -> None:
    """Bring `database` up to `SCHEMA_STATEMENTS`. Safe to run on every boot.

    The one session is opened asking the server for nothing below a warning.
    `IF NOT EXISTS` makes every boot after the first a no-op, and Neo4j reports
    each no-op as an INFORMATION notification saying the constraint already
    exists; the driver logs one of them per statement at every start. That is the
    designed outcome being announced as news, so the filter is set here — on
    the schema session alone, so a notification about a *query* still surfaces.
    """
    async with driver.session(
        database=database,
        notifications_min_severity=NotificationMinimumSeverity.WARNING,
    ) as session:
        for statement in SCHEMA_STATEMENTS:
            result = await session.run(statement)
            # Consuming is what makes the statement's failure this call's
            # failure: `run` only sends it.
            await result.consume()
