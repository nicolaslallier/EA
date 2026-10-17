"""Time in the model: lifespans and their validation, with no database."""

from __future__ import annotations

from datetime import date

import pytest

from ea.domain.errors import InvalidLifespanError
from ea.domain.timeline import (
    VALID_FROM,
    VALID_UNTIL,
    Lifespan,
    readable_lifespan_properties,
    validate_lifespan_properties,
)


class TestLifespan:
    def test_both_bounds_are_inclusive(self) -> None:
        span = Lifespan(date(2026, 1, 1), date(2026, 12, 31))

        assert span.covers(date(2026, 1, 1))
        assert span.covers(date(2026, 12, 31))
        assert not span.covers(date(2025, 12, 31))
        assert not span.covers(date(2027, 1, 1))

    def test_an_open_end_covers_everything_after_the_start(self) -> None:
        assert Lifespan(date(2026, 1, 1), None).covers(date(2099, 1, 1))
        assert not Lifespan(date(2026, 1, 1), None).covers(date(2025, 1, 1))

    def test_an_open_start_covers_everything_before_the_end(self) -> None:
        assert Lifespan(None, date(2026, 1, 1)).covers(date(1990, 1, 1))
        assert not Lifespan(None, date(2026, 1, 1)).covers(date(2026, 1, 2))

    def test_an_undated_thing_is_always_there(self) -> None:
        assert Lifespan().covers(date(1900, 1, 1))
        assert Lifespan().covers(date(2999, 1, 1))
        assert not Lifespan().is_dated

    def test_two_lifespans_sharing_one_day_overlap(self) -> None:
        first = Lifespan(date(2026, 1, 1), date(2026, 6, 30))
        second = Lifespan(date(2026, 6, 30), date(2026, 12, 31))

        assert first.overlaps(second) and second.overlaps(first)

    def test_two_disjoint_lifespans_do_not_overlap(self) -> None:
        first = Lifespan(date(2026, 1, 1), date(2026, 6, 29))
        second = Lifespan(date(2026, 6, 30), date(2026, 12, 31))

        assert not first.overlaps(second)

    def test_an_open_lifespan_overlaps_whatever_it_reaches(self) -> None:
        assert Lifespan().overlaps(Lifespan(date(2026, 1, 1), date(2026, 2, 1)))
        assert Lifespan(date(2030, 1, 1), None).overlaps(Lifespan(None, date(2031, 1, 1)))

    def test_it_is_read_from_the_properties(self) -> None:
        span = Lifespan.read({VALID_FROM: "2026-03-01", VALID_UNTIL: "2027-06-30", "owner": "x"})

        assert span == Lifespan(date(2026, 3, 1), date(2027, 6, 30))

    def test_a_blank_property_reads_as_open(self) -> None:
        assert Lifespan.read({VALID_FROM: "  ", VALID_UNTIL: ""}) == Lifespan()

    def test_a_malformed_stored_value_is_named_not_swallowed(self) -> None:
        with pytest.raises(InvalidLifespanError, match=VALID_FROM):
            Lifespan.read({VALID_FROM: "2027-06"})


class TestValidation:
    def test_nothing_to_validate_comes_back_as_it_came(self) -> None:
        assert validate_lifespan_properties(None) is None
        assert validate_lifespan_properties({}) == {}

    def test_other_properties_are_left_alone(self) -> None:
        assert validate_lifespan_properties({"owner": "finance"}) == {"owner": "finance"}

    def test_a_valid_pair_is_stored_in_its_canonical_form(self) -> None:
        stored = validate_lifespan_properties(
            {VALID_FROM: " 2026-03-01 ", VALID_UNTIL: "20270630", "owner": "x"}
        )

        assert stored == {VALID_FROM: "2026-03-01", VALID_UNTIL: "2027-06-30", "owner": "x"}

    @pytest.mark.parametrize("partial", ["2027-06", "2027-Q2", "2027", "demain", "2027-13-01"])
    def test_a_partial_or_impossible_date_is_refused(self, partial: str) -> None:
        with pytest.raises(InvalidLifespanError, match=VALID_UNTIL):
            validate_lifespan_properties({VALID_UNTIL: partial})

    def test_an_end_before_the_start_is_refused(self) -> None:
        with pytest.raises(InvalidLifespanError, match="before"):
            validate_lifespan_properties({VALID_FROM: "2027-01-02", VALID_UNTIL: "2027-01-01"})

    def test_a_one_day_lifespan_is_legal(self) -> None:
        stored = validate_lifespan_properties({VALID_FROM: "2027-01-01", VALID_UNTIL: "2027-01-01"})

        assert stored == {VALID_FROM: "2027-01-01", VALID_UNTIL: "2027-01-01"}

    def test_a_blank_date_is_removed_not_stored(self) -> None:
        """A stored blank end would hide the element from every dated read."""
        stored = validate_lifespan_properties({VALID_FROM: "", VALID_UNTIL: "  ", "owner": "x"})

        assert stored == {"owner": "x"}


class TestReadingWhatWasStoredBeforeTheRule:
    """A baseline captured before revision 0014 may hold any string under these keys."""

    def test_a_good_pair_is_canonical_and_the_rest_is_untouched(self) -> None:
        stored = readable_lifespan_properties({VALID_FROM: "20260301", "owner": "x"})

        assert stored == {VALID_FROM: "2026-03-01", "owner": "x"}

    def test_an_unreadable_value_is_set_aside_not_raised_not_lost(self) -> None:
        stored = readable_lifespan_properties({VALID_UNTIL: "2027-06", VALID_FROM: "2" * 9999})

        assert stored == {"legacy_valid_until": "2027-06", "legacy_valid_from": "2" * 9999}

    def test_a_blank_is_removed_and_a_reversed_pair_sets_the_end_aside(self) -> None:
        assert readable_lifespan_properties({VALID_FROM: " "}) == {}
        assert readable_lifespan_properties(
            {VALID_FROM: "2027-02-01", VALID_UNTIL: "2027-01-01"}
        ) == {VALID_FROM: "2027-02-01", "legacy_valid_until": "2027-01-01"}

    def test_a_legacy_key_already_there_is_never_overwritten_exactly_as_the_migration(
        self,
    ) -> None:
        """The same slots as revision 0014: `_2` … `_9`, then appended to the last."""
        assert readable_lifespan_properties(
            {VALID_FROM: "not a date", "legacy_valid_from": "keep me"}
        ) == {"legacy_valid_from": "keep me", "legacy_valid_from_2": "not a date"}
        assert readable_lifespan_properties(
            {
                VALID_FROM: "2027-02-01",
                VALID_UNTIL: "2027-01-01",
                "legacy_valid_until": "old",
            }
        ) == {
            VALID_FROM: "2027-02-01",
            "legacy_valid_until": "old",
            "legacy_valid_until_2": "2027-01-01",
        }
        taken = {
            "legacy_valid_from": "a",
            **{f"legacy_valid_from_{n}": str(n) for n in range(2, 10)},
        }
        full = readable_lifespan_properties({VALID_FROM: "x", **taken})
        assert full["legacy_valid_from"] == "a"
        assert full["legacy_valid_from_9"] == "9\nx"
        assert VALID_FROM not in full


class TestAValueThatIsNotAStringNeverRaisesWhenReadBack:
    @pytest.mark.parametrize(
        "value", [20240101, 2024.5, True, None, ["2024-01-01"], {"a": 1}, "9" * 100_000]
    )
    def test_it_is_set_aside_not_raised(self, value: object) -> None:
        stored = readable_lifespan_properties({VALID_FROM: value, "owner": "x"})  # type: ignore[dict-item]

        assert VALID_FROM not in stored
        assert stored["owner"] == "x"
        assert f"legacy_{VALID_FROM}" in stored
        assert Lifespan.read_tolerant({VALID_FROM: value}) == Lifespan()  # type: ignore[dict-item]

    @pytest.mark.parametrize("value", [20240101, True, ["2024-01-01"]])
    def test_the_strict_reading_refuses_it_in_the_domain_error_not_an_attribute_error(
        self, value: object
    ) -> None:
        with pytest.raises(InvalidLifespanError):
            Lifespan.read({VALID_FROM: value})  # type: ignore[dict-item]


class TestWhatIsRefusedIsNotEchoed:
    def test_the_message_names_the_key_and_never_the_value(self) -> None:
        with pytest.raises(InvalidLifespanError) as raised:
            validate_lifespan_properties({VALID_UNTIL: "secret-looking-text"})

        assert VALID_UNTIL in str(raised.value)
        assert "secret-looking-text" not in str(raised.value)

    def test_an_absurdly_long_value_is_refused_without_being_parsed(self) -> None:
        with pytest.raises(InvalidLifespanError):
            validate_lifespan_properties({VALID_FROM: "2" * 100_000})

    def test_a_week_date_is_not_a_day_we_store(self) -> None:
        """`date.fromisoformat` reads `2027-W01-1`; the stored spelling is only YYYY-MM-DD."""
        stored = validate_lifespan_properties({VALID_FROM: "2027-W01-1"})

        assert stored == {VALID_FROM: "2027-01-04"}
