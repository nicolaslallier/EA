"""Plateaus, the gap between two of them, and the roadmap — on the in-memory graph."""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import (
    AnalysisBusyError,
    DuplicateGapError,
    ElementNotFoundError,
    InvalidTimelineError,
    NotAuthorisedError,
    ScopeTooLargeError,
)
from ea.domain.model import Element
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.timeline import TimelineService
from tests.conftest import InMemoryRepository, a_reader

pytestmark = pytest.mark.asyncio


async def plateau(service: ArchitectureService, name: str, **dates: str) -> Element:
    return await service.create_element(element_type=E.PLATEAU, name=name, properties=dates or None)


async def contain(service: ArchitectureService, owner: Element, *members: Element) -> None:
    for member in members:
        await service.connect(
            relationship_type=R.COMPOSITION, source_id=owner.id, target_id=member.id
        )


async def app(service: ArchitectureService, name: str, **dates: str) -> Element:
    return await service.create_element(
        element_type=E.APPLICATION_COMPONENT, name=name, properties=dates or None
    )


class TestPlateaus:
    async def test_they_come_earliest_first_with_what_they_contain(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        await plateau(service, "As-Is", valid_from="2024-01-01")
        to_be = await plateau(service, "To-Be", valid_from="2028-01-01")
        await plateau(service, "Idée")
        await contain(service, to_be, await app(service, "Billing"))

        listed = await timeline.list_plateaus()

        assert [(s.element.name, s.member_count) for s in listed] == [
            ("As-Is", 0),
            ("To-Be", 1),
            ("Idée", 0),
        ]
        assert listed[0].lifespan.start == date(2024, 1, 1)

    async def test_only_plateaus_are_listed(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        await app(service, "Billing")

        assert await timeline.list_plateaus() == ()

    async def test_more_plateaus_than_the_ceiling_is_refused_not_cut(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        small = TimelineService(service, repository, max_plateaus=2)
        for name in "abc":
            await plateau(service, name)

        with pytest.raises(ScopeTooLargeError):
            await small.list_plateaus()


class TestGap:
    async def test_it_says_what_leaves_arrives_and_stays(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        as_is, to_be = await plateau(service, "As-Is"), await plateau(service, "To-Be")
        old = await app(service, "Old")
        kept = await app(service, "Kept")
        new = await app(service, "New")
        await contain(service, as_is, old, kept)
        await contain(service, to_be, kept, new)

        comparison = await timeline.plateau_gap(as_is.id, to_be.id)

        assert [e.name for e in comparison.diff.only_in_a] == ["Old"]
        assert [e.name for e in comparison.diff.only_in_b] == ["New"]
        assert [e.name for e in comparison.diff.common] == ["Kept"]
        assert comparison.gaps == ()

    async def test_a_plateau_cannot_be_compared_with_itself(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        one = await plateau(service, "One")

        with pytest.raises(InvalidTimelineError, match="itself"):
            await timeline.plateau_gap(one.id, one.id)

    async def test_an_unknown_plateau_is_a_404(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        one = await plateau(service, "One")

        with pytest.raises(ElementNotFoundError):
            await timeline.plateau_gap(one.id, uuid4())

    async def test_something_that_is_not_a_plateau_is_refused_without_its_name(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        one, billing = await plateau(service, "One"), await app(service, "Billing")

        with pytest.raises(InvalidTimelineError) as refused:
            await timeline.plateau_gap(one.id, billing.id)

        assert "Billing" not in str(refused.value)

    async def test_a_plateau_too_big_to_compare_is_refused_not_cut(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        small = TimelineService(service, repository, max_members=2)
        a, b = await plateau(service, "A"), await plateau(service, "B")
        await contain(
            service, a, await app(service, "x"), await app(service, "y"), await app(service, "z")
        )

        with pytest.raises(ScopeTooLargeError):
            await small.plateau_gap(a.id, b.id)

    async def test_the_comparison_is_computed_off_the_event_loop_under_a_place(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        from ea.services.analysis import analysis_admission

        admission = analysis_admission(limit=1)
        guarded = TimelineService(service, repository, admission=admission)
        a, b = await plateau(service, "A"), await plateau(service, "B")

        async with admission.lease("someone-else"):
            with pytest.raises(AnalysisBusyError):
                await guarded.plateau_gap(a.id, b.id)

        assert (await guarded.plateau_gap(a.id, b.id)).diff.common == ()
        assert admission.in_flight == 0


class TestCreatingTheGap:
    async def test_it_is_an_ordinary_gap_element_associated_to_both(
        self,
        service: ArchitectureService,
        timeline: TimelineService,
        repository: InMemoryRepository,
    ) -> None:
        as_is, to_be = await plateau(service, "As-Is"), await plateau(service, "To-Be")

        gap = await timeline.create_gap(as_is.id, to_be.id)

        assert gap.element_type is E.GAP
        assert gap.name == "Écart As-Is → To-Be"
        links = list(repository.relationships.values())
        assert {(link.relationship_type, link.source_id, link.target_id) for link in links} == {
            (R.ASSOCIATION, gap.id, as_is.id),
            (R.ASSOCIATION, gap.id, to_be.id),
        }
        assert [g.id for g in (await timeline.plateau_gap(as_is.id, to_be.id)).gaps] == [gap.id]

    async def test_a_name_can_be_given(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        a, b = await plateau(service, "A"), await plateau(service, "B")

        assert (await timeline.create_gap(a.id, b.id, name="Migration ERP")).name == "Migration ERP"

    async def test_a_default_name_is_cut_to_the_length_a_name_may_have(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        a, b = await plateau(service, "A" * 200), await plateau(service, "B" * 200)

        gap = await timeline.create_gap(a.id, b.id)

        assert 0 < len(gap.name) <= 200

    async def test_a_second_gap_for_the_same_pair_is_refused_in_either_order(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        a, b = await plateau(service, "A"), await plateau(service, "B")
        await timeline.create_gap(a.id, b.id)

        with pytest.raises(DuplicateGapError):
            await timeline.create_gap(a.id, b.id, name="Autre nom")
        with pytest.raises(DuplicateGapError):
            await timeline.create_gap(b.id, a.id, name="Encore un")

    async def test_a_reader_cannot_create_one(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        a, b = await plateau(service, "A"), await plateau(service, "B")

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await timeline.create_gap(a.id, b.id)

    async def test_nothing_stays_behind_when_the_second_association_fails(
        self,
        service: ArchitectureService,
        timeline: TimelineService,
        repository: InMemoryRepository,
    ) -> None:
        """One transaction: the gap, its two links and their audit lines, or none of them."""
        a, b = await plateau(service, "A"), await plateau(service, "B")
        before_events = len(repository.audit_log.events)
        real = repository.add_relationship
        calls = 0

        async def failing_second(relationship, **kwargs):  # type: ignore[no-untyped-def]
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("the store went away")
            return await real(relationship, **kwargs)

        repository.add_relationship = failing_second  # type: ignore[method-assign]

        with pytest.raises(RuntimeError):
            await timeline.create_gap(a.id, b.id)

        assert [e.element_type for e in repository.elements.values()] == [E.PLATEAU, E.PLATEAU]
        assert repository.relationships == {}
        assert len(repository.audit_log.events) == before_events

    async def test_a_locked_plateau_refuses_and_leaves_nothing(
        self,
        service: ArchitectureService,
        timeline: TimelineService,
        repository: InMemoryRepository,
    ) -> None:
        from dataclasses import replace

        from ea.domain.approval import ApprovalState
        from ea.domain.errors import ElementLockedError

        a, b = await plateau(service, "A"), await plateau(service, "B")
        repository.elements[a.id] = replace(
            repository.elements[a.id], approval_state=ApprovalState.APPROVED, locked=True
        )

        with pytest.raises(ElementLockedError):
            await timeline.create_gap(a.id, b.id)

        assert len(repository.elements) == 2 and repository.relationships == {}


class TestRoadmap:
    async def test_it_holds_plateaus_work_packages_and_deliverables_with_their_dates(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        to_be = await plateau(service, "To-Be", valid_from="2028-01-01")
        wp = await service.create_element(
            element_type=E.WORK_PACKAGE,
            name="Migration",
            properties={"valid_from": "2026-06-01", "valid_until": "2027-12-31"},
        )
        deliverable = await service.create_element(element_type=E.DELIVERABLE, name="Runbook")
        await app(service, "Billing", valid_from="2020-01-01")  # not a roadmap type
        await service.connect(
            relationship_type=R.REALIZATION, source_id=wp.id, target_id=deliverable.id
        )
        await contain(service, to_be, wp)

        roadmap = await timeline.roadmap()

        assert {i.element.name for i in roadmap.items} == {"To-Be", "Migration", "Runbook"}
        by_name = {i.element.name: i for i in roadmap.items}
        assert by_name["Migration"].plateau_ids == (to_be.id,)
        assert not by_name["Runbook"].lifespan.is_dated
        assert (roadmap.window_from, roadmap.window_to) == (date(2026, 6, 1), date(2028, 1, 1))
        assert [(e.relationship_type, e.source_id, e.target_id) for e in roadmap.edges] == [
            (R.REALIZATION, wp.id, deliverable.id)
        ]

    async def test_a_window_leaves_out_what_does_not_overlap_but_keeps_the_undated(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        await service.create_element(
            element_type=E.WORK_PACKAGE, name="Early", properties={"valid_until": "2025-12-31"}
        )
        await service.create_element(
            element_type=E.WORK_PACKAGE, name="Inside", properties={"valid_from": "2026-03-01"}
        )
        await service.create_element(element_type=E.DELIVERABLE, name="Undated")

        roadmap = await timeline.roadmap(date(2026, 1, 1), date(2026, 12, 31))

        assert {i.element.name for i in roadmap.items} == {"Inside", "Undated"}

    async def test_a_window_wider_than_twenty_years_is_refused_before_any_read(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        timeline: TimelineService,
    ) -> None:
        repository.reads.clear()

        with pytest.raises(InvalidTimelineError, match="20 years"):
            await timeline.roadmap(date(2000, 1, 1), date(2030, 1, 1))

        assert repository.reads == []

    async def test_more_items_than_the_ceiling_is_refused_not_cut(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        small = TimelineService(service, repository, max_items=2)
        for name in "abc":
            await service.create_element(element_type=E.WORK_PACKAGE, name=name)

        with pytest.raises(ScopeTooLargeError):
            await small.roadmap()

    async def test_more_links_than_the_ceiling_is_refused_not_cut(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        small = TimelineService(service, repository, max_links=1)
        wp = await service.create_element(element_type=E.WORK_PACKAGE, name="wp")
        for name in "xy":
            d = await service.create_element(element_type=E.DELIVERABLE, name=name)
            await service.connect(relationship_type=R.REALIZATION, source_id=wp.id, target_id=d.id)

        with pytest.raises(ScopeTooLargeError):
            await small.roadmap()

    async def test_a_reader_may_read_it(
        self, service: ArchitectureService, timeline: TimelineService
    ) -> None:
        with acting_as(a_reader()):
            assert (await timeline.roadmap()).items == ()
