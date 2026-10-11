"""The exchange use cases, against in-memory doubles."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest
from defusedxml import ElementTree as DefusedET

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import NotAuthenticatedError, UnknownScopeError
from ea.domain.exchange import NAMESPACE, SOURCE_ID, ExportRequest, ExportScope
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
