"""The MCP tool takes its place among the imports like the HTTP door does (docs/adr/0050).

`/mcp` does not cross `api/body_limit.py`, where HTTP takes its place; the service takes it for
every caller that arrives without one. Same admission, same key (the `sub` of the verified
token), same lease given back at the end — the analysis thread included, exception and
cancellation included.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ea.domain.errors import InvalidExchangeFileError
from ea.mcp import build_mcp_server
from ea.services.analysis import AnalysisService
from ea.services.architecture import ArchitectureService
from ea.services.audit import AuditService
from ea.services.baselines import BaselineService
from ea.services.caller import acting_as
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.files import FileService
from ea.services.ipam import IpamService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService
from ea.services.timeline import TimelineService
from tests.conftest import InMemoryAuditLog, InMemoryExchange, InMemoryRepository
from tests.exchange_files import DATA
from tests.unit.test_exchange_admission import Parser, Wire, run, settle
from tests.unit.test_exchange_admission_callers import ALICE, BOB, CAROL, build, hold

pytestmark = pytest.mark.asyncio
XML = (DATA / "minimal-model.xml").read_text(encoding="utf-8")


@pytest.fixture
def parser() -> Parser:
    return Parser()


def mcp_over(
    app: Any,
    service: ArchitectureService,
    document_service: DocumentService,
    ipam: IpamService,
    file_service: FileService,
    package_service: PackageService,
    saved_query_service: SavedQueryService,
    audit_log: InMemoryAuditLog,
    baseline_service: BaselineService,
    diagram_service: DiagramService,
) -> MCPServer[Any]:
    return build_mcp_server(
        lambda: service,
        lambda: document_service,
        lambda: ipam,
        lambda: file_service,
        lambda: package_service,
        lambda: saved_query_service,
        lambda: AuditService(audit_log),
        lambda: baseline_service,
        lambda: app.state.exchange_service,
        lambda: AnalysisService(service, InMemoryRepository()),
        lambda: TimelineService(service, InMemoryRepository()),
        lambda: diagram_service,
    )


@pytest.fixture
def parts(
    document_service: DocumentService,
    ipam: IpamService,
    file_service: FileService,
    package_service: PackageService,
    saved_query_service: SavedQueryService,
    audit_log: InMemoryAuditLog,
    baseline_service: BaselineService,
    diagram_service: DiagramService,
) -> tuple[Any, ...]:
    return (
        document_service,
        ipam,
        file_service,
        package_service,
        saved_query_service,
        audit_log,
        baseline_service,
        diagram_service,
    )


async def call(server: MCPServer[Any], **arguments: Any) -> Any:
    result = await server.call_tool("import_model", arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def test_two_http_imports_in_flight_and_one_over_mcp_is_refused_then_served(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    parser: Parser,
    parts: tuple[Any, ...],
) -> None:
    app = build(service, exchange_store, parser)
    server = mcp_over(app, service, *parts)
    _, first = await hold(app, parser, "alice")
    _, second = await hold(app, parser, "bob")
    admission = app.state.import_admission

    with acting_as(CAROL), pytest.raises(ToolError, match="other imports"):
        await call(server, content=XML)

    assert admission.in_flight == 2  # the refusal took nothing
    parser.release.set()
    await asyncio.gather(first, second)
    assert admission.in_flight == 0
    parser.block = False
    with acting_as(CAROL):
        report = await call(server, content=XML)
    assert report["dry_run"] is True and admission.in_flight == 0 and admission.callers == {}


async def test_a_caller_with_an_http_import_in_flight_cannot_start_a_second_over_mcp(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    parser: Parser,
    parts: tuple[Any, ...],
) -> None:
    app = build(service, exchange_store, parser)
    server = mcp_over(app, service, *parts)
    _, held = await hold(app, parser, "alice")

    with acting_as(ALICE), pytest.raises(ToolError, match="other imports"):
        await call(server, content=XML)
    parser.block = False
    with acting_as(BOB):
        assert (await call(server, content=XML))["dry_run"] is True  # another caller gets in

    parser.release.set()
    await held
    assert app.state.import_admission.callers == {}


async def test_the_place_comes_back_after_a_refused_file_a_failure_and_a_cancellation(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    parser: Parser,
    parts: tuple[Any, ...],
) -> None:
    app = build(service, exchange_store, parser)
    server = mcp_over(app, service, *parts)
    admission = app.state.import_admission
    parser.block = False

    with acting_as(ALICE):
        parser.fail = InvalidExchangeFileError("not an exchange file")
        with pytest.raises(ToolError):
            await call(server, content="<model><broken")  # a refused file
        parser.fail = RuntimeError("boom")
        with pytest.raises(Exception, match=r"boom|Error executing"):
            await call(server, content=XML)  # an unexpected failure
        parser.fail = None
        assert admission.in_flight == 0

        parser.block = True
        task = asyncio.create_task(call(server, content=XML))
        await asyncio.to_thread(parser.entered.wait, 5)
        assert admission.callers == {"alice": 1}
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        # the thread cannot be stopped: the place stays until it has really ended
        assert admission.in_flight == 1
        parser.release.set()
        await settle()
        await asyncio.sleep(0.05)

    assert admission.in_flight == 0 and admission.callers == {}


async def test_a_reader_takes_no_place_and_is_refused(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    parser: Parser,
    parts: tuple[Any, ...],
) -> None:
    from tests.conftest import a_reader

    app = build(service, exchange_store, parser)
    server = mcp_over(app, service, *parts)

    with acting_as(a_reader()), pytest.raises(ToolError, match="role"):
        await call(server, content=XML)

    assert app.state.import_admission.in_flight == 0


async def test_the_file_is_not_in_the_debug_log_of_the_call_nor_in_the_log_of_a_failure(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    parser: Parser,
    parts: tuple[Any, ...],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.exc import IntegrityError

    marker = "SECRET-MARKER-MCP-LOG"
    app = build(service, exchange_store, parser)
    server = mcp_over(app, service, *parts)
    parser.block = False
    xml = XML.replace("Customer", marker)
    del xml  # (the marker must lead the argument: it is what a 120-character summary would show)
    leading = f"<!-- {marker} -->" + XML

    async def exploding(*_: object, **__: object) -> None:
        raise IntegrityError("INSERT ... VALUES ($1)", {"name": marker}, RuntimeError(marker))

    with caplog.at_level(logging.DEBUG), acting_as(ALICE):
        await call(server, content=leading)
        monkeypatch.setattr(app.state.exchange_service, "import_exchange", exploding)
        with pytest.raises(Exception):  # noqa: B017 - the SDK's own wrapper
            await call(server, content=leading)

    assert marker not in caplog.text
    called = [r for r in caplog.records if getattr(r, "arguments", None)]
    assert called and all(
        str(r.arguments["content"]).startswith("<") and "chars>" in str(r.arguments["content"])  # type: ignore[attr-defined]
        for r in called
    )  # the length is what is said of a file


async def test_a_wire_is_not_needed_to_prove_the_door_never_reads_for_mcp() -> None:
    assert Wire and run  # (the HTTP door is covered by test_exchange_admission_callers)
