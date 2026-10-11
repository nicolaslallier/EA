"""The requirements use case, over the same in-memory graph as the others."""

from __future__ import annotations

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import AnalysisBusyError, NotAuthenticatedError, ScopeTooLargeError
from ea.domain.requirements import Gap
from ea.services.analysis import analysis_admission
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.requirements import RequirementService
from tests.conftest import InMemoryRepository, a_reader, an_editor


@pytest.fixture
def requirements(repository: InMemoryRepository) -> RequirementService:
    return RequirementService(repository)


@pytest.mark.asyncio
class TestCoverage:
    async def test_it_reads_what_the_architecture_service_wrote(
        self, service: ArchitectureService, requirements: RequirementService
    ) -> None:
        requirement = await service.create_element(
            element_type=E.REQUIREMENT, name="R1", properties={"req_status": "approved"}
        )
        design = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")
        await service.connect(
            relationship_type=R.REALIZATION, source_id=design.id, target_id=requirement.id
        )

        coverage = await requirements.coverage()

        (trace,) = coverage.traces
        assert [element.name for element in trace.designed_by] == ["Billing"]
        assert trace.gap is Gap.VERIFICATION

    async def test_the_filters_reach_the_computation(
        self, service: ArchitectureService, requirements: RequirementService
    ) -> None:
        await service.create_element(
            element_type=E.REQUIREMENT, name="A", properties={"req_status": "approved"}
        )
        await service.create_element(
            element_type=E.REQUIREMENT, name="B", properties={"req_status": "proposed"}
        )

        coverage = await requirements.coverage(status="proposed")

        assert [trace.requirement.name for trace in coverage.traces] == ["B"]

    async def test_a_reader_may_read_the_coverage(self, requirements: RequirementService) -> None:
        with acting_as(a_reader()):
            coverage = await requirements.coverage()

        assert coverage.total == 0

    async def test_nobody_calling_is_refused(
        self, requirements: RequirementService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await requirements.coverage()


@pytest.mark.asyncio
class TestTheBounds:
    async def test_too_many_requirements_are_refused_not_cut(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        for number in range(3):
            await service.create_element(element_type=E.REQUIREMENT, name=f"R{number}")

        with pytest.raises(ScopeTooLargeError):
            await RequirementService(repository, max_requirements=2).coverage()

        assert (await RequirementService(repository, max_requirements=3).coverage()).total == 3

    async def test_too_many_realizations_are_refused_not_cut(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        requirement = await service.create_element(element_type=E.REQUIREMENT, name="R")
        for number in range(3):
            design = await service.create_element(
                element_type=E.APPLICATION_COMPONENT, name=f"D{number}"
            )
            await service.connect(
                relationship_type=R.REALIZATION, source_id=design.id, target_id=requirement.id
            )

        with pytest.raises(ScopeTooLargeError):
            await RequirementService(repository, max_links=2).coverage()

    async def test_the_computation_holds_a_place_of_the_admission(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        admission = analysis_admission(limit=1)
        busy = RequirementService(repository, admission=admission)

        async with admission.lease("someone"):
            with acting_as(an_editor(subject="other")), pytest.raises(AnalysisBusyError):
                await busy.coverage()
        assert (await busy.coverage()).total == 0


@pytest.mark.asyncio
async def test_an_unknown_filter_is_refused_before_anything_is_read(
    repository: InMemoryRepository,
) -> None:
    class Unreadable:
        async def traceability(self, **_: int) -> None:
            raise AssertionError("nothing should be read")

    with pytest.raises(ValueError, match="unknown status"):
        await RequirementService(Unreadable()).coverage(status="banana")  # type: ignore[arg-type]
