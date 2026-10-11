"""The one predicate that decides what is visible on a day — compiled, not executed."""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

from sqlalchemy import and_
from sqlalchemy.dialects import postgresql

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.domain.archimate import RelationshipType
from ea.repositories.architecture_store import _impact_walk, _neighbourhood_walk, visible_at


def test_no_day_asked_adds_no_condition_at_all() -> None:
    """The regression guarantee: without `as_of` the SQL is the SQL it always was."""
    assert visible_at(ElementRecord, None) == []
    assert visible_at(RelationshipRecord, None) == []


def test_a_day_asked_compares_both_ends_with_the_day_bound_not_inlined() -> None:
    clauses = visible_at(ElementRecord, date(2027, 1, 1))

    compiled = and_(*clauses).compile(dialect=postgresql.dialect())

    sql = str(compiled)
    assert "elements.properties ->> 'valid_from'" in sql
    assert "elements.properties ->> 'valid_until'" in sql
    assert "IS NULL" in sql
    assert "2027-01-01" not in sql
    assert "2027-01-01" in compiled.params.values()


def test_the_same_predicate_reads_a_relationship() -> None:
    sql = str(
        and_(*visible_at(RelationshipRecord, date(2027, 1, 1))).compile(
            dialect=postgresql.dialect()
        )
    )

    assert "relationships.properties ->> 'valid_until'" in sql


def _sql(cte: Any) -> str:
    return str(cte.select().compile(dialect=postgresql.dialect()))


def test_the_walks_are_byte_for_byte_the_same_without_a_day() -> None:
    element = uuid4()
    for walk in (_neighbourhood_walk, _impact_walk):
        before = _sql(walk(element, 3, (RelationshipType.SERVING,)))
        after = _sql(walk(element, 3, (RelationshipType.SERVING,), None))

        assert "valid_from" not in before
        assert before == after


def test_a_day_puts_the_predicate_on_the_seed_the_link_and_the_far_end() -> None:
    element = uuid4()
    for walk in (_neighbourhood_walk, _impact_walk):
        sql = _sql(walk(element, 3, (), date(2027, 1, 1)))

        # the seed (a retired root reaches nothing), the link, and the far end of the hop
        assert sql.count("'valid_until'") >= 3
        assert sql.count("'valid_from'") >= 3
