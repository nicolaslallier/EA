"""The pieces of the graph store that need no database (docs/adr/0046)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.dialects import postgresql

from ea.db.models.architecture import ElementRecord
from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.model import Element
from ea.repositories.architecture_store import (
    _write_review,
    element_from_row,
    element_row,
    frozen_clause,
    lock_order,
)

AT = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def test_a_review_writes_the_review_columns_and_nothing_the_element_says() -> None:
    element = Element.create(element_type=E.NODE, name="a", description="kept", now=AT)
    row = ElementRecord(**element_row(element))
    changed = replace(
        element,
        name="renamed",
        description="ignored",
        approval_state=ApprovalState.APPROVED,
        locked=True,
        approved_by_sub="approver-1",
        approved_at=AT,
        revision=7,
        last_modified_by_sub="someone",
    )

    _write_review(row, changed)

    back = element_from_row(row)
    assert (back.name, back.description) == ("a", "kept")
    assert (back.approval_state, back.locked, back.approved_by_sub) == (
        ApprovalState.APPROVED,
        True,
        "approver-1",
    )
    assert (back.revision, back.last_modified_by_sub) == (7, "someone")


def test_the_sql_twin_of_the_rule_is_fail_closed() -> None:
    sql = str(frozen_clause().compile(dialect=postgresql.dialect()))

    assert "NOT IN" in sql and "locked" in sql  # "not draft, not in review" — never "== approved"


def test_element_rows_are_locked_in_ascending_id_order_each_once() -> None:
    from uuid import UUID

    low, mid, high = (UUID(int=n) for n in (1, 2, 3))

    assert lock_order([high, low, mid, low, high]) == [low, mid, high]
    assert lock_order(iter([mid, low])) == lock_order([low, mid]) == [low, mid]
    assert lock_order([]) == []
