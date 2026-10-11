"""Reading properties as numbers and ranks, dimensions, and the colour scale."""

from __future__ import annotations

import pytest

from ea.domain.analytics import (
    Dimension,
    DimensionKind,
    Metric,
    rank_level,
    read_number,
    read_rank,
    scale_for,
    validate_question,
)
from ea.domain.errors import InvalidAnalysisError


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
