"""What the analysis service does with a graph: the matrix first, the rest in later lots."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import (
    ElementNotFoundError,
    MatrixAxisTooLargeError,
    NotAuthenticatedError,
)
from ea.domain.matrix import MAX_MATRIX_AXIS, AxisSelection
from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import a_reader


async def two_linked(service: ArchitectureService):  # type: ignore[no-untyped-def]
    api = await service.create_element(element_type=E.APPLICATION_SERVICE, name="Invoice API")
    process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Order to cash")
    link = await service.connect(
        relationship_type=R.SERVING, source_id=api.id, target_id=process.id
    )
    return api, process, link


@pytest.mark.asyncio
class TestTheMatrix:
    async def test_named_elements_make_a_grid_with_their_link(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        api, process, link = await two_linked(service)

        matrix = await analysis.matrix(
            AxisSelection(element_ids=(api.id,)), AxisSelection(element_ids=(process.id,))
        )

        assert [c.links for c in matrix.cells] == [(link,)]

    async def test_an_empty_catalogue_gives_an_empty_grid(self, analysis: AnalysisService) -> None:
        matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert (matrix.rows.elements, matrix.cols.elements, matrix.cells) == ((), (), ())
        assert not matrix.rows.truncated

    async def test_no_criteria_means_the_whole_catalogue(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await two_linked(service)

        matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert len(matrix.rows.elements) == len(matrix.cols.elements) == 2
        assert len(matrix.cells) == 1

    async def test_an_unknown_id_is_named_not_ignored(self, analysis: AnalysisService) -> None:
        missing = uuid4()

        with pytest.raises(ElementNotFoundError, match=str(missing)):
            await analysis.matrix(AxisSelection(element_ids=(missing,)), AxisSelection())

    async def test_named_elements_over_the_ceiling_are_refused(
        self, analysis: AnalysisService
    ) -> None:
        too_many = tuple(uuid4() for _ in range(MAX_MATRIX_AXIS + 1))

        with pytest.raises(MatrixAxisTooLargeError):
            await analysis.matrix(AxisSelection(element_ids=too_many), AxisSelection())

    async def test_a_catalogue_axis_over_the_ceiling_is_cut_and_counted(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        for index in range(MAX_MATRIX_AXIS + 3):
            await service.create_element(element_type=E.NODE, name=f"node-{index:03}")

        matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert len(matrix.rows.elements) == MAX_MATRIX_AXIS
        assert matrix.rows.total == MAX_MATRIX_AXIS + 3
        assert matrix.rows.truncated

    async def test_a_reader_may_read_the_grid(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await two_linked(service)

        with acting_as(a_reader()):
            matrix = await analysis.matrix(AxisSelection(), AxisSelection())

        assert len(matrix.cells) == 1

    async def test_nobody_reads_nothing(
        self, analysis: AnalysisService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await analysis.matrix(AxisSelection(), AxisSelection())


@pytest.mark.asyncio
class TestOneCell:
    async def test_it_lists_the_links_and_what_may_still_be_added(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        api, process, link = await two_linked(service)

        cell = await analysis.cell(api.id, process.id)

        assert cell.links == (link,)
        assert R.SERVING in cell.allowed

    async def test_a_pair_without_links_still_says_what_is_permitted(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        data = await service.create_element(element_type=E.DATA_OBJECT, name="Invoice")
        process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Order")

        cell = await analysis.cell(data.id, process.id)

        assert cell.links == ()
        assert R.SERVING not in cell.allowed
        assert R.ASSOCIATION in cell.allowed

    async def test_the_diagonal_never_offers_a_containment_of_itself(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        component = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="A")

        cell = await analysis.cell(component.id, component.id)

        assert R.COMPOSITION not in cell.allowed
        assert R.ASSOCIATION in cell.allowed

    async def test_an_unknown_end_is_a_not_found(self, analysis: AnalysisService) -> None:
        with pytest.raises(ElementNotFoundError):
            await analysis.cell(uuid4(), uuid4())
