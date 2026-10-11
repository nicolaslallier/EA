"""Plateaus compared, and the roadmap built from them — pure, no database."""

from __future__ import annotations

from datetime import date
from typing import ClassVar
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import InvalidTimelineError
from ea.domain.model import Element, Relationship
from ea.domain.timeline import (
    MAX_ROADMAP_DAYS,
    Lifespan,
    PlateauSummary,
    build_roadmap,
    check_window,
    diff_plateaus,
    resolve_window,
    sort_plateaus,
)
from tests.conftest import FIXED_NOW


def an_element(
    element_type: E = E.APPLICATION_COMPONENT, name: str = "Billing", **properties: str
) -> Element:
    return Element.create(
        element_type=element_type, name=name, now=FIXED_NOW, properties=properties or None
    )


def a_link(source: Element, target: Element, relationship_type: R) -> Relationship:
    return Relationship.between(relationship_type, source, target, now=FIXED_NOW)


class TestDiffPlateaus:
    def test_what_leaves_what_arrives_and_what_stays(self) -> None:
        old, kept, new = an_element(name="Old"), an_element(name="Kept"), an_element(name="New")

        gap = diff_plateaus([old, kept], [kept, new])

        assert gap.only_in_a == (old,)
        assert gap.only_in_b == (new,)
        assert gap.common == (kept,)

    def test_a_duplicate_is_counted_once(self) -> None:
        one = an_element(name="One")

        gap = diff_plateaus([one, one], [one])

        assert gap.common == (one,) and gap.only_in_a == () and gap.only_in_b == ()

    def test_two_empty_plateaus_differ_in_nothing(self) -> None:
        gap = diff_plateaus([], [])

        assert (gap.only_in_a, gap.only_in_b, gap.common) == ((), (), ())

    def test_an_empty_side_makes_everything_an_arrival_or_a_departure(self) -> None:
        one = an_element(name="One")

        assert diff_plateaus([], [one]).only_in_b == (one,)
        assert diff_plateaus([one], []).only_in_a == (one,)

    def test_the_answer_is_ordered_by_name_whatever_the_input_order(self) -> None:
        b, a = an_element(name="b"), an_element(name="A")

        assert [e.name for e in diff_plateaus([b, a], []).only_in_a] == ["A", "b"]


class TestPlateauOrder:
    def test_earliest_first_and_undated_last(self) -> None:
        def summary(name: str, start: date | None) -> PlateauSummary:
            return PlateauSummary(an_element(E.PLATEAU, name), Lifespan(start, None), 0)

        ordered = sort_plateaus(
            [
                summary("none", None),
                summary("late", date(2028, 1, 1)),
                summary("early", date(2026, 1, 1)),
            ]
        )

        assert [s.element.name for s in ordered] == ["early", "late", "none"]


class TestWindow:
    SPANS: ClassVar[list[Lifespan]] = [
        Lifespan(date(2026, 3, 1), date(2027, 6, 30)),
        Lifespan(None, date(2029, 1, 1)),
        Lifespan(),
    ]

    def test_the_default_window_is_the_bounds_of_the_data(self) -> None:
        assert resolve_window(self.SPANS, None, None) == (date(2026, 3, 1), date(2029, 1, 1))

    def test_no_dated_data_means_no_window(self) -> None:
        assert resolve_window([Lifespan()], None, None) == (None, None)

    def test_an_explicit_bound_wins_over_the_data(self) -> None:
        assert resolve_window(self.SPANS, date(2027, 1, 1), None) == (
            date(2027, 1, 1),
            date(2029, 1, 1),
        )

    def test_a_window_that_ends_before_it_starts_is_refused_before_anything_is_read(self) -> None:
        with pytest.raises(InvalidTimelineError, match="before"):
            check_window(date(2030, 1, 1), date(2029, 1, 1))

    def test_two_explicit_bounds_wider_than_twenty_years_are_refused_before_a_read(self) -> None:
        with pytest.raises(InvalidTimelineError, match="20 years"):
            check_window(date(2000, 1, 1), date(2030, 1, 1))

    def test_a_default_end_is_cut_to_twenty_years(self) -> None:
        start, end = resolve_window([Lifespan(date(2000, 1, 1), date(2060, 1, 1))], None, None)

        assert start == date(2000, 1, 1)
        assert end is not None and (end - start).days == MAX_ROADMAP_DAYS

    def test_a_single_explicit_bound_far_from_the_data_is_cut_not_refused(self) -> None:
        start, end = resolve_window(
            [Lifespan(date(2026, 1, 1), date(2027, 1, 1))], date(2060, 1, 1), None
        )

        assert start == date(2060, 1, 1) and end == date(2060, 1, 1)


class TestBuildingTheRoadmap:
    def test_dated_things_with_their_plateau_and_the_edges_between_them(self) -> None:
        to_be = an_element(E.PLATEAU, "To-Be", valid_from="2028-01-01")
        work = an_element(
            E.WORK_PACKAGE, "Migration", valid_from="2026-06-01", valid_until="2027-12-31"
        )
        deliverable = an_element(E.DELIVERABLE, "Runbook")
        links = [
            a_link(to_be, work, R.COMPOSITION),
            a_link(work, deliverable, R.REALIZATION),
        ]

        roadmap = build_roadmap([to_be, work, deliverable], links, None, None)

        by_name = {i.element.name: i for i in roadmap.items}
        assert by_name["Migration"].plateau_ids == (to_be.id,)
        assert by_name["To-Be"].plateau_ids == ()
        assert not by_name["Runbook"].lifespan.is_dated
        assert (roadmap.window_from, roadmap.window_to) == (date(2026, 6, 1), date(2028, 1, 1))
        assert [(e.relationship_type, e.source_id, e.target_id) for e in roadmap.edges] == [
            (R.REALIZATION, work.id, deliverable.id)
        ]

    def test_a_window_leaves_out_what_does_not_overlap_but_keeps_the_undated(self) -> None:
        early = an_element(E.WORK_PACKAGE, "Early", valid_until="2025-12-31")
        inside = an_element(E.WORK_PACKAGE, "Inside", valid_from="2026-03-01")
        undated = an_element(E.DELIVERABLE, "Undated")

        roadmap = build_roadmap([early, inside, undated], [], date(2026, 1, 1), date(2026, 12, 31))

        assert {i.element.name for i in roadmap.items} == {"Inside", "Undated"}

    def test_an_edge_to_a_thing_outside_the_window_is_dropped(self) -> None:
        early = an_element(E.WORK_PACKAGE, "Early", valid_until="2025-12-31")
        inside = an_element(E.WORK_PACKAGE, "Inside", valid_from="2026-03-01")

        roadmap = build_roadmap(
            [early, inside],
            [a_link(early, inside, R.TRIGGERING)],
            date(2026, 1, 1),
            date(2026, 12, 31),
        )

        assert roadmap.edges == ()

    def test_the_output_does_not_depend_on_the_input_order(self) -> None:
        a = an_element(E.WORK_PACKAGE, "A", valid_from="2026-01-01")
        b = an_element(E.WORK_PACKAGE, "B", valid_from="2026-01-01")

        first = build_roadmap([a, b], [], None, None)
        second = build_roadmap([b, a], [], None, None)

        assert first == second

    def test_a_link_between_unknown_things_is_ignored(self) -> None:
        a, b = an_element(E.WORK_PACKAGE, "A"), an_element(E.WORK_PACKAGE, "B")
        stranger = an_element(E.WORK_PACKAGE, "Stranger")

        roadmap = build_roadmap([a, b], [a_link(a, stranger, R.TRIGGERING)], None, None)

        assert roadmap.edges == ()
        assert uuid4() not in {i.element.id for i in roadmap.items}


class TestADefaultWindowThatLosesSomethingSaysSo:
    def test_a_default_window_cut_to_twenty_years_is_flagged_with_the_real_bounds(self) -> None:
        old = an_element(E.WORK_PACKAGE, "Old", valid_from="1999-01-01")
        recent = an_element(E.WORK_PACKAGE, "Recent", valid_from="2027-01-01")

        roadmap = build_roadmap([old, recent], [], None, None)

        assert roadmap.window_truncated is True
        assert (roadmap.data_from, roadmap.data_to) == (date(1999, 1, 1), date(2027, 1, 1))
        assert {i.element.name for i in roadmap.items} == {"Old"}

    def test_a_window_that_lost_nothing_is_not_flagged(self) -> None:
        a = an_element(E.WORK_PACKAGE, "A", valid_from="2026-01-01", valid_until="2027-01-01")

        roadmap = build_roadmap([a], [], None, None)

        assert roadmap.window_truncated is False
        assert (roadmap.data_from, roadmap.data_to) == (date(2026, 1, 1), date(2027, 1, 1))

    def test_an_explicit_window_is_the_callers_choice_and_never_flagged(self) -> None:
        old = an_element(E.WORK_PACKAGE, "Old", valid_from="1999-01-01")

        roadmap = build_roadmap([old], [], date(2026, 1, 1), date(2026, 12, 31))

        assert roadmap.window_truncated is False

    def test_no_dated_data_has_no_bounds(self) -> None:
        roadmap = build_roadmap([an_element(E.DELIVERABLE, "D")], [], None, None)

        assert (roadmap.data_from, roadmap.data_to, roadmap.window_truncated) == (None, None, False)
