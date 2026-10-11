"""An import's log lines never repeat the file's names; the audit trail keeps them."""

from __future__ import annotations

import logging
from uuid import uuid4

import pytest

from ea.domain.exchange import ExchangeFormat, ImportContent, ImportOptions
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from ea.services.exchanges import ExchangeService
from ea.services.packages import PackageService
from tests.conftest import FIXED_NOW, InMemoryExchange, InMemoryRepository
from tests.exchange_files import el, exchange_xml, rel

pytestmark = pytest.mark.asyncio
FORGED = "&#13;&#10;[ERROR] fake"


def a_file(marker: str) -> ImportContent:
    long = "L" * 10_000
    return ImportContent(
        ExchangeFormat.ARCHIMATE_XML,
        exchange_xml(
            el("a", "Node", f"{marker}-a{FORGED.replace('&#13;&#10;', '-')}")
            + el("b", "Node", f"{marker}-b")
            + el("big", "Node", long)
            + el("blank", "Node", " "),
            rel("r1", "Association", "a", "b")
            + rel("r2", "Association", "a", "blank")  # refused: its end was
            + rel("r3", "Association", "a", "nowhere"),
            extra=(
                f"<organizations><item><label>{marker}-folder{FORGED}</label>"
                '<item identifierRef="a"/></item></organizations>'
                "<views><diagrams>"
                f'<view identifier="v" xsi:type="Diagram"><name>{marker}-view{FORGED}</name>'
                '<node identifier="n" elementRef="a" xsi:type="Element" '
                'x="1" y="1" w="100" h="50"/>'
                '</view><view identifier="w" xsi:type="Diagram">'
                f"<name>{marker}-{long}</name></view>"
                "</diagrams></views>"
            ),
        ),
    )


@pytest.mark.parametrize("dry_run", [True, False])
async def test_no_log_record_repeats_a_name_of_the_file(
    exchange_store: InMemoryExchange,
    service: ArchitectureService,
    diagram_service: DiagramService,
    package_service: PackageService,
    repository: InMemoryRepository,
    caplog: pytest.LogCaptureFixture,
    dry_run: bool,
) -> None:
    marker = f"SECRET-MARKER-{uuid4()}"
    importer = ExchangeService(
        exchange_store,
        service,
        diagrams=diagram_service,
        packages=package_service,
        clock=lambda: FIXED_NOW,
    )

    with caplog.at_level(logging.DEBUG, logger="ea"):
        report = await importer.import_exchange(a_file(marker), ImportOptions(dry_run=dry_run))

    assert report.counts  # it ran, and refused some rows
    assert caplog.records
    for record in caplog.records:
        shown = record.getMessage() + " " + " ".join(str(v) for v in vars(record).values())
        assert marker not in shown, record.getMessage()
        assert "[ERROR] fake" not in shown, record.getMessage()
        assert "LLLLLLLLLL" not in shown
    if not dry_run:  # the audit keeps the names: that is the record of what was done
        names = " ".join(str(e.after) for e in repository.audit_log.events)
        assert marker in names


async def test_outside_an_import_a_name_is_logged_as_before(
    package_service: PackageService, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="ea"):
        await package_service.create(name="Visible name")

    assert "Visible name" in caplog.text
