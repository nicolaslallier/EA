"""`as_of` against a real PostgreSQL: what the recursive walks do at a retired neighbour."""

from __future__ import annotations

from datetime import date

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementFilter
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]

AFTER = date(2028, 1, 1)
BEFORE = date(2027, 6, 1)


async def _chain(
    service: ArchitectureService, **middle: str
) -> tuple[Element, Element, Element, Relationship, Relationship]:
    """a --serving--> b --serving--> c, where `b` may carry a lifespan."""
    a = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="a")
    b = await service.create_element(
        element_type=E.APPLICATION_COMPONENT, name="b", properties=middle or None
    )
    c = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="c")
    ab = await service.connect(relationship_type=R.SERVING, source_id=a.id, target_id=b.id)
    bc = await service.connect(relationship_type=R.SERVING, source_id=b.id, target_id=c.id)
    return a, b, c, ab, bc


class TestLists:
    async def test_the_catalogue_and_its_count_agree_on_the_day(
        self, graph_service: ArchitectureService
    ) -> None:
        await graph_service.create_element(
            element_type=E.NODE, name="old", properties={"valid_until": "2026-12-31"}
        )
        await graph_service.create_element(
            element_type=E.NODE, name="new", properties={"valid_from": "2028-01-01"}
        )
        await graph_service.create_element(element_type=E.NODE, name="always")

        criteria = ElementFilter(as_of=BEFORE)

        assert [e.name for e in await graph_service.list_elements(criteria)] == ["always"]
        assert await graph_service.count_elements(criteria) == 1

    async def test_a_link_with_an_end_out_of_service_is_not_listed(
        self, graph_service: ArchitectureService
    ) -> None:
        _, _, _, ab, bc = await _chain(graph_service, valid_until="2027-12-31")

        before = await graph_service.list_relationships(as_of=BEFORE)
        after = await graph_service.list_relationships(as_of=AFTER)

        assert {link.id for link in before} == {ab.id, bc.id}
        assert after == ()

    async def test_a_link_with_a_lifespan_of_its_own_is_cut_when_it_ends(
        self, graph_service: ArchitectureService
    ) -> None:
        a = await graph_service.create_element(element_type=E.NODE, name="a")
        b = await graph_service.create_element(element_type=E.NODE, name="b")
        link = await graph_service.connect(
            relationship_type=R.ASSOCIATION,
            source_id=a.id,
            target_id=b.id,
            properties={"valid_until": "2026-12-31"},
        )

        last = await graph_service.list_relationships(as_of=date(2026, 12, 31))
        assert [x.id for x in last] == [link.id]
        assert await graph_service.list_relationships(as_of=date(2027, 1, 1)) == ()


class TestWalks:
    async def test_a_retired_neighbour_cuts_the_neighbourhood(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service, valid_until="2027-12-31")

        before = await graph_service.neighbourhood(a.id, depth=3, as_of=BEFORE)
        after = await graph_service.neighbourhood(a.id, depth=3, as_of=AFTER)

        assert {e.name for e in before.elements} == {"a", "b", "c"}
        assert {e.name for e in after.elements} == {"a"}
        assert after.relationships == ()

    async def test_a_retired_neighbour_cuts_the_impact_rather_than_being_skipped(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service, valid_until="2027-12-31")

        before = await graph_service.impact_of(a.id, depth=5, as_of=BEFORE)
        after = await graph_service.impact_of(a.id, depth=5, as_of=AFTER)

        assert {e.name for e in before.elements} == {"a", "b", "c"}
        assert {e.name for e in after.elements} == {"a"}  # c is not reached through a dead b

    async def test_a_link_out_of_period_cuts_the_path_even_between_live_ends(
        self, graph_service: ArchitectureService
    ) -> None:
        a = await graph_service.create_element(element_type=E.NODE, name="a")
        b = await graph_service.create_element(element_type=E.NODE, name="b")
        await graph_service.connect(
            relationship_type=R.ASSOCIATION,
            source_id=a.id,
            target_id=b.id,
            properties={"valid_until": "2026-12-31"},
        )

        view = await graph_service.neighbourhood(a.id, depth=2, as_of=BEFORE)

        assert {e.name for e in view.elements} == {"a"}

    async def test_an_undated_chain_is_untouched_by_any_day(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service)

        view = await graph_service.neighbourhood(a.id, depth=3, as_of=date(1999, 1, 1))

        assert {e.name for e in view.elements} == {"a", "b", "c"}

    async def test_a_root_out_of_period_reaches_nothing_and_is_not_a_404(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service)
        retired = await graph_service.update_element(a.id, properties={"valid_until": "2026-12-31"})

        for view in (
            await graph_service.neighbourhood(retired.id, depth=3, as_of=BEFORE),
            await graph_service.impact_of(retired.id, depth=3, as_of=BEFORE),
        ):
            assert view.elements == () and view.relationships == ()

    async def test_the_boundary_day_is_inclusive_in_the_walk(
        self, graph_service: ArchitectureService
    ) -> None:
        a, *_ = await _chain(graph_service, valid_until="2027-12-31")

        last = await graph_service.neighbourhood(a.id, depth=2, as_of=date(2027, 12, 31))
        next_day = await graph_service.neighbourhood(a.id, depth=2, as_of=date(2028, 1, 1))

        assert len(last.elements) == 3 and len(next_day.elements) == 1


class TestNoDayAskedChangesNothing:
    async def test_the_answers_are_the_same_with_and_without_as_of_none(
        self, graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
    ) -> None:
        a, *_ = await _chain(graph_service, valid_until="2020-01-01")  # retired long ago

        plain = await graph_repository.neighbourhood(a.id, depth=3)
        explicit_none = await graph_repository.neighbourhood(a.id, depth=3, as_of=None)
        impact_plain = await graph_repository.impacted_by(a.id, depth=3)
        impact_none = await graph_repository.impacted_by(a.id, depth=3, as_of=None)

        assert [e.id for e in plain.elements] == [e.id for e in explicit_none.elements]
        assert len(plain.elements) == 3 and len(plain.relationships) == 2
        assert [e.id for e in impact_plain.elements] == [e.id for e in impact_none.elements]
