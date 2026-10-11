"""Plateaus, gaps and the roadmap against a real PostgreSQL (docs/adr/0061)."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import DuplicateGapError, ElementNotFoundError, ScopeTooLargeError
from ea.domain.model import Element
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.timeline import TimelineService
from tests.conftest import an_editor

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def timeline(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> TimelineService:
    return TimelineService(graph_service, graph_repository)


async def _plateau(service: ArchitectureService, name: str, **dates: str) -> Element:
    return await service.create_element(element_type=E.PLATEAU, name=name, properties=dates or None)


async def _app(service: ArchitectureService, name: str) -> Element:
    return await service.create_element(element_type=E.APPLICATION_COMPONENT, name=name)


async def _contain(service: ArchitectureService, owner: Element, *members: Element) -> None:
    for member in members:
        await service.connect(
            relationship_type=R.COMPOSITION, source_id=owner.id, target_id=member.id
        )


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as connection:
        # `table` is one of the literals below, never input.
        result = await connection.execute(text(f"SELECT count(*) FROM {table}"))  # nosec B608
        return int(result.scalar_one())


async def test_plateaus_come_with_the_number_of_distinct_members(
    graph_service: ArchitectureService, timeline: TimelineService
) -> None:
    to_be = await _plateau(graph_service, "To-Be", valid_from="2028-01-01")
    await _plateau(graph_service, "As-Is", valid_from="2024-01-01")
    one, two = await _app(graph_service, "one"), await _app(graph_service, "two")
    await _contain(graph_service, to_be, one, two)
    await graph_service.connect(
        relationship_type=R.AGGREGATION, source_id=to_be.id, target_id=one.id
    )

    listed = await timeline.list_plateaus()

    assert [(s.element.name, s.member_count) for s in listed] == [("As-Is", 0), ("To-Be", 2)]


async def test_the_comparison_reads_members_and_ignores_other_links(
    graph_service: ArchitectureService, timeline: TimelineService
) -> None:
    a, b = await _plateau(graph_service, "A"), await _plateau(graph_service, "B")
    old = await _app(graph_service, "old")
    kept = await _app(graph_service, "kept")
    new = await _app(graph_service, "new")
    stranger = await _app(graph_service, "stranger")
    await _contain(graph_service, a, old, kept)
    await _contain(graph_service, b, kept, new)
    await graph_service.connect(
        relationship_type=R.ASSOCIATION, source_id=a.id, target_id=stranger.id
    )

    comparison = await timeline.plateau_gap(a.id, b.id)

    assert [e.name for e in comparison.diff.only_in_a] == ["old"]
    assert [e.name for e in comparison.diff.only_in_b] == ["new"]
    assert [e.name for e in comparison.diff.common] == ["kept"]


async def test_the_limit_is_in_the_query_so_a_big_plateau_is_refused_not_loaded(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> None:
    small = TimelineService(graph_service, graph_repository, max_members=2)
    a, b = await _plateau(graph_service, "A"), await _plateau(graph_service, "B")
    await _contain(graph_service, a, *[await _app(graph_service, f"m{i}") for i in range(4)])

    assert len(await graph_repository.members_of(a.id, limit=3)) == 3
    with pytest.raises(ScopeTooLargeError):
        await small.plateau_gap(a.id, b.id)


async def test_creating_the_gap_writes_the_element_two_links_and_their_audit_together(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService, timeline: TimelineService
) -> None:
    a, b = await _plateau(graph_service, "A"), await _plateau(graph_service, "B")
    events_before = await _count(engine_at_head, "audit_events")

    gap = await timeline.create_gap(a.id, b.id)

    assert gap.element_type is E.GAP
    assert await _count(engine_at_head, "audit_events") == events_before + 3
    assert [g.id for g in (await timeline.plateau_gap(a.id, b.id)).gaps] == [gap.id]
    assert [g.id for g in (await timeline.plateau_gap(b.id, a.id)).gaps] == [gap.id]


async def test_a_failure_in_the_middle_leaves_nothing_behind(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService, timeline: TimelineService
) -> None:
    a, b = await _plateau(graph_service, "A"), await _plateau(graph_service, "B")
    elements, links = (
        await _count(engine_at_head, "elements"),
        await _count(engine_at_head, "relationships"),
    )
    events = await _count(engine_at_head, "audit_events")

    # The second plateau is deleted between the check and the link: the association fails.
    real = graph_service.connect
    calls = 0

    async def failing_second(**kwargs):  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ElementNotFoundError("gone")
        return await real(**kwargs)

    graph_service.connect = failing_second  # type: ignore[method-assign]
    with pytest.raises(ElementNotFoundError):
        await timeline.create_gap(a.id, b.id)

    assert await _count(engine_at_head, "elements") == elements
    assert await _count(engine_at_head, "relationships") == links
    assert await _count(engine_at_head, "audit_events") == events


async def test_two_editors_drawing_the_same_gap_at_once_get_one_gap(
    engine_at_head: AsyncEngine, graph_service: ArchitectureService, timeline: TimelineService
) -> None:
    a, b = await _plateau(graph_service, "A"), await _plateau(graph_service, "B")

    async def draw(first: Element, second: Element, name: str) -> object:
        with acting_as(an_editor()):
            try:
                return await timeline.create_gap(first.id, second.id, name=name)
            except DuplicateGapError as refusal:
                return refusal

    results = await asyncio.gather(draw(a, b, "one"), draw(b, a, "two"))

    assert sorted(type(r).__name__ for r in results) == ["DuplicateGapError", "Element"]
    gaps = await timeline.plateau_gap(a.id, b.id)
    assert len(gaps.gaps) == 1


async def test_the_roadmap_is_windowed_in_sql_and_keeps_the_undated(
    graph_service: ArchitectureService,
    graph_repository: PostgresArchitectureRepository,
    timeline: TimelineService,
) -> None:
    for name, props in {
        "early": {"valid_until": "2025-12-31"},
        "inside": {"valid_from": "2026-03-01", "valid_until": "2026-09-30"},
        "late": {"valid_from": "2027-02-01"},
        "edge": {"valid_from": "2026-12-31"},
    }.items():
        await graph_service.create_element(element_type=E.WORK_PACKAGE, name=name, properties=props)
    await graph_service.create_element(element_type=E.DELIVERABLE, name="undated")
    await _app(graph_service, "not on a roadmap")

    roadmap = await timeline.roadmap(date(2026, 1, 1), date(2026, 12, 31))

    assert {i.element.name for i in roadmap.items} == {"inside", "edge", "undated"}
    capped = await graph_repository.roadmap_elements((E.WORK_PACKAGE,), None, None, limit=2)
    assert len(capped) == 2


async def test_the_roadmap_links_are_containment_triggering_and_realization_among_items(
    graph_service: ArchitectureService, timeline: TimelineService
) -> None:
    to_be = await _plateau(graph_service, "To-Be", valid_from="2028-01-01")
    wp = await graph_service.create_element(element_type=E.WORK_PACKAGE, name="wp")
    deliverable = await graph_service.create_element(element_type=E.DELIVERABLE, name="d")
    await _contain(graph_service, to_be, wp)
    await graph_service.connect(
        relationship_type=R.REALIZATION, source_id=wp.id, target_id=deliverable.id
    )
    outsider = await _app(graph_service, "app")
    await graph_service.connect(
        relationship_type=R.ASSOCIATION, source_id=wp.id, target_id=outsider.id
    )

    roadmap = await timeline.roadmap()

    by_name = {i.element.name: i for i in roadmap.items}
    assert by_name["wp"].plateau_ids == (to_be.id,)
    assert [(e.relationship_type, e.source_id) for e in roadmap.edges] == [(R.REALIZATION, wp.id)]
