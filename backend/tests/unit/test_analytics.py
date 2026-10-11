"""Reading properties as numbers and ranks, dimensions, and the colour scale."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from ea.domain.analytics import (
    INVALID_KEY,
    MAX_BUCKETS,
    UNSET_KEY,
    Dimension,
    DimensionKind,
    Metric,
    group_elements,
    rank_level,
    read_number,
    read_rank,
    scale_for,
    summarise,
    validate_question,
)
from ea.domain.archimate import ElementType as E
from ea.domain.errors import InvalidAnalysisError
from tests.support_analysis import an_element


class TestReadingANumber:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1200", 1200.0),
            ("1 200,50", 1200.5),
            ("1 200", 1200.0),  # noqa: RUF001
            ("1 200,5", 1200.5),  # noqa: RUF001
            ("-3.5", -3.5),
            ("12,5", 12.5),
            ("  42  ", 42.0),
        ],
    )
    def test_the_tolerated_spellings(self, raw: str, expected: float) -> None:
        assert read_number(raw) == expected

    @pytest.mark.parametrize("raw", ["1 200 €", "1.200,50", "", "abc", "1e3", "٣", "12,5,5", "--1"])
    def test_anything_else_is_unreadable_not_guessed(self, raw: str) -> None:
        assert read_number(raw) is None


class TestReadingARank:
    def test_the_declared_order_ignores_case(self) -> None:
        assert [read_rank("risk", v) for v in ("faible", "Moyen", "ÉLEVÉ", "critique")] == [
            0,
            1,
            2,
            3,
        ]

    def test_a_value_outside_the_enumeration_has_no_rank(self) -> None:
        assert read_rank("risk", "inconnu") is None

    def test_a_key_the_catalogue_does_not_order_has_no_rank(self) -> None:
        assert read_rank("owner", "faible") is None
        assert read_rank("cost", "faible") is None


class TestDimensions:
    def test_they_parse_and_print_back(self) -> None:
        for text in ("element_type", "layer", "property:risk"):
            assert str(Dimension.parse(text)) == text

    def test_a_property_dimension_carries_its_key(self) -> None:
        assert Dimension.parse("property:cost") == Dimension(DimensionKind.PROPERTY, "cost")

    @pytest.mark.parametrize(
        "text", ["", "bogus", "property:", "property:1x", "property:a b", "Layer"]
    )
    def test_anything_else_is_refused(self, text: str) -> None:
        with pytest.raises(InvalidAnalysisError):
            Dimension.parse(text)


class TestTheQuestion:
    def test_a_count_needs_no_property(self) -> None:
        assert validate_question(Metric.COUNT, None) is None
        assert validate_question(Metric.COUNT, "cost") is None

    def test_a_sum_needs_one(self) -> None:
        assert validate_question(Metric.SUM, "cost") == "cost"
        with pytest.raises(InvalidAnalysisError, match="sum"):
            validate_question(Metric.SUM, None)

    def test_the_key_must_be_a_plain_identifier(self) -> None:
        with pytest.raises(InvalidAnalysisError):
            validate_question(Metric.AVG, "cost'; --")


class TestTheScale:
    def test_ten_distinct_values_fall_into_five_equal_steps(self) -> None:
        assert scale_for([float(n) for n in range(1, 11)]) == (0, 0, 1, 1, 2, 2, 3, 3, 4, 4)

    def test_equal_values_share_a_level(self) -> None:
        assert scale_for([5.0, 5.0, 5.0]) == (0, 0, 0)

    def test_a_missing_value_has_no_level_and_does_not_shift_the_others(self) -> None:
        assert scale_for([1.0, None, 2.0]) == (0, None, 2)

    def test_nothing_in_nothing_out(self) -> None:
        assert scale_for([]) == ()
        assert scale_for([None, None]) == (None, None)

    def test_the_order_of_the_input_is_kept(self) -> None:
        assert scale_for([10.0, 1.0]) == (2, 0)

    def test_ranks_spread_over_the_five_levels(self) -> None:
        assert [rank_level(rank, 4) for rank in range(4)] == [0, 1, 3, 4]
        assert [rank_level(rank, 5) for rank in range(5)] == [0, 1, 2, 3, 4]
        assert rank_level(0, 1) == 0


def node(name: str, **properties: str):  # type: ignore[no-untyped-def]
    return an_element(E.NODE, name, properties=properties)


def counts(aggregate) -> dict[tuple[str, ...], int]:  # type: ignore[no-untyped-def]
    return {bucket.keys: bucket.count for bucket in aggregate.buckets}


class TestCounting:
    def test_by_element_type(self) -> None:
        elements = [node("a"), node("b"), an_element(E.BUSINESS_PROCESS, "p")]
        dims = (Dimension.parse("element_type"),)

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        assert counts(result) == {("business_process",): 1, ("node",): 2}
        assert result.total == 3

    def test_by_layer(self) -> None:
        elements = [node("a"), an_element(E.BUSINESS_PROCESS, "p")]
        dims = (Dimension.parse("layer"),)

        assert counts(summarise(group_elements(elements, dims), dims, Metric.COUNT)) == {
            ("business",): 1,
            ("technology",): 1,
        }

    def test_nothing_gives_an_empty_aggregate(self) -> None:
        dims = (Dimension.parse("layer"),)

        result = summarise(group_elements([], dims), dims, Metric.COUNT)

        assert (result.buckets, result.total, result.unreadable, result.truncated) == (
            (),
            0,
            0,
            False,
        )

    def test_the_levels_of_a_count_are_quantiles_of_the_counts(self) -> None:
        elements = [node(f"n{i}") for i in range(3)] + [an_element(E.BUSINESS_PROCESS, "p")]
        dims = (Dimension.parse("element_type"),)

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        by_key = {bucket.keys: bucket.level for bucket in result.buckets}
        assert by_key[("node",)] > by_key[("business_process",)]


class TestAnOrderedProperty:
    def elements(self):  # type: ignore[no-untyped-def]
        return [
            node("a", risk="Élevé"),
            node("b", risk="élevé"),
            node("c", risk="faible"),
            node("d", risk="inconnu"),
            node("e"),
            node("f", risk="   "),
        ]

    def test_values_are_folded_onto_the_declared_spelling_and_the_rest_goes_last(self) -> None:
        dims = (Dimension.parse("property:risk"),)

        result = summarise(group_elements(self.elements(), dims), dims, Metric.COUNT)

        assert [bucket.keys[0] for bucket in result.buckets] == [
            "faible",
            "élevé",
            INVALID_KEY,
            UNSET_KEY,
        ]
        assert counts(result)[("élevé",)] == 2
        assert counts(result)[(UNSET_KEY,)] == 2

    def test_the_level_is_the_rank_in_the_declared_order(self) -> None:
        dims = (Dimension.parse("property:risk"),)

        result = summarise(group_elements(self.elements(), dims), dims, Metric.COUNT)

        levels = {bucket.keys[0]: bucket.level for bucket in result.buckets}
        assert levels == {"faible": 0, "élevé": 3, INVALID_KEY: None, UNSET_KEY: None}


class TestANumericProperty:
    def elements(self):  # type: ignore[no-untyped-def]
        return [
            node("a", cost="1 200,50"),
            node("b", cost="300"),
            node("c", cost="1 200 €"),
            node("d"),
            node("e", cost="   "),
        ]

    def run(self, metric: Metric):  # type: ignore[no-untyped-def]
        dims = (Dimension.parse("layer"),)
        return summarise(group_elements(self.elements(), dims, "cost"), dims, metric, "cost")

    def test_sum_ignores_what_it_cannot_read_and_says_how_much_that_was(self) -> None:
        result = self.run(Metric.SUM)

        assert result.buckets[0].value == 1500.5
        assert result.unreadable == 1
        assert result.buckets[0].count == 5

    def test_the_other_metrics(self) -> None:
        assert self.run(Metric.AVG).buckets[0].value == 750.25
        assert self.run(Metric.MIN).buckets[0].value == 300.0
        assert self.run(Metric.MAX).buckets[0].value == 1200.5

    def test_a_bucket_with_nothing_readable_has_no_value_and_no_level(self) -> None:
        dims = (Dimension.parse("layer"),)

        result = summarise(
            group_elements([node("a", cost="n/a")], dims, "cost"), dims, Metric.SUM, "cost"
        )

        assert (result.buckets[0].value, result.buckets[0].level) == (None, None)
        assert result.unreadable == 1


class TestTwoDimensions:
    def test_a_heat_map_is_a_count_per_pair(self) -> None:
        elements = [
            node("a", lifecycle="en production"),
            node("b", lifecycle="en production"),
            an_element(E.BUSINESS_PROCESS, "p", properties={"lifecycle": "retiré"}),
        ]
        dims = (Dimension.parse("layer"), Dimension.parse("property:lifecycle"))

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        assert counts(result) == {("business", "retiré"): 1, ("technology", "en production"): 2}
        assert result.axis_keys(0) == ("business", "technology")
        assert result.axis_keys(1) == ("en production", "retiré")

    def test_the_levels_of_a_pair_are_quantiles_of_the_counts(self) -> None:
        elements = [node(f"n{i}", lifecycle="retiré") for i in range(4)] + [
            an_element(E.BUSINESS_PROCESS, "p", properties={"lifecycle": "retiré"})
        ]
        dims = (Dimension.parse("layer"), Dimension.parse("property:lifecycle"))

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        by_key = {bucket.keys: bucket.level for bucket in result.buckets}
        assert by_key[("technology", "retiré")] > by_key[("business", "retiré")]


class TestTheCeiling:
    def test_more_buckets_than_the_ceiling_are_cut_and_flagged(self) -> None:
        elements = [node(f"n{i}", team=f"team-{i:04d}") for i in range(MAX_BUCKETS + 5)]
        dims = (Dimension.parse("property:team"),)

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        assert len(result.buckets) == MAX_BUCKETS
        assert result.truncated is True
        assert result.total == MAX_BUCKETS + 5


def test_grouping_is_independent_of_the_order_of_the_elements() -> None:
    elements = [node("a", risk="faible"), node("b", risk="élevé"), node("c", risk="faible")]
    dims = (Dimension.parse("property:risk"),)

    forward = summarise(group_elements(elements, dims), dims, Metric.COUNT)
    backward = summarise(group_elements(list(reversed(elements)), dims), dims, Metric.COUNT)

    assert forward == backward


class TestThePackageDimension:
    def test_it_parses_and_prints_back(self) -> None:
        assert str(Dimension.parse("package")) == "package"

    def test_elements_are_grouped_by_package_id_and_the_unfiled_go_last(self) -> None:
        one, other = uuid4(), uuid4()
        elements = [
            replace(node("a"), package_id=one),
            replace(node("b"), package_id=one),
            replace(node("c"), package_id=other),
            node("d"),
        ]
        dims = (Dimension.parse("package"),)

        result = summarise(group_elements(elements, dims), dims, Metric.COUNT)

        assert {b.keys[0]: b.count for b in result.buckets} == {
            str(one): 2,
            str(other): 1,
            UNSET_KEY: 1,
        }
        assert result.buckets[-1].keys == (UNSET_KEY,)
