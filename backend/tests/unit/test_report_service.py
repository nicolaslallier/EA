"""The report use cases against in-memory stores, a frozen clock and a fake monotonic clock."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import (
    DiagramNotFoundError,
    DocumentNotFoundError,
    FileStorageUnavailableError,
    NotAuthenticatedError,
    NotAuthorisedError,
    PackageNotFoundError,
    ReportBusyError,
    ReportFormatUnavailableError,
    ReportTooLargeError,
    UnknownReportTemplateError,
    UnsupportedReportSubjectError,
)
from ea.domain.ports import GraphView
from ea.domain.reports import (
    MAX_REPORT_DIAGRAMS,
    MAX_REPORT_ELEMENTS,
    ReportModel,
    ReportRequest,
    ReportSubject,
)
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.files import FileService
from ea.services.packages import PackageService
from ea.services.reports import ReportService, report_admission
from tests.conftest import FIXED_NOW, InMemoryObjectStore, a_reader

pytestmark = pytest.mark.asyncio


class Ticking:
    """A monotonic clock that moves `step` seconds every time it is read."""

    def __init__(self, step: float) -> None:
        self.now = 0.0
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


@pytest.fixture
def reports(
    diagram_service: DiagramService,
    service: ArchitectureService,
    document_service_without_an_index: DocumentService,
    file_service: FileService,
) -> ReportService:
    return ReportService(
        diagram_service,
        service,
        document_service_without_an_index,
        file_service,
        clock=lambda: FIXED_NOW,
        new_id=lambda: "ab12cd34",
    )


async def a_diagram(
    service: ArchitectureService, diagrams: DiagramService, *names: str, title: str = "Vente"
) -> tuple[object, list]:  # type: ignore[type-arg]
    elements = [
        await service.create_element(element_type=ElementType.APPLICATION_COMPONENT, name=name)
        for name in names
    ]
    diagram = await diagrams.create(name=title)
    await diagrams.replace_layout(
        diagram.id,
        revision=0,
        nodes=[DiagramNode(e.id, 10.0 * i, 10.0) for i, e in enumerate(elements)],
    )
    return diagram, elements


def request_for(
    diagram: object, fmt: str = "html", template: str = "fiche-diagramme"
) -> ReportRequest:
    return ReportRequest(template, ReportSubject("diagram", diagram.id), fmt)  # type: ignore[attr-defined,arg-type]


class TestListingTemplates:
    async def test_a_caller_sees_the_built_in_templates_and_the_formats_served(
        self, reports: ReportService
    ) -> None:
        assert [t.id for t in await reports.list_templates()] == [
            "fiche-diagramme",
            "inventaire-paquet",
        ]
        assert reports.formats == ("html",)

    async def test_nobody_calling_is_refused(
        self, reports: ReportService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await reports.list_templates()


class TestBuilding:
    async def test_a_reader_gets_the_html_of_a_diagram_and_nothing_is_stored(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: object,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "Facturation")

        with acting_as(a_reader()):
            rendered = await reports.build(request_for(diagram))

        assert rendered.content_type == "text/html; charset=utf-8"
        assert rendered.filename == "2026-09-07-vente-fiche-diagramme.html"
        assert b"Facturation" in rendered.content
        assert b"<svg" in rendered.content
        assert files_store.objects == {}  # type: ignore[attr-defined]

    async def test_markup_in_a_name_is_text_in_the_whole_report(
        self, service: ArchitectureService, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, '<script>alert(1)</script> & "x"', title="<b>T</b>"
        )

        html = (await reports.build(request_for(diagram))).content.decode()

        assert "<script>" not in html
        assert "<b>T</b>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html

    async def test_a_diagram_without_elements_still_gives_a_report(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="Vide")

        html = (await reports.build(request_for(diagram))).content.decode()

        assert "Aucun élément." in html
        assert "Aucune relation." in html

    async def test_an_unknown_diagram_is_not_found(self, reports: ReportService) -> None:
        ghost = ReportRequest("fiche-diagramme", ReportSubject("diagram", uuid4()), "html")

        with pytest.raises(DiagramNotFoundError):
            await reports.build(ghost)

    async def test_an_unknown_template_is_refused(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="D")

        with pytest.raises(UnknownReportTemplateError):
            await reports.build(request_for(diagram, template="nope"))

    async def test_a_subject_the_template_does_not_take_is_refused(
        self, reports: ReportService
    ) -> None:
        request = ReportRequest("fiche-diagramme", ReportSubject("package", uuid4()), "html")

        with pytest.raises(UnsupportedReportSubjectError):
            await reports.build(request)

    async def test_a_format_the_deployment_cannot_produce_is_a_typed_refusal(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="D")

        with pytest.raises(ReportFormatUnavailableError, match="pdf"):
            await reports.build(request_for(diagram, "pdf"))

    async def test_nobody_calling_is_refused(
        self, reports: ReportService, nobody_calling: None
    ) -> None:
        ghost = ReportRequest("fiche-diagramme", ReportSubject("diagram", uuid4()), "html")

        with pytest.raises(NotAuthenticatedError):
            await reports.build(ghost)


class TestTheCeilings:
    async def test_past_500_elements_it_is_refused_not_truncated(
        self, service: ArchitectureService, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS + 1)]
        )

        with pytest.raises(ReportTooLargeError, match="500") as caught:
            await reports.build(request_for(diagram))

        assert "narrow" in str(caught.value) or "split" in str(caught.value)

    async def test_exactly_500_elements_is_allowed(
        self, service: ArchitectureService, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS)]
        )

        assert (await reports.build(request_for(diagram))).content

    async def test_documents_past_2_mib_in_total_are_refused(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        document_service_without_an_index: DocumentService,
        reports: ReportService,
    ) -> None:
        diagram, (element,) = await a_diagram(service, diagram_service, "Facturation")
        for number in range(3):  # 3 x 900 000 bytes > 2 MiB
            await document_service_without_an_index.attach_text(
                element.id, filename=f"d{number}.md", content="mot " * 225_000
            )

        with pytest.raises(ReportTooLargeError, match="2 MiB"):
            await reports.build(request_for(diagram))

    async def test_a_document_deleted_while_rendering_is_left_out_not_fatal(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        document_service_without_an_index: DocumentService,
        file_service: FileService,
    ) -> None:
        diagram, (element,) = await a_diagram(service, diagram_service, "Facturation")
        await document_service_without_an_index.attach_text(
            element.id, filename="r.md", content="# Run\n"
        )

        class Vanishing:
            async def list_for_element(self, element_id):  # type: ignore[no-untyped-def]
                return await document_service_without_an_index.list_for_element(element_id)

            async def get(self, document_id):  # type: ignore[no-untyped-def]
                raise DocumentNotFoundError("gone")

        reports = ReportService(diagram_service, service, Vanishing(), file_service)  # type: ignore[arg-type]

        html = (await reports.build(request_for(diagram))).content.decode()

        assert "Aucun document." in html
        assert "Facturation" in html

    async def test_a_slow_report_is_stopped_by_the_clock_not_by_sleeping(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        slow = ReportService(diagram_service, service, None, file_service, monotonic=Ticking(31.0))

        with pytest.raises(ReportTooLargeError, match="30 seconds"):
            await slow.build(request_for(diagram))

    async def test_a_fast_clock_lets_it_through(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        quick = ReportService(diagram_service, service, None, file_service, monotonic=Ticking(0.0))

        assert (await quick.build(request_for(diagram))).content


class TestOneAtATime:
    async def test_a_second_report_of_the_same_caller_is_refused_while_the_first_runs(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        entered, release = threading.Event(), threading.Event()

        def blocking(model: ReportModel) -> bytes:
            entered.set()
            release.wait(5)
            return b"%PDF-fake"

        reports = ReportService(
            diagram_service,
            service,
            None,
            file_service,
            admission=report_admission(),
            extra_renderers={"pdf": blocking},
        )
        first = asyncio.create_task(reports.build(request_for(diagram, "pdf")))
        await asyncio.to_thread(entered.wait, 5)

        with pytest.raises(ReportBusyError):
            await reports.build(request_for(diagram))
        with acting_as(a_reader()):  # another caller is not held up
            assert (await reports.build(request_for(diagram))).content
        release.set()
        assert (await first).content == b"%PDF-fake"
        assert (await reports.build(request_for(diagram))).content  # the slot is free again

    async def test_a_refused_report_frees_the_slot(
        self, diagram_service: DiagramService, reports: ReportService
    ) -> None:
        diagram = await diagram_service.create(name="D")
        with pytest.raises(UnknownReportTemplateError):
            await reports.build(request_for(diagram, template="nope"))

        assert (await reports.build(request_for(diagram))).content


class TestDepositing:
    async def test_an_editor_deposits_under_reports_with_a_filled_record(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "Facturation")

        stored = await reports.deposit(request_for(diagram))

        key = "reports/2026/2026-09-07-vente-fiche-diagramme-ab12cd34.html"
        assert stored.key == key
        assert key in files_store.objects
        assert files_store.objects[key][1] == "text/html; charset=utf-8"
        record = stored.metadata
        assert record is not None
        assert record.uploaded_by == "editor"
        assert record.details.title == "Rapport Fiche de diagramme — Vente"
        assert record.details.tags == ("rapport", "fiche-diagramme")
        assert f"diagram:{diagram.id}" in record.details.description  # type: ignore[attr-defined]
        assert "v1" in record.details.description

    async def test_two_deposits_are_two_files(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        ids = iter(["aaaaaaaa", "bbbbbbbb"])
        reports = ReportService(
            diagram_service,
            service,
            None,
            file_service,
            clock=lambda: FIXED_NOW,
            new_id=lambda: next(ids),
        )

        first = await reports.deposit(request_for(diagram))
        second = await reports.deposit(request_for(diagram))

        assert first.key != second.key
        assert len(files_store.objects) == 2

    async def test_a_reader_cannot_deposit_and_nothing_is_written(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")

        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await reports.deposit(request_for(diagram))

        assert files_store.objects == {}

    async def test_a_report_past_the_ceiling_writes_no_object(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        files_store: InMemoryObjectStore,
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS + 1)]
        )

        with pytest.raises(ReportTooLargeError):
            await reports.deposit(request_for(diagram))

        assert files_store.objects == {}

    async def test_a_very_long_subject_still_fits_the_200_character_title(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A", title="D" * 200)

        stored = await reports.deposit(request_for(diagram))

        assert stored.metadata is not None
        assert len(stored.metadata.details.title) <= 200


class TestTheAuditLine:
    async def test_one_line_per_report_says_whether_it_was_deposited(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await reports.build(request_for(diagram))
            await reports.deposit(request_for(diagram))

        lines = [r for r in caplog.records if getattr(r, "action", "") == "report_generated"]
        assert [line.deposited for line in lines] == [False, True]  # type: ignore[attr-defined]
        assert lines[0].template == "fiche-diagramme"  # type: ignore[attr-defined]
        assert lines[0].report_format == "html"  # type: ignore[attr-defined]
        assert lines[0].bytes > 0  # type: ignore[attr-defined]


def _package_request(package_id: UUID) -> ReportRequest:
    return ReportRequest("inventaire-paquet", ReportSubject("package", package_id), "html")


class TestPackages:
    async def test_a_package_report_covers_its_elements_and_its_diagrams(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        package_service: PackageService,
        file_service: FileService,
    ) -> None:
        package = await package_service.create(name="Finance", description="Le SI finance")
        inside = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Facturation"
        )
        await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Hors-paquet"
        )
        await package_service.move_elements([inside.id], package.package.id)
        diagram = await diagram_service.create(name="Vue finance", package_id=package.package.id)
        await diagram_service.replace_layout(
            diagram.id, revision=0, nodes=[DiagramNode(inside.id, 10.0, 10.0)]
        )
        reports = ReportService(
            diagram_service, service, None, file_service, packages=package_service
        )

        html = (await reports.build(_package_request(package.package.id))).content.decode()

        assert "Finance" in html
        assert "Facturation" in html
        assert "Hors-paquet" not in html
        assert "<svg" in html

    async def test_the_sub_packages_are_part_of_the_package(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        package_service: PackageService,
        file_service: FileService,
    ) -> None:
        parent = await package_service.create(name="Finance")
        child = await package_service.create(name="Compta", parent_id=parent.package.id)
        element = await service.create_element(
            element_type=ElementType.APPLICATION_COMPONENT, name="Grand livre"
        )
        await package_service.move_elements([element.id], child.package.id)
        reports = ReportService(
            diagram_service, service, None, file_service, packages=package_service
        )

        built = await reports.build(_package_request(parent.package.id))

        assert b"Grand livre" in built.content

    async def test_more_than_500_elements_in_a_package_is_refused(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        package_service: PackageService,
        file_service: FileService,
    ) -> None:
        package = await package_service.create(name="Gros")
        made = [
            await service.create_element(
                element_type=ElementType.APPLICATION_COMPONENT, name=f"E{i}"
            )
            for i in range(MAX_REPORT_ELEMENTS + 1)
        ]
        for start in range(0, len(made), 100):  # a move takes at most 100 elements
            await package_service.move_elements(
                [e.id for e in made[start : start + 100]], package.package.id
            )
        reports = ReportService(
            diagram_service, service, None, file_service, packages=package_service
        )

        with pytest.raises(ReportTooLargeError, match="500"):
            await reports.build(_package_request(package.package.id))

    async def test_more_than_20_diagrams_is_refused(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        package_service: PackageService,
        file_service: FileService,
    ) -> None:
        package = await package_service.create(name="Vues")
        for i in range(MAX_REPORT_DIAGRAMS + 1):
            await diagram_service.create(name=f"D{i}", package_id=package.package.id)
        reports = ReportService(
            diagram_service, service, None, file_service, packages=package_service
        )

        with pytest.raises(ReportTooLargeError, match="20"):
            await reports.build(_package_request(package.package.id))

    async def test_an_unknown_package_is_not_found(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        package_service: PackageService,
        file_service: FileService,
    ) -> None:
        reports = ReportService(
            diagram_service, service, None, file_service, packages=package_service
        )

        with pytest.raises(PackageNotFoundError):
            await reports.build(_package_request(uuid4()))

    async def test_without_the_packages_service_the_kind_is_unsupported(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        reports = ReportService(diagram_service, service, None, file_service)

        with pytest.raises(UnsupportedReportSubjectError):
            await reports.build(_package_request(uuid4()))


class TestWhatIsBoundedBeforeItIsRead:
    async def test_a_diagram_with_more_links_than_the_ceiling_is_refused(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, (a, b) = await a_diagram(service, diagram_service, "A", "B")
        for _ in range(3):
            await service.connect(
                relationship_type=RelationshipType.ASSOCIATION, source_id=a.id, target_id=b.id
            )
        reports = ReportService(diagram_service, service, None, file_service, max_relationships=2)

        with pytest.raises(ReportTooLargeError, match="relationships"):
            await reports.build(request_for(diagram))

    async def test_the_graph_of_an_oversized_diagram_is_never_read(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"E{i}" for i in range(MAX_REPORT_ELEMENTS + 1)]
        )
        read: list[int] = []
        original = service.view_of

        async def spying(ids: Sequence[UUID], **kwargs: object) -> GraphView:
            read.append(len(ids))
            return await original(ids, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "view_of", spying)

        with pytest.raises(ReportTooLargeError):
            await reports.build(request_for(diagram))

        assert read == []  # refused on the header's count, before the graph was read


class TestThePlaces:
    async def test_the_places_are_returned_whatever_happens(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        admission = report_admission()
        reports = ReportService(diagram_service, service, None, file_service, admission=admission)
        diagram = await diagram_service.create(name="D")
        with pytest.raises(UnknownReportTemplateError):
            await reports.build(request_for(diagram, template="nope"))
        await reports.build(request_for(diagram))

        assert admission.in_flight == 0

    async def test_the_global_ceiling_refuses_whoever_asks(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        admission = report_admission(limit=1)
        reports = ReportService(diagram_service, service, None, file_service, admission=admission)
        diagram = await diagram_service.create(name="D")

        async with admission.lease("someone-else"):
            with pytest.raises(ReportBusyError):
                await reports.build(request_for(diagram))


class TestDepositingWithoutABucket:
    async def test_no_bucket_is_a_typed_refusal_before_anything_is_built(
        self, service: ArchitectureService, diagram_service: DiagramService
    ) -> None:
        reports = ReportService(diagram_service, service, None, FileService(None, metadata=None))
        diagram = await diagram_service.create(name="D")

        with pytest.raises(FileStorageUnavailableError):
            await reports.deposit(request_for(diagram))


class TestTheOutputIsBoundedBeforeItIsSerialised:
    async def test_text_past_the_ceiling_is_refused_before_the_engine_runs(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram = await diagram_service.create(name="Lourd")
        elements = [
            await service.create_element(
                element_type=ElementType.APPLICATION_COMPONENT,
                name=f"E{i}",
                description="x" * 1_000_000,  # the domain has no bound; the write doors do
            )
            for i in range(10)
        ]
        await diagram_service.replace_layout(
            diagram.id,
            revision=0,
            nodes=[DiagramNode(e.id, 10.0 * i, 10.0) for i, e in enumerate(elements)],
        )
        ran: list[int] = []

        def engine(model: ReportModel) -> bytes:
            ran.append(1)
            return b"%PDF"

        reports = ReportService(
            diagram_service, service, None, file_service, extra_renderers={"pdf": engine}
        )

        with pytest.raises(ReportTooLargeError, match="MiB of text"):
            await reports.build(request_for(diagram, "pdf"))

        assert ran == []

    async def test_a_client_that_hangs_up_does_not_free_the_place_of_a_running_thread(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
    ) -> None:
        diagram, _ = await a_diagram(service, diagram_service, "A")
        entered, release = threading.Event(), threading.Event()

        def blocking(model: ReportModel) -> bytes:
            entered.set()
            release.wait(5)
            return b"%PDF-fake"

        admission = report_admission()
        reports = ReportService(
            diagram_service,
            service,
            None,
            file_service,
            admission=admission,
            extra_renderers={"pdf": blocking},
        )
        task = asyncio.create_task(reports.build(request_for(diagram, "pdf")))
        await asyncio.to_thread(entered.wait, 5)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert admission.in_flight == 1  # the thread still runs: its place is still taken
        release.set()
        for _ in range(100):
            if admission.in_flight == 0:
                break
            await asyncio.sleep(0.01)
        assert admission.in_flight == 0

    async def test_a_thousand_and_one_boxes_never_reach_the_graph_read(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        reports: ReportService,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        diagram, _ = await a_diagram(
            service, diagram_service, *[f"B{i}" for i in range(MAX_REPORT_ELEMENTS * 2 + 1)]
        )
        read: list[int] = []
        original = service.view_of

        async def spying(ids: Sequence[UUID], **kwargs: object) -> GraphView:
            read.append(len(ids))
            return await original(ids, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(service, "view_of", spying)

        with pytest.raises(ReportTooLargeError):
            await reports.build(request_for(diagram))

        assert read == []


class TestTheNodeCeilingStopsTheEngine:
    async def test_two_hundred_thousand_paragraphs_are_refused_without_starting_the_engine(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        file_service: FileService,
        document_service_without_an_index: DocumentService,
    ) -> None:
        diagram, (element,) = await a_diagram(service, diagram_service, "A")
        await document_service_without_an_index.attach_text(
            element.id, filename="d.md", content="a\n\n" * 200_000
        )
        ran: list[int] = []

        def engine(model: ReportModel) -> bytes:
            ran.append(1)
            return b"%PDF"

        reports = ReportService(
            diagram_service,
            service,
            document_service_without_an_index,
            file_service,
            extra_renderers={"pdf": engine},
        )

        with pytest.raises(ReportTooLargeError, match="blocks"):
            await reports.build(request_for(diagram, "pdf"))

        assert ran == []
