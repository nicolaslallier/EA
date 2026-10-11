"""The exchange use cases, against in-memory doubles."""

from __future__ import annotations

import dataclasses
import io
import logging
import zipfile
from uuid import uuid4

import pytest
from defusedxml import ElementTree as DefusedET

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.diagrams import DiagramNode
from ea.domain.errors import (
    ExchangeFileTooLargeError,
    InvalidExchangeFileError,
    NotAuthenticatedError,
    NotAuthorisedError,
    UnknownScopeError,
)
from ea.domain.exchange import (
    NAMESPACE,
    SOURCE_ID,
    Category,
    ExchangeFormat,
    ExportRequest,
    ExportScope,
    ImportContent,
    ImportOptions,
    ScopeKind,
)
from ea.domain.ports import ElementFilter
from ea.domain.query import ElementQuery
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.exchanges import ExchangeService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService
from tests.conftest import FIXED_NOW, InMemoryRepository, a_reader, lay_out

pytestmark = pytest.mark.asyncio
NS = {"x": NAMESPACE}


async def exported(service: ExchangeService, request: ExportRequest | None = None) -> str:
    export = await service.export(request or ExportRequest())
    return b"".join([chunk async for chunk in export.chunks]).decode("utf-8")


async def two_linked_elements(graph: ArchitectureService) -> None:
    a = await graph.create_element(
        element_type=E.APPLICATION_COMPONENT,
        name="Billing",
        description="Invoices",
        properties={SOURCE_ID: "app-1", "owner": "finance"},
    )
    b = await graph.create_element(element_type=E.APPLICATION_COMPONENT, name="CRM")
    await graph.connect(relationship_type=R.FLOW, source_id=a.id, target_id=b.id, name="orders")


class TestExport:
    async def test_it_writes_every_element_and_link_with_stable_identifiers(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await two_linked_elements(service)

        root = DefusedET.fromstring(await exported(exchange_service))

        elements = root.findall("x:elements/x:element", NS)
        assert elements[0].get("identifier") == "app-1"
        assert elements[1].get("identifier").startswith("id-")  # type: ignore[union-attr]
        link = root.find("x:relationships/x:relationship", NS)
        assert link is not None and link.get("source") == "app-1"

    async def test_two_exports_of_the_same_model_are_identical(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await two_linked_elements(service)

        assert await exported(exchange_service) == await exported(exchange_service)

    async def test_the_file_name_is_made_by_the_server(
        self, exchange_service: ExchangeService
    ) -> None:
        export = await exchange_service.export(ExportRequest())

        assert export.filename == "ea-model-2026-09-07.xml"
        assert export.media_type == "application/xml"
        await export.chunks.aclose()  # type: ignore[attr-defined]

    async def test_a_reader_may_export_but_nobody_may(
        self, exchange_service: ExchangeService, nobody_calling: None
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await exchange_service.export(ExportRequest())

    async def test_a_reader_exports(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await two_linked_elements(service)
        with acting_as(a_reader()):
            assert "<model" in await exported(exchange_service)

    async def test_it_logs_one_line_with_counts_and_never_the_content(
        self,
        service: ArchitectureService,
        exchange_service: ExchangeService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await two_linked_elements(service)
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await exported(exchange_service)

        (line,) = [r for r in caplog.records if getattr(r, "action", None) == "exported"]
        assert (line.elements, line.relationships) == (2, 1)  # type: ignore[attr-defined]
        assert "Billing" not in caplog.text

    async def test_an_export_that_outlives_its_budget_stops(
        self, service: ArchitectureService, exchange_store: object
    ) -> None:
        await two_linked_elements(service)
        ticks = iter([0.0, 10_000.0, 20_000.0, 30_000.0])
        slow = ExchangeService(
            exchange_store,  # type: ignore[arg-type]
            service,
            clock=lambda: FIXED_NOW,
            monotonic=lambda: next(ticks),
        )

        with pytest.raises(TimeoutError):
            await exported(slow)

    async def test_two_elements_with_one_source_id_get_distinct_identifiers_and_a_warning(
        self,
        service: ArchitectureService,
        exchange_service: ExchangeService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        a = await service.create_element(
            element_type=E.NODE, name="A", properties={SOURCE_ID: "dup"}
        )
        b = await service.create_element(
            element_type=E.NODE, name="B", properties={SOURCE_ID: "dup"}
        )
        await service.connect(relationship_type=R.FLOW, source_id=a.id, target_id=b.id)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            root = DefusedET.fromstring(await exported(exchange_service))

        ids = [e.get("identifier") for e in root.findall("x:elements/x:element", NS)]
        assert ids == ["dup", f"id-{b.id}"]
        link = root.find("x:relationships/x:relationship", NS)
        assert link is not None
        assert (link.get("source"), link.get("target")) == ("dup", f"id-{b.id}")
        (warning,) = [r for r in caplog.records if getattr(r, "action", None) == "export_adjusted"]
        assert warning.levelno == logging.WARNING
        assert warning.identifiers_replaced == 1  # type: ignore[attr-defined]

    async def test_a_name_with_a_forbidden_character_is_cleaned_with_a_warning(
        self,
        service: ArchitectureService,
        exchange_service: ExchangeService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await service.create_element(element_type=E.NODE, name="srv\x01-1")
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            text = await exported(exchange_service)

        assert "srv-1" in text
        (warning,) = [r for r in caplog.records if getattr(r, "action", None) == "export_adjusted"]
        assert warning.characters_dropped == 1  # type: ignore[attr-defined]
        assert "srv" not in caplog.text

    async def test_a_clean_export_raises_no_warning(
        self,
        service: ArchitectureService,
        exchange_service: ExchangeService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await two_linked_elements(service)
        caplog.clear()
        with caplog.at_level(logging.WARNING):
            await exported(exchange_service)

        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    async def test_an_unsupported_scope_is_refused(self, exchange_service: ExchangeService) -> None:
        with pytest.raises(UnknownScopeError):
            await exchange_service.export(ExportRequest(scope=ExportScope(package_id=uuid4())))


CSV = ExportRequest(format=ExchangeFormat.CSV)


async def zip_of(service: ExchangeService, request: ExportRequest = CSV) -> bytes:
    export = await service.export(request)
    return b"".join([chunk async for chunk in export.chunks])


class TestCsvExport:
    async def test_it_is_a_zip_of_the_three_files_named_by_the_server(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await two_linked_elements(service)
        export = await exchange_service.export(CSV)

        assert export.media_type == "application/zip"
        assert export.filename == "ea-model-2026-09-07.zip"
        archive = zipfile.ZipFile(io.BytesIO(b"".join([c async for c in export.chunks])))
        assert archive.namelist() == ["elements.csv", "relations.csv", "properties.csv"]
        rows = archive.read("elements.csv").decode("utf-8")
        assert '"app-1","ApplicationComponent","Billing"' in rows

    async def test_an_export_without_the_guard_is_labelled_raw_in_its_name_and_its_header(
        self, exchange_service: ExchangeService
    ) -> None:
        raw = await exchange_service.export(
            ExportRequest(format=ExchangeFormat.CSV, safe_cells=False)
        )
        await raw.chunks.aclose()  # type: ignore[attr-defined]  # (one place per caller)
        safe = await exchange_service.export(CSV)

        assert raw.filename == "ea-model-2026-09-07-raw.zip" and raw.cells == "raw"
        assert safe.filename == "ea-model-2026-09-07.zip" and safe.cells == "safe"
        await raw.chunks.aclose()  # type: ignore[attr-defined]
        await safe.chunks.aclose()  # type: ignore[attr-defined]

    async def test_two_exports_are_byte_identical_including_the_archive(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await two_linked_elements(service)

        assert await zip_of(exchange_service) == await zip_of(exchange_service)

    async def test_a_formula_in_a_name_is_neutralised_unless_asked_not_to(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="=cmd")

        safe = zipfile.ZipFile(io.BytesIO(await zip_of(exchange_service)))
        raw = zipfile.ZipFile(
            io.BytesIO(
                await zip_of(
                    exchange_service, ExportRequest(format=ExchangeFormat.CSV, safe_cells=False)
                )
            )
        )

        assert "'=cmd" in safe.read("elements.csv").decode()
        assert '"=cmd"' in raw.read("elements.csv").decode()

    async def test_an_identifier_that_cannot_be_written_is_replaced_as_in_the_xml(
        self,
        service: ArchitectureService,
        exchange_service: ExchangeService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        a = await service.create_element(
            element_type=E.NODE, name="A", properties={SOURCE_ID: "dup"}
        )
        b = await service.create_element(
            element_type=E.NODE, name="B", properties={SOURCE_ID: "dup"}
        )
        await service.connect(relationship_type=R.FLOW, source_id=a.id, target_id=b.id)
        caplog.clear()
        with caplog.at_level(logging.INFO):
            archive = zipfile.ZipFile(io.BytesIO(await zip_of(exchange_service)))

        assert '"dup","Node","A"' in archive.read("elements.csv").decode()
        assert f'"id-{b.id}","Node","B"' in archive.read("elements.csv").decode()
        assert f'"dup","id-{b.id}"' in archive.read("relations.csv").decode()
        (warning,) = [r for r in caplog.records if getattr(r, "action", None) == "export_adjusted"]
        assert warning.identifiers_replaced == 1  # type: ignore[attr-defined]

    async def test_it_logs_counts_never_the_content(
        self,
        service: ArchitectureService,
        exchange_service: ExchangeService,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        await two_linked_elements(service)
        caplog.clear()
        with caplog.at_level(logging.INFO):
            await zip_of(exchange_service)

        (line,) = [r for r in caplog.records if getattr(r, "action", None) == "exported"]
        assert (line.elements, line.relationships, line.format) == (2, 1, "csv")  # type: ignore[attr-defined]
        assert "Billing" not in caplog.text

    async def test_an_export_that_outlives_its_budget_stops(
        self, service: ArchitectureService, exchange_store: object
    ) -> None:
        await two_linked_elements(service)
        ticks = iter([0.0, 10_000.0, 20_000.0, 30_000.0])
        slow = ExchangeService(
            exchange_store,  # type: ignore[arg-type]
            service,
            clock=lambda: FIXED_NOW,
            monotonic=lambda: next(ticks),
        )

        with pytest.raises(TimeoutError):
            await zip_of(slow)

    async def test_nobody_may_export_a_csv(
        self,
        service: ArchitectureService,
        exchange_service: ExchangeService,
        nobody_calling: None,
    ) -> None:
        with pytest.raises(NotAuthenticatedError):
            await exchange_service.export(CSV)

    async def test_an_unsupported_scope_is_refused_for_csv_too(
        self, exchange_service: ExchangeService
    ) -> None:
        with pytest.raises(UnknownScopeError):
            await exchange_service.export(
                ExportRequest(format=ExchangeFormat.CSV, scope=ExportScope(package_id=uuid4()))
            )


class TestCsvImport:
    async def test_the_three_files_are_imported_through_the_service(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await two_linked_elements(service)
        archive = zipfile.ZipFile(io.BytesIO(await zip_of(exchange_service)))
        # a different, empty catalogue to import into
        for element in await service.list_elements(ElementFilter(limit=100)):
            await service.delete_element(element.id)

        report = await exchange_service.import_exchange(
            ImportContent(
                ExchangeFormat.CSV,
                archive.read("elements.csv"),
                archive.read("relations.csv"),
                archive.read("properties.csv"),
            ),
            ImportOptions(dry_run=False),
        )

        assert report.format is ExchangeFormat.CSV
        assert report.counts[Category.ELEMENTS].created == 2
        assert report.counts[Category.RELATIONSHIPS].created == 1
        found = await service.list_elements(ElementFilter(limit=100))
        billing = next(e for e in found if e.name == "Billing")
        assert (billing.description, dict(billing.properties)["owner"]) == ("Invoices", "finance")

    async def test_a_dry_run_is_the_default_and_writes_nothing(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        csv_main = b'"ID","Type","Name"\r\n"a","Node","srv"\r\n'

        report = await exchange_service.import_exchange(
            ImportContent(ExchangeFormat.CSV, csv_main), ImportOptions()
        )

        assert report.dry_run and report.counts[Category.ELEMENTS].created == 1
        assert await service.list_elements(ElementFilter(limit=100)) == ()

    async def test_a_reader_may_not_import(self, exchange_service: ExchangeService) -> None:
        with acting_as(a_reader()), pytest.raises(NotAuthorisedError):
            await exchange_service.import_exchange(
                ImportContent(ExchangeFormat.CSV, b'"ID","Type","Name"\r\n'), ImportOptions()
            )

    async def test_the_report_names_the_file_and_the_line_and_sorts_issues_by_file(
        self, exchange_service: ExchangeService
    ) -> None:
        main = b'"ID","Type","Name"\r\n"a","Node","A"\r\n"b","Node","  "\r\n'
        relations = (
            b'"ID","Type","Source","Target"\r\n"r1","Flow","a","ghost"\r\n"r2","Flow","a","b"\r\n'
        )

        report = await exchange_service.import_exchange(
            ImportContent(ExchangeFormat.CSV, main, relations), ImportOptions()
        )

        by_code = {i.code: i for i in report.issues}
        assert by_code["blank_name"].line == 3 and by_code["blank_name"].source_id == "b"
        assert by_code["unknown_end"].line == 2 and by_code["unknown_end"].source_id == "r1"
        assert by_code["end_rejected"].line == 3
        assert report.counts[Category.ELEMENTS].rejected == 1
        assert report.counts[Category.RELATIONSHIPS].rejected == 2

    async def test_the_issues_found_while_reading_a_relation_row_count_as_relationships(
        self, exchange_service: ExchangeService
    ) -> None:
        main = b'"ID","Type","Name"\r\n"a","Node","A"\r\n'
        relations = b'"ID","Type","Source","Target","Documentation"\r\n"r","Flow","a","a","d"\r\n'
        report = await exchange_service.import_exchange(
            ImportContent(ExchangeFormat.CSV, main, relations), ImportOptions()
        )

        assert [i.code for i in report.issues] == ["documentation_dropped"]

    async def test_the_ceiling_is_on_the_sum_of_the_three_files(
        self, exchange_store: object, service: ArchitectureService
    ) -> None:
        importer = ExchangeService(exchange_store, service, max_bytes=100)  # type: ignore[arg-type]
        part = b'"ID","Type","Name"\r\n' + b" " * 40

        with pytest.raises(ExchangeFileTooLargeError):
            await importer.import_exchange(
                ImportContent(ExchangeFormat.CSV, part, part, part), ImportOptions()
            )

    async def test_the_report_digest_covers_every_file_and_tells_them_apart(self) -> None:
        a, b = b'"ID","Type","Name"\r\n', b'"ID","Key","Value"\r\n'

        one = ImportContent(ExchangeFormat.CSV, a, None, b)
        other = ImportContent(ExchangeFormat.CSV, a, b, None)

        assert one.sha256 != other.sha256

    async def test_a_file_that_cannot_be_read_is_refused_without_echo(
        self, exchange_service: ExchangeService
    ) -> None:
        with pytest.raises(InvalidExchangeFileError) as raised:
            await exchange_service.import_exchange(
                ImportContent(ExchangeFormat.CSV, b'"ID","Type","Name"\r\n"a","Node","LEAK\x00"'),
                ImportOptions(),
            )

        assert "LEAK" not in str(raised.value)


class TestViewsInTheExport:
    async def test_a_saved_diagram_is_written_with_its_boxes(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        exchange_service: ExchangeService,
    ) -> None:
        a = await service.create_element(
            element_type=E.NODE, name="srv", properties={SOURCE_ID: "n-1"}
        )
        diagram = await diagram_service.create(name="Overview", description="Main")
        await lay_out(diagram_service, diagram.id, [DiagramNode(a.id, 10, 20, 100, 50)])

        root = DefusedET.fromstring(await exported(exchange_service))

        view = root.find("x:views/x:diagrams/x:view", NS)
        assert view is not None and view.get("identifier") == "view-Overview"
        box = view.find("x:node", NS)
        assert box is not None and box.get("elementRef") == "n-1"
        assert (box.get("x"), box.get("w")) == ("10", "100")

    async def test_with_views_false_leaves_them_out(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        exchange_service: ExchangeService,
    ) -> None:
        a = await service.create_element(element_type=E.NODE, name="srv")
        diagram = await diagram_service.create(name="Overview")
        await lay_out(diagram_service, diagram.id, [DiagramNode(a.id, 0, 0)])

        root = DefusedET.fromstring(
            await exported(exchange_service, ExportRequest(with_views=False))
        )

        assert root.find("x:views", NS) is None

    async def test_two_names_that_normalise_alike_keep_distinct_identifiers(
        self, diagram_service: DiagramService, exchange_service: ExchangeService
    ) -> None:
        for name in ("a b", "a_b"):
            await diagram_service.create(name=name)

        root = DefusedET.fromstring(await exported(exchange_service))

        identifiers = [v.get("identifier") for v in root.findall("x:views/x:diagrams/x:view", NS)]
        assert len(set(identifiers)) == 2

    async def test_a_source_id_that_looks_like_a_view_is_not_taken_for_one(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        exchange_service: ExchangeService,
    ) -> None:
        a = await service.create_element(
            element_type=E.NODE, name="srv", properties={SOURCE_ID: "view-Overview"}
        )
        diagram = await diagram_service.create(name="Overview")
        await lay_out(diagram_service, diagram.id, [DiagramNode(a.id, 0, 0)])

        root = DefusedET.fromstring(await exported(exchange_service))

        (element,) = root.findall("x:elements/x:element", NS)
        view = root.find("x:views/x:diagrams/x:view", NS)
        assert view is not None and element.get("identifier") != view.get("identifier")

    async def test_csv_has_no_views(
        self,
        service: ArchitectureService,
        diagram_service: DiagramService,
        exchange_service: ExchangeService,
    ) -> None:
        a = await service.create_element(element_type=E.NODE, name="srv")
        diagram = await diagram_service.create(name="Overview")
        await lay_out(diagram_service, diagram.id, [DiagramNode(a.id, 0, 0)])

        archive = zipfile.ZipFile(io.BytesIO(await zip_of(exchange_service)))

        assert archive.namelist() == ["elements.csv", "relations.csv", "properties.csv"]


class TestOrganizationsInTheExport:
    async def test_the_package_tree_is_written_as_organizations(
        self,
        service: ArchitectureService,
        package_service: PackageService,
        exchange_service: ExchangeService,
        repository: InMemoryRepository,
    ) -> None:
        infra = (await package_service.create(name="Infra")).package.id
        net = (await package_service.create(name="Net", parent_id=infra)).package.id
        await package_service.create(name="Empty")
        a = await service.create_element(
            element_type=E.NODE, name="srv", properties={SOURCE_ID: "n-1"}
        )
        b = await service.create_element(
            element_type=E.NODE, name="sw", properties={SOURCE_ID: "n-2"}
        )
        await service.create_element(
            element_type=E.NODE, name="loose", properties={SOURCE_ID: "n-3"}
        )
        repository.elements[a.id] = dataclasses.replace(a, package_id=infra)
        repository.elements[b.id] = dataclasses.replace(b, package_id=net)

        root = DefusedET.fromstring(await exported(exchange_service))

        labels = [
            i.find("x:label", NS).text for i in root.findall("x:organizations/x:item[x:label]", NS)
        ]  # type: ignore[union-attr]
        assert labels == ["Empty", "Infra"]
        folder = root.find("x:organizations/x:item[x:label='Infra']", NS)
        assert folder is not None
        assert folder.find("x:item[@identifierRef='n-1']", NS) is not None
        assert folder.find("x:item/x:item[@identifierRef='n-2']", NS) is not None
        assert root.find("x:organizations/x:item[@identifierRef='n-3']", NS) is not None

    async def test_a_catalogue_without_packages_has_no_organizations(
        self, service: ArchitectureService, exchange_service: ExchangeService
    ) -> None:
        await service.create_element(element_type=E.NODE, name="srv")

        root = DefusedET.fromstring(await exported(exchange_service))

        assert root.find("x:organizations", NS) is None


class TestScopes:
    async def arranged(  # type: ignore[no-untyped-def]
        self, service, package_service, repository
    ):
        top = (await package_service.create(name="Top")).package.id
        child = (await package_service.create(name="Child", parent_id=top)).package.id
        other = (await package_service.create(name="Other")).package.id
        made = {}
        for name, package in (("in", top), ("deep", child), ("out", other), ("loose", None)):
            element = await service.create_element(
                element_type=E.NODE, name=name, properties={SOURCE_ID: f"n-{name}"}
            )
            repository.elements[element.id] = dataclasses.replace(element, package_id=package)
            made[name] = repository.elements[element.id]
        return top, child, made

    def names(self, root) -> set[str]:  # type: ignore[no-untyped-def]
        return {
            e.find("x:name", NS).text  # type: ignore[union-attr]
            for e in root.findall("x:elements/x:element", NS)
        }

    async def test_a_package_scope_exports_its_subtree_and_the_links_inside_it(
        self, service, package_service, exchange_service, repository
    ) -> None:  # type: ignore[no-untyped-def]
        top, _, made = await self.arranged(service, package_service, repository)
        await service.connect(
            relationship_type=R.ASSOCIATION, source_id=made["in"].id, target_id=made["deep"].id
        )
        await service.connect(
            relationship_type=R.ASSOCIATION, source_id=made["in"].id, target_id=made["out"].id
        )

        request = ExportRequest(scope=ExportScope(ScopeKind.PACKAGE, package_id=top))
        root = DefusedET.fromstring(await exported(exchange_service, request))

        assert self.names(root) == {"in", "deep"}
        links = root.findall("x:relationships/x:relationship", NS)
        assert len(links) == 1  # the one with an end outside the scope is not written
        assert {link.get("source") for link in links} == {"n-in"}
        assert root.find("x:organizations/x:item/x:label", NS).text == "Top"  # type: ignore[union-attr]
        assert root.find("x:organizations/x:item/x:item/x:label", NS).text == "Child"  # type: ignore[union-attr]
        assert len(root.findall("x:organizations/x:item", NS)) == 1  # nothing of "Other"

    async def test_without_the_subpackages_only_that_package_is_exported(
        self, service, package_service, exchange_service, repository
    ) -> None:  # type: ignore[no-untyped-def]
        top, _, _ = await self.arranged(service, package_service, repository)

        request = ExportRequest(
            scope=ExportScope(ScopeKind.PACKAGE, package_id=top, include_subpackages=False)
        )
        root = DefusedET.fromstring(await exported(exchange_service, request))

        assert self.names(root) == {"in"}

    async def test_a_view_with_a_box_outside_the_scope_is_left_out_and_counted(
        self, service, package_service, diagram_service, exchange_service, repository
    ) -> None:  # type: ignore[no-untyped-def]
        top, _, made = await self.arranged(service, package_service, repository)
        whole = await diagram_service.create(name="whole")
        await lay_out(diagram_service, whole.id, [DiagramNode(made["in"].id, 0, 0)])
        straddling = await diagram_service.create(name="straddling")
        await lay_out(
            diagram_service,
            straddling.id,
            [DiagramNode(made["in"].id, 0, 0), DiagramNode(made["out"].id, 5, 5)],
        )
        request = ExportRequest(scope=ExportScope(ScopeKind.PACKAGE, package_id=top))

        export = await exchange_service.export(request)
        root = DefusedET.fromstring(b"".join([c async for c in export.chunks]))

        views = [v.get("identifier") for v in root.findall("x:views/x:diagrams/x:view", NS)]
        assert views == ["view-whole"]
        assert export.skipped_views == 1

    async def test_the_whole_model_skips_no_view(self, exchange_service: ExchangeService) -> None:
        export = await exchange_service.export(ExportRequest())
        await export.chunks.aclose()  # type: ignore[attr-defined]

        assert export.skipped_views == 0

    async def test_a_query_scope_exports_what_the_saved_query_finds(
        self, service, package_service, saved_query_service, exchange_service, repository
    ) -> None:  # type: ignore[no-untyped-def]
        _, _, made = await self.arranged(service, package_service, repository)
        actor = await service.create_element(element_type=E.BUSINESS_ACTOR, name="Alice")
        await service.connect(
            relationship_type=R.ASSOCIATION, source_id=made["in"].id, target_id=made["deep"].id
        )
        await service.connect(
            relationship_type=R.ASSOCIATION, source_id=made["in"].id, target_id=actor.id
        )
        saved = await saved_query_service.create(
            name="nodes", query=ElementQuery(element_types=(E.NODE,))
        )

        request = ExportRequest(scope=ExportScope(ScopeKind.QUERY, query_id=saved.id))
        root = DefusedET.fromstring(await exported(exchange_service, request))

        assert self.names(root) == {"in", "deep", "out", "loose"}  # never Alice
        assert len(root.findall("x:relationships/x:relationship", NS)) == 1
        labels = {
            i.find("x:label", NS).text for i in root.findall("x:organizations/x:item[x:label]", NS)
        }  # type: ignore[union-attr]
        assert labels == {"Top", "Other"}  # the whole skeleton of the tree

    async def test_csv_follows_the_scope_too(
        self, service, package_service, exchange_service, repository
    ) -> None:  # type: ignore[no-untyped-def]
        top, _, made = await self.arranged(service, package_service, repository)
        await service.connect(
            relationship_type=R.ASSOCIATION, source_id=made["in"].id, target_id=made["out"].id
        )

        archive = zipfile.ZipFile(
            io.BytesIO(
                await zip_of(
                    exchange_service,
                    ExportRequest(
                        format=ExchangeFormat.CSV,
                        scope=ExportScope(ScopeKind.PACKAGE, package_id=top),
                    ),
                )
            )
        )

        assert b"out" not in archive.read("elements.csv")
        assert archive.read("relations.csv").count(b"\r\n") == 1  # the header alone

    async def test_an_unknown_package_is_an_unknown_scope(self, exchange_service) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(UnknownScopeError):
            await exchange_service.export(
                ExportRequest(scope=ExportScope(ScopeKind.PACKAGE, package_id=uuid4()))
            )

    async def test_an_unknown_saved_query_is_an_unknown_scope(self, exchange_service) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(UnknownScopeError):
            await exchange_service.export(
                ExportRequest(scope=ExportScope(ScopeKind.QUERY, query_id=uuid4()))
            )

    async def test_a_saved_query_naming_a_vanished_package_is_an_unknown_scope(
        self, saved_query_service, exchange_service, saved_queries
    ) -> None:  # type: ignore[no-untyped-def]
        saved = await saved_query_service.create(name="stale", query=ElementQuery())
        saved_queries.queries[saved.id] = dataclasses.replace(
            saved, query=ElementQuery(package_id=uuid4())
        )

        with pytest.raises(UnknownScopeError):
            await exchange_service.export(
                ExportRequest(scope=ExportScope(ScopeKind.QUERY, query_id=saved.id))
            )

    async def test_a_saved_query_that_cannot_be_read_is_an_unknown_scope_not_a_500(
        self, saved_query_service, exchange_service, monkeypatch
    ) -> None:  # type: ignore[no-untyped-def]
        async def unreadable(query_id):  # type: ignore[no-untyped-def]
            raise ValueError("stored query is corrupt: SECRET")

        monkeypatch.setattr(saved_query_service, "get", unreadable)

        with pytest.raises(UnknownScopeError) as raised:
            await exchange_service.export(
                ExportRequest(scope=ExportScope(ScopeKind.QUERY, query_id=uuid4()))
            )

        assert "SECRET" not in str(raised.value)

    async def test_a_stored_query_of_a_version_nobody_reads_is_an_unknown_scope(
        self, saved_query_service, exchange_service, saved_queries
    ) -> None:  # type: ignore[no-untyped-def]
        saved = await saved_query_service.create(name="old", query=ElementQuery())
        saved_queries.corrupt.add(saved.id)

        with pytest.raises(UnknownScopeError) as raised:
            await exchange_service.export(
                ExportRequest(scope=ExportScope(ScopeKind.QUERY, query_id=saved.id))
            )

        assert "version" not in str(raised.value)

    @pytest.mark.parametrize(
        "scope",
        [
            ExportScope(ScopeKind.PACKAGE),
            ExportScope(ScopeKind.QUERY),
            ExportScope(ScopeKind.MODEL, package_id=uuid4()),
            ExportScope(ScopeKind.MODEL, query_id=uuid4()),
            ExportScope(ScopeKind.PACKAGE, package_id=uuid4(), query_id=uuid4()),
            ExportScope(ScopeKind.QUERY, package_id=uuid4(), query_id=uuid4()),
        ],
    )
    async def test_an_ill_formed_scope_is_refused(self, exchange_service, scope) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(UnknownScopeError):
            await exchange_service.export(ExportRequest(scope=scope))

    async def test_a_scope_is_a_read_for_a_reader_and_nothing_for_nobody(
        self, service, package_service, exchange_service, repository, nobody_calling
    ) -> None:  # type: ignore[no-untyped-def]
        with pytest.raises(NotAuthenticatedError):
            await exchange_service.export(
                ExportRequest(scope=ExportScope(ScopeKind.PACKAGE, package_id=uuid4()))
            )


class TestTheSnapshotOfAnExport:
    def watching(self, store: object, monkeypatch: pytest.MonkeyPatch) -> list[str]:
        from contextlib import asynccontextmanager

        events: list[str] = []
        original = store.snapshot  # type: ignore[attr-defined]

        @asynccontextmanager
        async def snapshot(scope):  # type: ignore[no-untyped-def]
            events.append("open")
            async with original(scope) as opened:
                yield opened
            events.append("closed")

        monkeypatch.setattr(store, "snapshot", snapshot)
        return events

    async def test_it_is_one_reading_over_before_the_first_byte_and_never_held_by_a_reader(
        self,
        exchange_service: ExchangeService,
        exchange_store: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        events = self.watching(exchange_store, monkeypatch)

        export = await exchange_service.export(ExportRequest())
        # The count of the header and the file share one reading, and it is already over: the
        # connection went back before a byte was sent (test_exchange_download).
        assert events == ["open", "closed"]
        await export.chunks.aclose()  # type: ignore[attr-defined]

        assert events == ["open", "closed"]

    async def test_it_is_given_back_at_the_end_of_the_body_and_when_the_client_leaves(
        self,
        exchange_service: ExchangeService,
        service: ArchitectureService,
        exchange_store: object,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await two_linked_elements(service)
        events = self.watching(exchange_store, monkeypatch)

        for request in (ExportRequest(), CSV):
            events.clear()
            export = await exchange_service.export(request)
            _ = [chunk async for chunk in export.chunks]
            assert events == ["open", "closed"]

        events.clear()
        export = await exchange_service.export(ExportRequest())
        iterator = export.chunks.__aiter__()
        await iterator.__anext__()  # the client reads a piece and leaves
        await export.chunks.aclose()  # type: ignore[attr-defined]
        assert events == ["open", "closed"]

    async def test_a_query_scope_is_run_once_before_the_download_starts(
        self,
        exchange_service: ExchangeService,
        saved_query_service: SavedQueryService,
        exchange_store: object,
    ) -> None:
        saved = await saved_query_service.create(name="q", query=ElementQuery())
        seen: list[object] = []
        original = exchange_store.snapshot  # type: ignore[attr-defined]

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def snapshot(scope):  # type: ignore[no-untyped-def]
            async with original(scope) as opened:
                yield opened
                seen.append(opened.scope_checked)

        exchange_store.snapshot = snapshot  # type: ignore[attr-defined]

        export = await exchange_service.export(
            ExportRequest(scope=ExportScope(ScopeKind.QUERY, query_id=saved.id))
        )
        await export.chunks.aclose()  # type: ignore[attr-defined]

        assert seen == [True]
