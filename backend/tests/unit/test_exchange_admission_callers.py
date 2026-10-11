"""One caller cannot hold every place (docs/adr/0048).

The places for imports and for exports are few and global; a single account — an editor, or for
an export a mere reader — could take them all and keep everyone else out. So each caller also has
a place of their own, keyed by the `sub` of the verified token and by nothing the request says.
What this does not do: someone with several accounts still fills the global places.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ea.core.config import Settings
from ea.domain.errors import ExportInProgressError, ImportInProgressError
from ea.domain.exchange import ExchangeFormat, ExportRequest, ImportContent, ImportOptions
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as
from ea.services.exchange_admission import Admission
from ea.services.exchanges import ExchangeService
from tests.conftest import InMemoryExchange, StaticVerifier, an_editor
from tests.unit.test_exchange_admission import Parser, Wire, run, settle

pytestmark = pytest.mark.asyncio

ALICE = an_editor(subject="alice", username="alice")
BOB = an_editor(subject="bob", username="bob")
CAROL = an_editor(subject="carol", username="carol")
TOKENS = {
    "alice": ALICE,
    "alice-again": ALICE,  # another token of the same account
    "bob": BOB,
    "carol": CAROL,
}


def build(
    service: ArchitectureService, store: InMemoryExchange, parser: Parser, **settings: Any
) -> Any:
    app = create_app(
        Settings(debug=True, auth_enabled=True, **settings),
        architecture_service=service,
        exchanges=store,  # type: ignore[arg-type]
        verifier=StaticVerifier(TOKENS),
    )
    app.state.exchange_service = ExchangeService(
        store, service, parser=parser, imports=app.state.import_admission
    )
    return app


@pytest.fixture
def parser() -> Parser:
    return Parser()


async def hold(app: Any, parser: Parser, token: str) -> tuple[Wire, asyncio.Task[None]]:
    wire = Wire()
    task = run(app, wire, token)
    await settle()
    await asyncio.to_thread(parser.entered.wait, 5)
    return wire, task


async def test_a_caller_who_holds_a_place_gets_no_second_one_and_the_body_is_not_read(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    first, task = await hold(app, parser, "alice")

    second = Wire()
    await run(app, second, "alice-again")  # the same account, another token

    assert second.status == 409 and (second.reads, second.sent) == (0, 0)
    assert app.state.import_admission.in_flight == 1  # no global place was spent on the refusal
    other = Wire()
    other_task = run(app, other, "bob")  # the others keep their access
    await settle()
    assert app.state.import_admission.in_flight == 2
    parser.release.set()
    await asyncio.gather(task, other_task)
    assert first.status == 200


async def test_two_callers_hold_the_global_places_and_a_third_waits(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    _, a = await hold(app, parser, "alice")
    _, b = await hold(app, parser, "bob")

    third = Wire()
    await run(app, third, "carol")

    assert third.status == 409 and third.reads == 0
    parser.release.set()
    await asyncio.gather(a, b)
    assert app.state.import_admission.in_flight == 0


async def test_the_key_is_the_verified_sub_never_a_header_or_a_part(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    _, task = await hold(app, parser, "alice")

    spoof = Wire()
    spoof.headers  # noqa: B018
    await run(app, spoof, "alice")  # (a header naming someone else is not even looked at)

    assert spoof.status == 409
    assert set(app.state.import_admission.callers) == {"alice"}  # not the name that was claimed
    parser.release.set()
    await task


async def test_the_table_of_callers_is_empty_after_a_hundred_cycles_and_a_hundred_cancellations(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser, exchange_read_timeout_s=0.05)
    parser.block = False

    for number in range(100):
        wire = Wire()
        await run(app, wire, ("alice", "bob", "carol")[number % 3])
    for number in range(100):
        task = run(app, Wire(size=5_000_000, stall=True), ("alice", "bob")[number % 2])
        await settle()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert app.state.import_admission.in_flight == 0
    assert app.state.import_admission.callers == {}  # keys are dropped at zero: no growth


async def test_a_second_import_of_a_caller_through_another_door_is_refused_too(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    """The MCP tool and a script reach the service without the HTTP door: same admission."""
    app = build(service, exchange_store, parser)
    _, task = await hold(app, parser, "alice")
    exchange = app.state.exchange_service
    content = ImportContent(ExchangeFormat.ARCHIMATE_XML, b"<model/>")

    with acting_as(ALICE), pytest.raises(ImportInProgressError):
        await exchange.import_exchange(content, ImportOptions())

    parser.block = False
    with acting_as(BOB):
        await exchange.import_exchange(content, ImportOptions())  # another caller gets in
    parser.release.set()
    await task
    assert app.state.import_admission.callers == {}


async def test_one_export_per_caller_and_the_others_keep_theirs(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    exchange = ExchangeService(exchange_store, service)

    with acting_as(ALICE):
        held = await exchange.export(ExportRequest())
        with pytest.raises(ExportInProgressError):
            await exchange.export(ExportRequest())
    with acting_as(BOB):
        other = await exchange.export(ExportRequest())
    assert exchange._exports.in_flight == 2

    await held.chunks.aclose()  # type: ignore[attr-defined]
    with acting_as(ALICE):
        again = await exchange.export(ExportRequest())  # her place came back
    for export in (other, again):
        await export.chunks.aclose()  # type: ignore[attr-defined]
    assert exchange._exports.callers == {}


async def test_a_reader_is_held_to_one_export_like_anybody(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    from tests.conftest import a_reader

    exchange = ExchangeService(exchange_store, service)

    with acting_as(a_reader()):
        held = await exchange.export(ExportRequest())
        with pytest.raises(ExportInProgressError):
            await exchange.export(ExportRequest())
        await held.chunks.aclose()  # type: ignore[attr-defined]


async def test_the_per_caller_limit_is_never_above_the_global_one() -> None:
    from pydantic import ValidationError

    for settings in (
        {"exchange_imports_per_caller": 3},
        {"exchange_exports_per_caller": 5, "exchange_max_exports": 4},
        {"exchange_imports_per_caller": 0},
    ):
        with pytest.raises(ValidationError):
            Settings(debug=True, auth_enabled=False, **settings)
    ok = Settings(debug=True, auth_enabled=False, exchange_exports_per_caller=2)
    assert ok.exchange_imports_per_caller == 1


async def test_the_admission_alone_refuses_the_caller_before_the_crowd() -> None:
    admission = Admission(4, per_caller=1)

    async with admission.lease("a"):
        with pytest.raises(ImportInProgressError):
            async with admission.lease("a"):
                pytest.fail("a second place for the same caller")
        async with admission.lease("b"):
            assert admission.in_flight == 2 and admission.callers == {"a": 1, "b": 1}
    assert admission.callers == {}
