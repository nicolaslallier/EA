"""The pieces of the graph store that need no database (docs/adr/0048)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from ea.db.models.architecture import ElementRecord
from ea.domain.approval import ApprovalState
from ea.domain.archimate import ElementType as E
from ea.domain.model import Element
from ea.repositories.architecture_store import (
    _other_ends,
    _review_ends,
    _write_review,
    element_from_row,
    element_row,
    frozen_clause,
    lock_elements,
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


class LockingSession:
    """A session that answers each locking statement with the rows whose ids it was asked for."""

    def __init__(
        self, rows: dict[UUID, ElementRecord], pairs: list[tuple[UUID, UUID]] = ()
    ) -> None:  # type: ignore[assignment]
        self.rows, self.pairs = rows, list(pairs)
        self.statements: list[list[UUID]] = []
        self.flushes = 0

    async def scalars(self, statement: Any) -> list[ElementRecord]:
        (asked,) = (v for v in statement.compile().params.values() if isinstance(v, list))
        self.statements.append(list(asked))
        return [self.rows[i] for i in asked if i in self.rows]

    async def execute(self, statement: Any) -> list[tuple[UUID, UUID]]:
        return self.pairs

    async def flush(self) -> None:
        self.flushes += 1


def a_row(name: str, **changes: Any) -> ElementRecord:
    return ElementRecord(
        **element_row(replace(Element.create(element_type=E.NODE, name=name, now=AT), **changes))
    )


@pytest.mark.asyncio
async def test_rows_are_locked_chunk_by_chunk_in_ascending_id_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ea.repositories import architecture_store

    monkeypatch.setattr(architecture_store, "LOCK_CHUNK", 2)
    ids = [UUID(int=n) for n in (5, 1, 4, 2, 3, 3)]
    session = LockingSession({i: ElementRecord(id=i) for i in ids})
    seen: list[int] = []

    async def after(chunk: int) -> None:
        seen.append(chunk)

    rows = await lock_elements(session, ids, key_only=True, after_chunk=after)  # type: ignore[arg-type]

    assert session.statements == [
        [UUID(int=1), UUID(int=2)],
        [UUID(int=3), UUID(int=4)],
        [UUID(int=5)],
    ]
    assert seen == [0, 1, 2] and set(rows) == set(ids)


@pytest.mark.asyncio
async def test_the_ends_of_a_link_are_reviewed_and_written_with_the_real_author() -> None:
    from ea.domain.approval import reviewed_after_change
    from ea.domain.ports import EndsReview

    approved = a_row(
        "a", approval_state=ApprovalState.APPROVED, approved_by_sub="p", approved_at=AT
    )
    draft = a_row("b")
    session = LockingSession({approved.id: approved, draft.id: draft})
    ends = EndsReview(change=reviewed_after_change, audit=lambda change: ())

    changes = await _review_ends(session, [approved.id, draft.id], ends, "actor")  # type: ignore[arg-type]

    assert {c.after.id for c in changes} == {approved.id, draft.id}
    assert (approved.approval_state, approved.approved_by_sub) == ("in_review", None)
    assert (approved.revision, approved.last_modified_by_sub) == (2, "actor")
    assert (draft.approval_state, draft.revision, draft.last_modified_by_sub) == (
        "draft",
        2,
        "actor",
    )
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_a_link_to_an_element_that_is_gone_is_refused_before_anything_is_written() -> None:
    from ea.domain.errors import ElementNotFoundError
    from ea.domain.ports import EndsReview

    here = a_row("here")
    session = LockingSession({here.id: here})

    with pytest.raises(ElementNotFoundError):
        await _review_ends(
            session,  # type: ignore[arg-type]
            [here.id, UUID(int=9)],
            EndsReview(change=lambda e: e, audit=lambda c: ()),
            "actor",
        )

    assert here.revision == 1  # untouched: no review
    assert session.flushes == 0


@pytest.mark.asyncio
async def test_the_other_ends_of_the_links_of_an_element_exclude_the_element_itself() -> None:
    me, left, right = UUID(int=1), UUID(int=2), UUID(int=3)
    session = LockingSession({}, [(me, left), (right, me), (me, me)])

    assert await _other_ends(session, me) == {left, right}  # type: ignore[arg-type]
