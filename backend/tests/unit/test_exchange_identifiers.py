"""The identifiers of an exported file are unique, whatever `source_id` holds."""

from __future__ import annotations

import re
from uuid import UUID, uuid4

import pytest

from ea.domain.exchange import MODEL_IDENTIFIER, IdentifierRegistry

NCNAME = re.compile(r"\A[A-Za-z_][A-Za-z0-9_.-]*\Z")


def test_a_safe_source_id_is_kept() -> None:
    assert IdentifierRegistry().assign("app-1", uuid4()) == "app-1"
    assert IdentifierRegistry().assign("id-5f3a9c", uuid4()) == "id-5f3a9c"  # an Archi identifier


def test_without_a_source_id_the_identifier_is_made_of_the_uuid() -> None:
    uid = uuid4()

    assert IdentifierRegistry().assign(None, uid) == f"id-{uid}"
    assert IdentifierRegistry().assign("", uid) == f"id-{uid}"


def test_two_objects_with_the_same_source_id_get_distinct_identifiers() -> None:
    registry = IdentifierRegistry()
    first, second = uuid4(), uuid4()

    assert registry.assign("dup", first) == "dup"
    assert registry.assign("dup", second) == f"id-{second}"
    assert registry.replaced == [second]


def test_a_source_id_shaped_like_another_objects_generated_identifier_is_refused() -> None:
    registry = IdentifierRegistry()
    victim, thief = uuid4(), uuid4()

    assert registry.assign(f"id-{victim}", thief) == f"id-{thief}"  # thief comes first
    assert registry.assign(None, victim) == f"id-{victim}"


def test_the_same_holds_when_the_generated_identifier_was_taken_first() -> None:
    registry = IdentifierRegistry()
    victim, thief = uuid4(), uuid4()

    assert registry.assign(None, victim) == f"id-{victim}"
    assert registry.assign(f"id-{victim}", thief) == f"id-{thief}"


def test_an_object_keeps_the_generated_form_of_its_own_uuid_as_source_id() -> None:
    uid = uuid4()

    assert IdentifierRegistry().assign(f"id-{uid}", uid) == f"id-{uid}"


def test_a_relationship_cannot_take_an_elements_source_id() -> None:
    registry = IdentifierRegistry()
    element, relationship = uuid4(), uuid4()

    assert registry.assign("shared", element) == "shared"
    assert registry.assign("shared", relationship) == f"id-{relationship}"


@pytest.mark.parametrize(
    "source_id",
    ["1abc", "a b", "a:b", "]]>", "a\nb", "x\n", "<x>", "é", MODEL_IDENTIFIER, "propid-owner"],
)
def test_a_source_id_that_is_not_a_safe_xml_name_is_replaced(source_id: str) -> None:
    uid = uuid4()

    identifier = IdentifierRegistry().assign(source_id, uid)

    assert identifier == f"id-{uid}"
    assert NCNAME.match(identifier)


class CountingSet(set[str]):
    lookups = 0

    def __contains__(self, item: object) -> bool:
        type(self).lookups += 1
        return super().__contains__(item)


def test_ten_thousand_objects_cost_a_constant_number_of_lookups_each() -> None:
    registry = IdentifierRegistry(taken=CountingSet())
    uids: list[UUID] = [uuid4() for _ in range(10_000)]

    identifiers = [registry.assign("same" if i % 2 else f"s{i}", u) for i, u in enumerate(uids)]

    assert len(set(identifiers)) == 10_000
    assert CountingSet.lookups <= 3 * 10_000
