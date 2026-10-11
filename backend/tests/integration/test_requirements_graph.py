"""The requirements read against a real PostgreSQL.

Two claims are about the database and cannot be made with a double: that the
read returns the requirements, the realizations pointing at one and the
elements at their other end — and nothing else — and that, with a test
realizing a requirement, the impact analysis never drags the tests into the
waves of a requirement or of its design.
"""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.model import Element
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def realize(service: ArchitectureService, source: Element, target: Element) -> None:
    await service.connect(relationship_type=R.REALIZATION, source_id=source.id, target_id=target.id)


class TestTraceability:
    async def test_it_returns_the_requirements_and_what_realizes_them_only(
        self, graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
    ) -> None:
        requirement = await graph_service.create_element(element_type=E.REQUIREMENT, name="R1")
        design = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="D")
        test = await graph_service.create_element(
            element_type=E.DELIVERABLE, name="T", properties={"test_result": "passed"}
        )
        goal = await graph_service.create_element(element_type=E.GOAL, name="G")
        await graph_service.create_element(element_type=E.BUSINESS_ACTOR, name="Loner")
        await realize(graph_service, design, requirement)
        await realize(graph_service, test, requirement)
        await realize(graph_service, requirement, goal)  # target is a goal: not traced

        view = await graph_repository.traceability(requirement_limit=100, link_limit=100)

        assert {element.name for element in view.elements} == {"R1", "D", "T"}
        assert {link.source_id for link in view.relationships} == {design.id, test.id}

    async def test_an_empty_catalogue_gives_an_empty_view(
        self, graph_repository: PostgresArchitectureRepository
    ) -> None:
        view = await graph_repository.traceability(requirement_limit=100, link_limit=100)

        assert view.elements == ()
        assert view.relationships == ()


class TestTheBounds:
    async def test_it_reads_at_most_the_limits_it_is_given(
        self, graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
    ) -> None:
        design = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="D")
        for number in range(5):
            requirement = await graph_service.create_element(
                element_type=E.REQUIREMENT, name=f"R{number}"
            )
            await realize(graph_service, design, requirement)

        view = await graph_repository.traceability(requirement_limit=3, link_limit=2)

        assert len(view.relationships) == 2
        requirements = [e for e in view.elements if e.element_type is E.REQUIREMENT]
        assert len(requirements) == 3


class TestImpact:
    async def test_impact_never_reaches_the_tests_of_a_requirement(
        self, graph_service: ArchitectureService
    ) -> None:
        """`realization` runs along the arrow, source to target; a test is a source.

        So the waves of a design element or of a requirement hold the
        requirement and the goal above it, never the tests beside it. What a
        failing test reaches is its requirement — pinned here so the
        convention of docs/adr/0064 is a tested fact, not a habit.
        """
        design = await graph_service.create_element(element_type=E.APPLICATION_COMPONENT, name="D")
        requirement = await graph_service.create_element(element_type=E.REQUIREMENT, name="R")
        goal = await graph_service.create_element(element_type=E.GOAL, name="G")
        test = await graph_service.create_element(
            element_type=E.DELIVERABLE, name="T", properties={"test_result": "failed"}
        )
        await realize(graph_service, design, requirement)
        await realize(graph_service, requirement, goal)
        await realize(graph_service, test, requirement)

        from_design = await graph_service.impact_of(design.id)
        from_requirement = await graph_service.impact_of(requirement.id)
        from_test = await graph_service.impact_of(test.id)

        assert {e.name for e in from_design.elements} == {"D", "R", "G"}
        assert {e.name for e in from_requirement.elements} == {"R", "G"}
        assert {e.name for e in from_test.elements} == {"T", "R", "G"}
