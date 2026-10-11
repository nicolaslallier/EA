"""The exchange use cases, against in-memory doubles."""

from __future__ import annotations

import io
import logging
import zipfile
from uuid import uuid4

import pytest
from defusedxml import ElementTree as DefusedET

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
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
)
from ea.domain.ports import ElementFilter
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.exchanges import ExchangeService
from tests.conftest import FIXED_NOW, a_reader

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
