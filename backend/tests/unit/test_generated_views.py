"""Generated views: pure — what the source yields, and what changed since a view was frozen."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.api.schemas import MAX_DIAGRAM_NODES
from ea.domain.errors import GeneratedViewTooLargeError
from ea.domain.generated_views import (
    MAX_GENERATED_MEMBERS,
    Generation,
    PackageSource,
    QuerySource,
    SourceMembers,
    diff_members,
    resolve_members,
)


def test_the_ceiling_is_the_one_the_api_declares() -> None:
    assert MAX_GENERATED_MEMBERS == MAX_DIAGRAM_NODES == 500


def test_a_view_is_live_or_frozen() -> None:
    assert [g.value for g in Generation] == ["live", "frozen"]


def test_sources_are_values() -> None:
    query = uuid4()

    assert QuerySource(query) == QuerySource(query)
    assert PackageSource(query).recursive is False
    assert PackageSource(query, recursive=True) != PackageSource(query)


def test_members_keep_their_order_and_lose_their_duplicates() -> None:
    a, b = uuid4(), uuid4()

    assert resolve_members(SourceMembers((a, b, a), total=3)) == (a, b)


def test_a_source_over_the_ceiling_is_refused_with_its_real_count() -> None:
    found = SourceMembers(tuple(uuid4() for _ in range(MAX_GENERATED_MEMBERS)), total=1234)

    with pytest.raises(GeneratedViewTooLargeError, match="1234"):
        resolve_members(found)


def test_a_source_exactly_at_the_ceiling_is_accepted() -> None:
    ids = tuple(uuid4() for _ in range(MAX_GENERATED_MEMBERS))

    assert resolve_members(SourceMembers(ids, total=MAX_GENERATED_MEMBERS)) == ids


def test_diff_names_what_arrived_and_what_left() -> None:
    kept, gone, new = uuid4(), uuid4(), uuid4()

    added, removed = diff_members([kept, gone], [kept, new])

    assert (added, removed) == ((new,), (gone,))


def test_diff_is_empty_when_nothing_changed_and_ignores_order() -> None:
    a, b = uuid4(), uuid4()

    assert diff_members([a, b], [b, a]) == ((), ())
    assert diff_members([], []) == ((), ())


def test_diff_is_sorted_so_the_same_change_always_reads_the_same() -> None:
    ids = sorted(uuid4() for _ in range(4))

    added, _ = diff_members([], reversed(ids))

    assert list(added) == ids
