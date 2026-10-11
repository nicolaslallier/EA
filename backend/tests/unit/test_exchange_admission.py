"""An import is admitted before a byte of its body is read (docs/adr/0050).

FastAPI parses a multipart body before it resolves anything, so the places for imports in
flight are taken by the ASGI door (`api/body_limit.py`), after the token and the role and
before the first read. These tests drive the app with a hand-made `receive` that counts what
the application pulled from the wire: a refused import must have pulled nothing.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from ea.core.config import Settings
from ea.domain.errors import InvalidExchangeFileError
from ea.domain.exchange import ExchangeModel
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService
from tests.conftest import (
    InMemoryExchange,
    StaticVerifier,
    a_reader,
    an_editor,
)

pytestmark = pytest.mark.asyncio

HEAD = (
    b'--B\r\nContent-Disposition: form-data; name="file"; filename="m.xml"\r\n'
    b"Content-Type: application/xml\r\n\r\n"
)
TAIL = b"\r\n--B--\r\n"


class Wire:
    """The client's end: a multipart body of `size` bytes, handed out on demand and counted."""

    def __init__(self, size: int = 100, *, stall: bool = False, delay: float = 0.0) -> None:
        self.size = size
        self.delay = delay  # a slow but steady client
        self.stall = stall  # never finishes sending: the client is slow
        self.sent = 0
        self.reads = 0
        self._chunks = self._generate()
        self.answer: list[dict[str, Any]] = []

    def _generate(self) -> Any:
        yield HEAD, True
        left = self.size
        while left:
            step = min(left, 64 * 1024)
            left -= step
            yield b"x" * step, True
        yield TAIL, False

    @property
    def headers(self) -> list[tuple[bytes, bytes]]:
        length = len(HEAD) + self.size + len(TAIL)
        return [
            (b"content-type", b"multipart/form-data; boundary=B"),
            (b"content-length", str(length).encode()),
        ]

    async def receive(self) -> dict[str, Any]:
        self.reads += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        item = next(self._chunks, None)
        if item is None or (self.stall and self.sent > 1000):
            if self.stall:
                await asyncio.Event().wait()
            return {"type": "http.disconnect"}
        chunk, more = item
        self.sent += len(chunk)
        return {"type": "http.request", "body": chunk, "more_body": more}

    async def send(self, message: dict[str, Any]) -> None:
        self.answer.append(message)

    @property
    def status(self) -> int:
        return next(m["status"] for m in self.answer if m["type"] == "http.response.start")


class Parser:
    """Stands in for the parsing: holds every caller while `block` is set, then fails or not."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.block = True
        self.fail: Exception | None = None

    def __call__(self, data: bytes) -> ExchangeModel:
        self.entered.set()
        if self.block:
            self.release.wait(10)
        if self.fail is not None:
            raise self.fail
        return ExchangeModel()


def build(
    service: ArchitectureService,
    store: InMemoryExchange,
    parser: Parser,
    *,
    verifier: StaticVerifier | None = None,
    **settings: Any,
) -> Any:
    app = create_app(
        Settings(
            debug=True,
            auth_enabled=verifier is not None,
            # these tests hold two places with one caller: the per-caller ceiling has its own file
            **{"exchange_imports_per_caller": 2, **settings},
        ),
        architecture_service=service,
        exchanges=store,  # type: ignore[arg-type]
        verifier=verifier,
    )
    app.state.exchange_service = ExchangeService(store, service, parser=parser)
    return app


def run(app: Any, wire: Wire, token: str | None = None) -> asyncio.Task[None]:
    headers = [*wire.headers]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/exchanges/imports",
        "raw_path": b"/exchanges/imports",
        "query_string": b"",
        "headers": headers,
        "app": app,
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "root_path": "",
    }
    return asyncio.create_task(app(scope, wire.receive, wire.send))


async def settle() -> None:
    for _ in range(20):
        await asyncio.sleep(0)


async def two_in_flight(app: Any, parser: Parser) -> list[tuple[Wire, asyncio.Task[None]]]:
    held = []
    for _ in range(2):
        wire = Wire()
        held.append((wire, run(app, wire)))
        await settle()
    await asyncio.to_thread(parser.entered.wait, 5)
    return held


async def finish(parser: Parser, held: list[tuple[Wire, asyncio.Task[None]]]) -> None:
    parser.release.set()
    for _, task in held:
        await task


@pytest.fixture
def parser() -> Parser:
    return Parser()


async def test_a_third_import_is_refused_without_a_byte_of_its_body_read(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    held = await two_in_flight(app, parser)

    third = Wire()
    await run(app, third)

    assert third.status == 409
    assert (third.reads, third.sent) == (0, 0)  # nothing was pulled from the wire
    await finish(parser, held)
    assert [wire.status for wire, _ in held] == [200, 200]


async def test_the_place_comes_back_after_a_success(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    held = await two_in_flight(app, parser)
    await finish(parser, held)

    assert app.state.import_admission.in_flight == 0
    parser.block = False
    again = Wire()
    await run(app, again)
    assert again.status == 200


@pytest.mark.parametrize(
    ("failure", "status"),
    [(InvalidExchangeFileError("no"), 422), (RuntimeError("boom"), 500)],
    ids=["a refusal of the domain", "an unexpected exception"],
)
async def test_the_place_comes_back_after_a_failure(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    parser: Parser,
    failure: Exception,
    status: int,
) -> None:
    app = build(service, exchange_store, parser)
    parser.block, parser.fail = False, failure

    for _ in range(100):  # a leak would show long before the hundredth
        wire = Wire()
        await run(app, wire)
        assert wire.status == status

    assert app.state.import_admission.in_flight == 0


async def test_the_place_comes_back_when_the_client_leaves_during_the_body(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)

    for _ in range(100):
        task = run(app, Wire(size=5_000_000, stall=True))
        await settle()
        assert app.state.import_admission.in_flight == 1  # taken before the body was read
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert app.state.import_admission.in_flight == 0


async def test_the_place_comes_back_when_the_client_leaves_while_waiting(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    first = await two_in_flight(app, parser)  # one parsing, one waiting its turn
    waiting = first[1][1]

    waiting.cancel()
    await asyncio.gather(waiting, return_exceptions=True)

    assert app.state.import_admission.in_flight == 1  # only the one parsing
    await finish(parser, first[:1])
    assert app.state.import_admission.in_flight == 0


async def test_the_place_is_kept_until_the_analysing_thread_has_really_finished(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    held = await two_in_flight(app, parser)
    parsing = held[0][1]

    parsing.cancel()  # the client is gone, the thread cannot be stopped
    await asyncio.gather(parsing, return_exceptions=True)
    await settle()

    assert app.state.import_admission.in_flight == 2  # its place is still taken
    refused = Wire()
    await run(app, refused)
    assert refused.status == 409 and refused.reads == 0

    parser.release.set()
    await asyncio.gather(held[1][1])
    for _ in range(50):
        if app.state.import_admission.in_flight == 0:
            break
        await asyncio.sleep(0.01)
    assert app.state.import_admission.in_flight == 0


async def test_neither_an_anonymous_caller_nor_a_reader_takes_a_place(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    verifier = StaticVerifier({"editor": an_editor(), "reader": a_reader()})
    app = build(service, exchange_store, parser, verifier=verifier)
    held = []
    for _ in range(2):
        wire = Wire()
        held.append((wire, run(app, wire, "editor")))
        await settle()
    await asyncio.to_thread(parser.entered.wait, 5)

    anonymous, reader = Wire(), Wire()
    await run(app, anonymous)
    await run(app, reader, "reader")

    assert (anonymous.status, reader.status) == (401, 403)  # not 409: refused before the door
    assert (anonymous.reads, reader.reads) == (0, 0)
    assert app.state.import_admission.in_flight == 2
    await finish(parser, held)


async def test_six_big_sends_at_once_hold_at_most_two_bodies(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(service, exchange_store, parser)
    size = 19 * 1024 * 1024
    wires = [Wire(size) for _ in range(6)]
    tasks = []
    for wire in wires:
        tasks.append(run(app, wire))
        await settle()
        if wire is wires[1]:
            await asyncio.to_thread(parser.entered.wait, 5)
    await asyncio.sleep(0.2)

    refused = [w for w in wires if w.answer and w.status == 409]
    assert len(refused) == 4 and all(w.sent == 0 for w in refused)
    assert sum(w.sent for w in wires) <= 2 * (size + len(HEAD) + len(TAIL))
    parser.release.set()
    await asyncio.gather(*tasks)
    assert app.state.import_admission.in_flight == 0


# --- A client that stops sending holds no place for ever -------------------------------------


def quick(service: ArchitectureService, store: InMemoryExchange, parser: Parser) -> Any:
    return build(service, store, parser, exchange_read_timeout_s=0.15, exchange_read_total_s=1.0)


async def test_two_silent_connections_are_cut_with_a_408_and_their_places_given_back(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = quick(service, exchange_store, parser)
    parser.block = False
    silent = [Wire(size=5_000_000, stall=True) for _ in range(2)]
    tasks = [run(app, wire) for wire in silent]
    await settle()
    assert app.state.import_admission.in_flight == 2  # both hold a place while they stall

    await asyncio.gather(*tasks)

    assert [wire.status for wire in silent] == [408, 408]
    assert app.state.import_admission.in_flight == 0
    third = Wire()
    await run(app, third)
    assert third.status == 200  # a normal import enters


async def test_the_408_is_the_typed_envelope(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    import json

    app = quick(service, exchange_store, parser)
    wire = Wire(size=5_000_000, stall=True)

    await run(app, wire)

    body = next(m["body"] for m in wire.answer if m["type"] == "http.response.body")
    assert json.loads(body)["error"] == "body_timeout"


async def test_a_slow_but_steady_client_passes_under_the_per_message_delay(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = quick(service, exchange_store, parser)
    parser.block = False
    wire = Wire(size=100_000, delay=0.02)  # 3 messages of 20 ms: well under 150 ms each

    await run(app, wire)

    assert wire.status == 200


async def test_a_steady_client_is_cut_by_the_total_delay(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = build(
        service, exchange_store, parser, exchange_read_timeout_s=0.15, exchange_read_total_s=0.3
    )
    parser.block = False
    wire = Wire(size=2_000_000, delay=0.02)  # 32 messages: 0.6 s in all, over the 0.2 s total

    await run(app, wire)

    assert wire.status == 408
    assert app.state.import_admission.in_flight == 0


async def test_the_delays_run_only_while_the_body_is_read(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    app = quick(service, exchange_store, parser)
    wire = Wire()
    task = run(app, wire)
    await asyncio.to_thread(parser.entered.wait, 5)

    await asyncio.sleep(1.3)  # analysis outlasts both delays: it is not the client's silence
    assert not task.done()
    parser.release.set()
    await task

    assert wire.status == 200


async def test_the_delays_are_validated_settings() -> None:
    from pydantic import ValidationError

    for name, value in (("exchange_read_timeout_s", 0), ("exchange_read_total_s", 10_000)):
        with pytest.raises(ValidationError):
            Settings(debug=True, auth_enabled=False, **{name: value})


async def test_on_a_real_server_two_slow_connections_do_not_keep_a_normal_import_out(
    service: ArchitectureService, exchange_store: InMemoryExchange, parser: Parser
) -> None:
    import uvicorn

    from tests.exchange_files import el, exchange_xml

    parser.block = False
    app = build(service, exchange_store, parser, exchange_read_timeout_s=0.3)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="error")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    head = (
        b"POST /exchanges/imports HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: multipart/form-data; boundary=B\r\nContent-Length: %d\r\n"
        b"Connection: close\r\n\r\n"
    )
    file_part = HEAD + exchange_xml(el("a", "Node", "A")) + TAIL
    try:
        slow = []
        for _ in range(2):  # a few bytes of a body announced as 1000, then silence
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write((head % 1000) + b"--B\r\n")
            await writer.drain()
            slow.append((reader, writer))
        await asyncio.sleep(0.1)
        assert app.state.import_admission.in_flight == 2
        for reader, writer in slow:
            answer = await asyncio.wait_for(reader.read(), 5)
            writer.close()
            assert answer.startswith(b"HTTP/1.1 408")
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write((head % len(file_part)) + file_part)
        await writer.drain()
        assert (await asyncio.wait_for(reader.read(), 5)).startswith(b"HTTP/1.1 200")
        writer.close()
    finally:
        server.should_exit = True
        await task
    assert app.state.import_admission.in_flight == 0
