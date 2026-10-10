"""The structured filter: its bounds, its normalisation, and its stored form."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain import query as query_module
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import Layer
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import InvalidQueryError
from ea.domain.ports import ElementFilter
from ea.domain.query import (
    MAX_PROPERTY_TESTS,
    MAX_RELATION_TESTS,
    MAX_TEXT,
    QUERY_VERSION,
    Direction,
    ElementQuery,
    PropertyOp,
    PropertyTest,
    QueryOrder,
    RelationTest,
    check_storable,
    from_json,
    to_json,
)


def a_full_query() -> ElementQuery:
    return ElementQuery(
        element_types=(E.APPLICATION_COMPONENT, E.NODE),
        layers=(Layer.APPLICATION,),
        text="factu",
        package_id=uuid4(),
        include_subpackages=False,
        properties=(
            PropertyTest("owner", PropertyOp.EQ, "finance"),
            PropertyTest("note", PropertyOp.CONTAINS, "urgent"),
            PropertyTest("cidr", PropertyOp.EXISTS),
            PropertyTest("retired", PropertyOp.ABSENT),
        ),
        relations=(
            RelationTest(R.SERVING, Direction.OUT, True, E.BUSINESS_PROCESS),
            RelationTest(R.ASSOCIATION, Direction.ANY, False),
        ),
        order=QueryOrder.UPDATED_AT,
        limit=25,
        offset=50,
    )


class TestNormalisation:
    def test_the_defaults_filter_nothing(self) -> None:
        query = ElementQuery()

        assert (query.element_types, query.layers, query.text, query.package_id) == (
            (),
            (),
            None,
            None,
        )
        assert (query.include_subpackages, query.root_only) == (True, False)
        assert (query.order, query.limit, query.offset) == (QueryOrder.NAME, 50, 0)

    def test_the_text_is_trimmed_and_blank_means_none(self) -> None:
        assert ElementQuery(text="  billing ").text == "billing"
        assert ElementQuery(text="   ").text is None

    def test_a_repeated_type_or_layer_is_kept_once_in_order(self) -> None:
        query = ElementQuery(
            element_types=(E.NODE, E.APPLICATION_COMPONENT, E.NODE),
            layers=(Layer.BUSINESS, Layer.BUSINESS),
        )

        assert query.element_types == (E.NODE, E.APPLICATION_COMPONENT)
        assert query.layers == (Layer.BUSINESS,)


class TestBounds:
    def test_the_text_is_bounded(self) -> None:
        assert ElementQuery(text="x" * MAX_TEXT).text
        with pytest.raises(ValueError, match="text"):
            ElementQuery(text="x" * (MAX_TEXT + 1))

    def test_the_number_of_property_tests_is_bounded(self) -> None:
        tests = tuple(PropertyTest(f"k{i}", PropertyOp.EXISTS) for i in range(MAX_PROPERTY_TESTS))
        assert len(ElementQuery(properties=tests).properties) == MAX_PROPERTY_TESTS
        with pytest.raises(ValueError, match="property"):
            ElementQuery(properties=(*tests, PropertyTest("one_more", PropertyOp.EXISTS)))

    def test_the_number_of_relation_tests_is_bounded(self) -> None:
        tests = tuple(RelationTest(R.ASSOCIATION) for _ in range(MAX_RELATION_TESTS))
        assert len(ElementQuery(relations=tests).relations) == MAX_RELATION_TESTS
        with pytest.raises(ValueError, match="relation"):
            ElementQuery(relations=(*tests, RelationTest(R.ASSOCIATION)))

    @pytest.mark.parametrize("limit", [0, 201, -1])
    def test_a_page_is_between_1_and_200(self, limit: int) -> None:
        with pytest.raises(ValueError, match="limit"):
            ElementQuery(limit=limit)

    def test_a_negative_offset_is_refused(self) -> None:
        with pytest.raises(ValueError, match="offset"):
            ElementQuery(offset=-1)

    def test_the_root_and_a_package_at_once_contradict_each_other(self) -> None:
        with pytest.raises(ValueError, match="root_only"):
            ElementQuery(root_only=True, package_id=uuid4())


class TestPropertyTests:
    @pytest.mark.parametrize("key", ["", "1abc", "a-b", "a b", "é", "x" * 64])
    def test_a_key_follows_the_rule_of_an_element_attribute(self, key: str) -> None:
        with pytest.raises(ValueError, match="plain identifier"):
            PropertyTest(key, PropertyOp.EXISTS)

    @pytest.mark.parametrize("op", [PropertyOp.EQ, PropertyOp.CONTAINS])
    def test_eq_and_contains_need_a_value(self, op: PropertyOp) -> None:
        with pytest.raises(ValueError, match="value"):
            PropertyTest("owner", op)

    def test_contains_refuses_an_empty_fragment_but_eq_allows_an_empty_value(self) -> None:
        PropertyTest("owner", PropertyOp.EQ, "")
        with pytest.raises(ValueError, match="value"):
            PropertyTest("owner", PropertyOp.CONTAINS, "")

    @pytest.mark.parametrize("op", [PropertyOp.EXISTS, PropertyOp.ABSENT])
    def test_exists_and_absent_take_no_value(self, op: PropertyOp) -> None:
        with pytest.raises(ValueError, match="no value"):
            PropertyTest("owner", op, "x")

    def test_a_value_is_bounded(self) -> None:
        with pytest.raises(ValueError, match="value"):
            PropertyTest("owner", PropertyOp.EQ, "x" * 201)

    def test_a_value_with_sql_in_it_is_just_a_value(self) -> None:
        assert PropertyTest("owner", PropertyOp.EQ, "x'; DROP TABLE elements; --").value


class TestRelationTests:
    def test_a_relation_no_filtered_type_can_have_is_accepted_not_refused(self) -> None:
        """The metamodel stays the only judge: the answer is empty, not an error."""
        query = ElementQuery(
            element_types=(E.DATA_OBJECT,),
            relations=(RelationTest(R.SERVING, Direction.OUT, True, E.BUSINESS_ACTOR),),
        )

        assert len(query.relations) == 1


class TestTheStoredForm:
    def test_it_round_trips_exactly(self) -> None:
        query = a_full_query()

        assert from_json(to_json(query)) == query

    def test_the_stored_form_is_versioned_and_plain_json(self) -> None:
        stored = to_json(a_full_query())

        assert stored["version"] == QUERY_VERSION
        assert stored["order"] == "updated_at"
        assert stored["relations"][0] == {
            "relationship_type": "serving",
            "direction": "out",
            "present": True,
            "other_type": "business_process",
        }

    def test_an_unknown_version_is_refused(self) -> None:
        stored = {**to_json(a_full_query()), "version": 2}

        with pytest.raises(InvalidQueryError, match="version"):
            from_json(stored)

    def test_a_value_the_enumerations_no_longer_know_is_refused(self) -> None:
        stored = to_json(a_full_query())
        stored["element_types"] = ["application_component", "flux_capacitor"]

        with pytest.raises(InvalidQueryError):
            from_json(stored)

    @pytest.mark.parametrize("garbage", ["oops", None, 3, [1, 2]])
    def test_something_that_is_not_an_object_is_refused(self, garbage: object) -> None:
        with pytest.raises(InvalidQueryError, match="object"):
            from_json(garbage)

    def test_a_missing_key_is_refused_not_a_key_error(self) -> None:
        stored = to_json(a_full_query())
        del stored["relations"]

        with pytest.raises(InvalidQueryError):
            from_json(stored)

    def test_a_stored_value_that_breaks_a_bound_is_refused(self) -> None:
        stored = to_json(a_full_query())
        stored["limit"] = 9999

        with pytest.raises(InvalidQueryError):
            from_json(stored)

    def test_a_stored_query_larger_than_the_ceiling_cannot_be_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        check_storable(a_full_query())
        monkeypatch.setattr(query_module, "MAX_STORED_QUERY_BYTES", 100)

        with pytest.raises(InvalidQueryError, match="bytes"):
            check_storable(a_full_query())


class TestTheFilterItBecomes:
    def test_every_criterion_reaches_the_repository_filter(self) -> None:
        query = a_full_query()

        criteria = ElementFilter.from_query(query)

        assert criteria.element_types == query.element_types
        assert criteria.search == "factu"
        assert criteria.package_id == query.package_id
        assert criteria.include_subpackages is False
        assert criteria.properties == query.properties
        assert criteria.relations == query.relations
        assert (criteria.order, criteria.limit, criteria.offset) == (QueryOrder.UPDATED_AT, 25, 50)


class TestNulCharacter:
    def test_a_nul_in_the_text_is_refused(self) -> None:
        with pytest.raises(InvalidQueryError, match="NUL"):
            ElementQuery(text="a\x00b")

    @pytest.mark.parametrize("op", [PropertyOp.EQ, PropertyOp.CONTAINS])
    def test_a_nul_in_a_property_value_is_refused(self, op: PropertyOp) -> None:
        with pytest.raises(InvalidQueryError, match="NUL"):
            PropertyTest("owner", op, "a\x00b")
