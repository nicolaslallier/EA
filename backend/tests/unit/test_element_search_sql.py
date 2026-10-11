"""What the filter compiles to, without a database: values are bound, never spelled."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from ea.db.models.architecture import ElementRecord
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.ports import ElementFilter
from ea.domain.query import (
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
)
from ea.repositories.architecture_store import _matching, _ordering

EVIL = "x'; DROP TABLE elements; --"


def compiled(**query: object) -> tuple[str, dict[str, object]]:
    criteria = ElementFilter.from_query(ElementQuery(**query))  # type: ignore[arg-type]
    statement = select(ElementRecord.id).where(*_matching(criteria))
    result = statement.compile(dialect=postgresql.dialect())
    return str(result), dict(result.params)


def test_property_keys_and_values_are_bound_parameters() -> None:
    tests = (
        PropertyTest("owner", PropertyOp.EQ, EVIL),
        PropertyTest("note", PropertyOp.CONTAINS, "100%"),
        PropertyTest("cidr", PropertyOp.EXISTS),
        PropertyTest("retired", PropertyOp.ABSENT),
    )

    sql, params = compiled(properties=tests, text=EVIL)

    for spelled in (EVIL, "100%", "owner", "note", "cidr", "retired"):
        assert spelled not in sql
        assert spelled in params.values() or spelled.lower() in params.values()


def test_a_relation_test_binds_its_types_and_picks_the_side() -> None:
    sql, params = compiled(
        relations=(
            RelationTest(R.SERVING, Direction.OUT, True, E.BUSINESS_PROCESS),
            RelationTest(R.ASSOCIATION, Direction.IN, False),
            RelationTest(R.FLOW, Direction.ANY, True, E.NODE),
        )
    )

    assert sql.count("EXISTS") == 3
    assert sql.count("NOT (EXISTS") + sql.count("NOT EXISTS") == 1
    assert {"serving", "business_process", "association", "flow", "node"} <= set(params.values())


def test_each_order_has_the_id_as_tie_break() -> None:
    for order in QueryOrder:
        columns = _ordering(order)
        assert columns[-1] is ElementRecord.id
        assert len(columns) == 2
