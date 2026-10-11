"""What the analysis service does with a graph: the matrix first, the rest in later lots."""

from __future__ import annotations

from uuid import uuid4

import pytest

from ea.domain.analytics import Dimension, Metric
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import (
    AnalysisBusyError,
    ElementNotFoundError,
    InvalidAnalysisError,
    MatrixAxisTooLargeError,
    NotAuthenticatedError,
    ScopeTooLargeError,
)
from ea.domain.matrix import MAX_MATRIX_AXIS, AxisSelection
from ea.domain.quality import QualityParams, QualityReport, RuleCode, Severity
from ea.services.analysis import AnalysisService, analysis_admission
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from tests.conftest import InMemoryRepository, a_reader


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


@pytest.mark.asyncio
class TestTheQualityRun:
    async def test_a_lone_bare_element_is_an_orphan_without_description(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="Loner")

        page = await analysis.check()

        assert {f.rule for f in page.findings} == {RuleCode.ORPHAN, RuleCode.NO_DESCRIPTION}
        assert {c.rule: c.count for c in page.report.counts}[RuleCode.ORPHAN] == 1

    async def test_an_empty_model_is_clean(self, analysis: AnalysisService) -> None:
        page = await analysis.check()

        assert page.findings == ()
        assert all(count.count == 0 for count in page.report.counts)

    async def test_links_count_against_the_orphan_rule(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await two_linked(service)

        page = await analysis.check(code=RuleCode.ORPHAN)

        assert page.findings == ()

    async def test_filters_and_paging_are_applied(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        for index in range(3):
            await service.create_element(element_type=E.NODE, name=f"n{index}")

        page = await analysis.check(severity=Severity.INFO, limit=2)

        assert len(page.findings) == 2
        assert page.next_cursor == 2

    async def test_a_scope_over_the_ceiling_is_refused_not_truncated(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        small = AnalysisService(service, repository, params=QualityParams(max_elements=2))
        for index in range(3):
            await service.create_element(element_type=E.NODE, name=f"n{index}")

        with pytest.raises(ScopeTooLargeError, match="2"):
            await small.check()

    async def test_a_reader_may_run_it(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="Loner")

        with acting_as(a_reader()):
            page = await analysis.check()

        assert page.findings

    async def test_nobody_runs_nothing(
        self, analysis: AnalysisService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await analysis.check()

    async def test_a_run_leaves_a_trace_with_its_duration(
        self, analysis: AnalysisService, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level("INFO", logger="ea.services.analysis"):
            await analysis.check()

        record = next(r for r in caplog.records if getattr(r, "action", "") == "quality_run")
        assert record.findings == 0  # type: ignore[attr-defined]
        assert record.duration_ms >= 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
class TestTheAdmission:
    """A run reads the whole model, so only so many may be in flight at once."""

    async def test_a_run_beyond_the_ceiling_is_refused_not_queued(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        busy = analysis_admission(limit=1)
        guarded = AnalysisService(service, repository, admission=busy)

        async with busy.lease("somebody else"):
            with pytest.raises(AnalysisBusyError):
                await guarded.check()

    async def test_the_place_is_given_back_when_the_run_ends(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        busy = analysis_admission(limit=1)
        guarded = AnalysisService(service, repository, admission=busy)

        await guarded.check()
        await guarded.check()

        assert busy.in_flight == 0


@pytest.mark.asyncio
class TestTheRunStaysOffTheEventLoop:
    """A rule run is CPU-bound: it runs in a thread, and its place outlives its deadline."""

    async def test_the_rules_run_in_a_worker_thread(
        self,
        analysis: AnalysisService,
        service: ArchitectureService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import threading

        from ea.domain import quality as rules

        seen: list[str] = []
        real = rules.run_checks

        def spy(*args: object, **kwargs: object) -> object:
            seen.append(threading.current_thread().name)
            return real(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr("ea.services.analysis.run_checks", spy)
        await service.create_element(element_type=E.NODE, name="n")

        await analysis.check()

        assert seen and seen[0] != threading.main_thread().name

    async def test_an_expired_lease_whose_run_is_still_going_keeps_its_place(
        self,
        service: ArchitectureService,
        repository: InMemoryRepository,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import asyncio
        import threading

        now = [0.0]
        busy = analysis_admission(limit=1, clock=lambda: now[0])
        guarded = AnalysisService(service, repository, admission=busy)
        started, release = threading.Event(), threading.Event()

        def slow(*args: object, **kwargs: object) -> object:
            started.set()
            release.wait(5)
            return QualityReport(findings=())

        monkeypatch.setattr("ea.services.analysis.run_checks", slow)
        run = asyncio.create_task(guarded.check())
        while not started.is_set():
            await asyncio.sleep(0.01)

        now[0] = 10_000.0  # far past the lease deadline, the thread still computing
        with pytest.raises(AnalysisBusyError):
            await guarded.check()

        release.set()
        await run
        assert busy.in_flight == 0


@pytest.mark.asyncio
class TestTheAggregates:
    async def a_model(self, service: ArchitectureService) -> None:
        await service.create_element(
            element_type=E.APPLICATION_COMPONENT,
            name="Billing",
            properties={"cost": "1 200,50", "lifecycle": "en production"},
        )
        await service.create_element(
            element_type=E.APPLICATION_COMPONENT,
            name="CRM",
            properties={"cost": "300", "lifecycle": "retiré"},
        )
        await service.create_element(
            element_type=E.BUSINESS_PROCESS, name="Order", properties={"cost": "1 200 €"}
        )

    async def test_counting_by_layer(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        result = await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)

        assert {b.keys: b.count for b in result.buckets} == {("application",): 2, ("business",): 1}

    async def test_summing_a_property_reports_what_it_could_not_read(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        result = await analysis.aggregate(Dimension.parse("layer"), Metric.SUM, "cost")

        assert {b.keys: b.value for b in result.buckets} == {
            ("application",): 1500.5,
            ("business",): None,
        }
        assert result.unreadable == 1

    async def test_a_heat_map_crosses_two_dimensions(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        result = await analysis.heatmap(
            Dimension.parse("layer"), Dimension.parse("property:lifecycle"), Metric.COUNT
        )

        assert {b.keys: b.count for b in result.buckets} == {
            ("application", "en production"): 1,
            ("application", "retiré"): 1,
            ("business", "__unset__"): 1,
        }

    async def test_a_sum_without_a_property_is_a_refused_question(
        self, analysis: AnalysisService
    ) -> None:
        with pytest.raises(InvalidAnalysisError, match="sum"):
            await analysis.aggregate(Dimension.parse("layer"), Metric.SUM)

    async def test_a_reader_may_ask(
        self, analysis: AnalysisService, service: ArchitectureService
    ) -> None:
        await self.a_model(service)

        with acting_as(a_reader()):
            result = await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)

        assert result.total == 3

    async def test_an_empty_catalogue_is_an_empty_answer(self, analysis: AnalysisService) -> None:
        result = await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)

        assert (result.buckets, result.total) == ((), 0)

    async def test_nobody_asks_nothing(
        self, analysis: AnalysisService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await analysis.aggregate(Dimension.parse("layer"), Metric.COUNT)


@pytest.mark.asyncio
class TestTheAggregatesAreAdmitted:
    async def test_an_aggregate_beyond_the_ceiling_is_refused(
        self, service: ArchitectureService, repository: InMemoryRepository
    ) -> None:
        busy = analysis_admission(limit=1)
        guarded = AnalysisService(service, repository, admission=busy)

        async with busy.lease("somebody else"):
            with pytest.raises(AnalysisBusyError):
                await guarded.aggregate(Dimension.parse("layer"), Metric.COUNT)
            with pytest.raises(AnalysisBusyError):
                await guarded.heatmap(
                    Dimension.parse("layer"), Dimension.parse("element_type"), Metric.COUNT
                )
