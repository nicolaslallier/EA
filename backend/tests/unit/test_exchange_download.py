"""An export holds no database connection while a client reads it (docs/adr/0050).

The file is rendered into a temporary file while the snapshot is open; the snapshot is given
back; only then does the first byte go out, from the file. So a slow reader costs a descriptor
and a place in a short queue — never a connection of the pool, which ten slow downloads would
otherwise empty for the whole API. Every way an export can end gives back the file and the place.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
import zipfile
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from ea.core.config import Settings
from ea.domain.archimate import ElementType as E
from ea.domain.errors import ExportInProgressError, ExportTooLargeError
from ea.domain.exchange import ExchangeFormat, ExportRequest
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService
from tests.conftest import FIXED_NOW, InMemoryExchange

pytestmark = pytest.mark.asyncio
CSV = ExportRequest(format=ExchangeFormat.CSV)


class Files:
    """Every temporary file the service opens, to ask whether each was closed."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.opened: list[tempfile.SpooledTemporaryFile[bytes]] = []
        real = tempfile.SpooledTemporaryFile

        def recording(*args: Any, **kwargs: Any) -> Any:
            made = real(*args, **kwargs)
            self.opened.append(made)
            return made

        monkeypatch.setattr("ea.services.exchanges.tempfile.SpooledTemporaryFile", recording)

    def all_closed(self) -> bool:
        return bool(self.opened) and all(f.closed for f in self.opened)


def watch_snapshots(store: InMemoryExchange, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    events: list[str] = []
    original = store.snapshot

    @asynccontextmanager
    async def snapshot(scope: Any):  # type: ignore[no-untyped-def]
        events.append("open")
        async with original(scope) as opened:
            yield opened
        events.append("closed")

    monkeypatch.setattr(store, "snapshot", snapshot)
    return events


async def fill(service: ArchitectureService, count: int = 5) -> None:
    for number in range(count):
        await service.create_element(element_type=E.NODE, name=f"node {number}")


def importer(store: InMemoryExchange, service: ArchitectureService, **kw: Any) -> ExchangeService:
    return ExchangeService(store, service, clock=lambda: FIXED_NOW, **kw)


@pytest.mark.parametrize("request_", [ExportRequest(), CSV], ids=["xml", "csv"])
async def test_the_snapshot_is_given_back_before_the_first_byte_is_read(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    monkeypatch: pytest.MonkeyPatch,
    request_: ExportRequest,
) -> None:
    await fill(service)
    events = watch_snapshots(exchange_store, monkeypatch)

    export = await importer(exchange_store, service).export(request_)

    assert events == ["open", "closed"]  # nothing has been read yet: a slow reader holds nothing
    first = await export.chunks.__anext__()
    assert first
    await export.chunks.aclose()  # type: ignore[attr-defined]
    assert events == ["open", "closed"]


async def test_the_body_is_the_file_and_its_length_is_known(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    await fill(service, 50)

    export = await importer(exchange_store, service).export(ExportRequest())
    body = b"".join([chunk async for chunk in export.chunks])

    assert export.size == len(body) and body.startswith(b"<?xml")
    assert b"node 49" in body


async def test_the_chunks_are_of_a_bounded_size(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    await fill(service, 3_000)

    export = await importer(exchange_store, service).export(ExportRequest())
    sizes = [len(chunk) async for chunk in export.chunks]

    assert max(sizes) <= 65_536 and len(sizes) > 1


@pytest.mark.parametrize("request_", [ExportRequest(), CSV], ids=["xml", "csv"])
@pytest.mark.parametrize("ending", ["end", "client leaves", "never read", "cancelled"])
async def test_the_file_and_the_place_come_back_however_the_download_ends(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    monkeypatch: pytest.MonkeyPatch,
    request_: ExportRequest,
    ending: str,
) -> None:
    await fill(service, 500)
    files = Files(monkeypatch)
    exchange = importer(exchange_store, service)

    export = await exchange.export(request_)
    assert exchange._exports.in_flight == 1
    if ending == "end":
        _ = [chunk async for chunk in export.chunks]
    elif ending == "client leaves":
        await export.chunks.__anext__()
        await export.chunks.aclose()  # type: ignore[attr-defined]
    elif ending == "never read":
        await export.chunks.aclose()  # type: ignore[attr-defined]
    else:
        task = asyncio.ensure_future(export.chunks.__anext__())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await export.chunks.aclose()  # type: ignore[attr-defined]

    assert files.all_closed()
    assert exchange._exports.in_flight == 0


async def test_a_hundred_downloads_leak_neither_a_file_nor_a_place(
    service: ArchitectureService, exchange_store: InMemoryExchange, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fill(service)
    files = Files(monkeypatch)
    exchange = importer(exchange_store, service)

    for number in range(100):
        export = await exchange.export(CSV if number % 2 else ExportRequest())
        if number % 3 == 0:
            await export.chunks.aclose()  # type: ignore[attr-defined]
        else:
            _ = [chunk async for chunk in export.chunks]

    assert files.all_closed() and exchange._exports.in_flight == 0


async def test_a_failure_while_rendering_gives_back_the_file_the_place_and_the_snapshot(
    service: ArchitectureService,
    exchange_store: InMemoryExchange,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await fill(service)
    files = Files(monkeypatch)
    events = watch_snapshots(exchange_store, monkeypatch)
    exchange = importer(exchange_store, service)

    async def broken(offset: int, limit: int) -> tuple[object, ...]:
        raise RuntimeError("the disk of the database caught fire")

    original = exchange_store.snapshot

    @asynccontextmanager
    async def failing(scope: Any):  # type: ignore[no-untyped-def]
        async with original(scope) as opened:
            opened.elements_page = broken  # type: ignore[method-assign]
            yield opened

    monkeypatch.setattr(exchange_store, "snapshot", failing)
    with pytest.raises(RuntimeError):
        await exchange.export(ExportRequest())

    assert files.all_closed() and exchange._exports.in_flight == 0
    assert events == ["open", "closed"]  # and the snapshot was given back


async def test_a_scope_that_cannot_be_resolved_takes_no_place_for_long(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    from uuid import uuid4

    from ea.domain.errors import UnknownScopeError
    from ea.domain.exchange import ExportScope, ScopeKind

    exchange = importer(exchange_store, service)

    for _ in range(10):
        with pytest.raises(UnknownScopeError):
            await exchange.export(
                ExportRequest(scope=ExportScope(ScopeKind.PACKAGE, package_id=uuid4()))
            )

    assert exchange._exports.in_flight == 0


async def test_one_more_export_than_the_places_is_refused_without_opening_a_snapshot(
    service: ArchitectureService, exchange_store: InMemoryExchange, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fill(service)
    events = watch_snapshots(exchange_store, monkeypatch)
    exchange = importer(exchange_store, service, max_exports=4, exports_per_caller=4)
    held = [await exchange.export(ExportRequest()) for _ in range(4)]
    opened = events.count("open")

    with pytest.raises(ExportInProgressError):
        await exchange.export(ExportRequest())

    assert events.count("open") == opened  # not a connection for the one that was refused
    await held[0].chunks.aclose()  # type: ignore[attr-defined]
    again = await exchange.export(ExportRequest())  # a place came back
    await again.chunks.aclose()  # type: ignore[attr-defined]
    for export in held[1:]:
        await export.chunks.aclose()  # type: ignore[attr-defined]


async def test_an_export_over_the_ceiling_is_refused_before_a_byte_goes_out(
    service: ArchitectureService, exchange_store: InMemoryExchange, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fill(service, 200)
    files = Files(monkeypatch)
    events = watch_snapshots(exchange_store, monkeypatch)
    exchange = importer(exchange_store, service, max_export_bytes=2_000)

    for request in (ExportRequest(), CSV):
        with pytest.raises(ExportTooLargeError) as raised:
            await exchange.export(request)
        assert "narrower" in str(raised.value)

    assert files.all_closed() and exchange._exports.in_flight == 0
    assert events == ["open", "closed"] * 2


async def test_the_ceiling_is_a_named_bound_and_the_zip_is_not_what_is_counted() -> None:
    from ea.domain.exchange import MAX_EXPORT_BYTES, MAX_EXPORTS_IN_FLIGHT

    assert MAX_EXPORT_BYTES > 20 * 1024 * 1024 and MAX_EXPORTS_IN_FLIGHT == 4


# --- Over HTTP ---------------------------------------------------------------------------------


class SlowClient:
    """An ASGI client: it takes the answer as slowly as it is told to, or not at all."""

    def __init__(self, *, delay: float = 0.0, never: bool = False) -> None:
        self.delay, self.never = delay, never
        self.messages: list[dict[str, Any]] = []
        self.blocked = asyncio.Event()

    async def receive(self) -> dict[str, Any]:
        await asyncio.Event().wait()  # no body; the client stays connected
        return {"type": "http.disconnect"}

    async def send(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        if self.never and message["type"] == "http.response.body":
            self.blocked.set()
            await asyncio.Event().wait()
        if self.delay:
            await asyncio.sleep(self.delay)


def scope_of(app: Any, query: str = "") -> dict[str, Any]:
    return {
        "type": "http",
        "method": "GET",
        "path": "/exchanges/export",
        "raw_path": b"/exchanges/export",
        "query_string": query.encode(),
        "headers": [],
        "app": app,
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "root_path": "",
    }


def build_app(
    service: ArchitectureService, store: InMemoryExchange, **settings: Any
) -> tuple[Any, ExchangeService]:
    app = create_app(
        Settings(debug=True, auth_enabled=False, **settings),
        architecture_service=service,
        exchanges=store,  # type: ignore[arg-type]
    )
    return app, app.state.exchange_service


async def test_a_client_that_never_reads_is_cut_after_the_send_timeout(
    service: ArchitectureService, exchange_store: InMemoryExchange, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fill(service, 2_000)
    files = Files(monkeypatch)
    app, exchange = build_app(service, exchange_store, exchange_send_timeout_s=0.1)
    client = SlowClient(never=True)

    await app(scope_of(app), client.receive, client.send)  # returns: the response was cut

    assert client.blocked.is_set()
    assert files.all_closed() and exchange._exports.in_flight == 0
    assert not any(m.get("more_body") is False for m in client.messages)  # it never completed


async def test_a_slow_but_steady_client_gets_the_whole_file_inside_the_timeout(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    await fill(service, 3_000)
    app, exchange = build_app(service, exchange_store, exchange_send_timeout_s=5.0)
    client = SlowClient(delay=0.002)

    await app(scope_of(app), client.receive, client.send)

    body = b"".join(m.get("body", b"") for m in client.messages)
    assert body.endswith(b"</model>\n") and exchange._exports.in_flight == 0
    start = next(m for m in client.messages if m["type"] == "http.response.start")
    assert dict(start["headers"])[b"content-length"] == str(len(body)).encode()


async def test_the_fifth_download_is_a_429_with_retry_after_and_no_snapshot(
    service: ArchitectureService, exchange_store: InMemoryExchange, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fill(service, 2_000)
    events = watch_snapshots(exchange_store, monkeypatch)
    app, exchange = build_app(service, exchange_store, exchange_max_exports=1)
    first = SlowClient(never=True)
    task = asyncio.create_task(app(scope_of(app), first.receive, first.send))
    await first.blocked.wait()
    opened = events.count("open")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as other:
        refused = await other.get("/exchanges/export")

    assert refused.status_code == 429
    assert refused.json()["error"] == "export_in_progress"
    assert int(refused.headers["retry-after"]) > 0
    assert events.count("open") == opened
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert exchange._exports.in_flight == 0


async def test_an_export_over_the_ceiling_is_a_typed_422_over_http(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    await fill(service, 200)
    app, exchange = build_app(service, exchange_store)
    exchange._max_export_bytes = 2_000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/exchanges/export")

    assert response.status_code == 422 and response.json()["error"] == "export_too_large"
    assert exchange._exports.in_flight == 0


async def test_a_reader_is_held_to_the_same_places(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    from tests.conftest import StaticVerifier, a_reader

    await fill(service)
    app = create_app(
        Settings(debug=True, auth_enabled=True, exchange_max_exports=1),
        architecture_service=service,
        exchanges=exchange_store,  # type: ignore[arg-type]
        verifier=StaticVerifier({"reader": a_reader()}),
    )
    from ea.services.caller import acting_as

    with acting_as(a_reader()):
        first = await app.state.exchange_service.export(ExportRequest())
        with pytest.raises(ExportInProgressError):
            await app.state.exchange_service.export(ExportRequest())
        await first.chunks.aclose()  # type: ignore[attr-defined]


async def test_the_settings_that_bound_the_downloads_are_validated() -> None:
    from pydantic import ValidationError

    for name, bad in (
        ("exchange_max_exports", 0),
        ("exchange_max_exports", 1_000),
        ("exchange_send_timeout_s", 0),
        ("exchange_send_timeout_s", 100_000),
    ):
        with pytest.raises(ValidationError):
            Settings(debug=True, auth_enabled=False, **{name: bad})
    assert Settings(debug=True, auth_enabled=False).exchange_send_timeout_s == 300.0


async def test_the_zip_of_a_download_is_the_same_whatever_the_route_to_it(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    await fill(service, 20)
    exchange = importer(exchange_store, service)
    first = await exchange.export(CSV)
    one = b"".join([chunk async for chunk in first.chunks])
    second = await exchange.export(CSV)
    two = b"".join([chunk async for chunk in second.chunks])

    assert one == two
    archive = zipfile.ZipFile(io.BytesIO(one))
    assert archive.namelist() == ["elements.csv", "relations.csv", "properties.csv"]
    assert first.filename.endswith(".zip")


async def test_on_a_real_server_the_api_answers_while_a_client_reads_one_byte_at_a_time(
    service: ArchitectureService, exchange_store: InMemoryExchange
) -> None:
    import uvicorn

    for number in range(300):  # about 6 MB of documentation: far more than the sockets buffer
        await service.create_element(
            element_type=E.NODE, name=f"n{number}", documentation="d" * 20_000
        )
    app, exchange = build_app(service, exchange_store, exchange_send_timeout_s=60)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="error")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port, limit=1)
        writer.write(b"GET /exchanges/export HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        await writer.drain()
        got = b""
        for _ in range(5):  # one byte every 100 ms: a client that takes its time
            got += await reader.read(1)
            await asyncio.sleep(0.1)
        assert exchange._exports.in_flight == 1

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as other:
            started = asyncio.get_running_loop().time()
            health = await other.get("/health")
            took = asyncio.get_running_loop().time() - started

        assert health.status_code == 200 and took < 1.0  # served while the slow client reads
        assert exchange._exports.in_flight == 1  # still sending
        writer.transport.abort()  # the client leaves
        for _ in range(100):
            if exchange._exports.in_flight == 0:
                break
            await asyncio.sleep(0.05)
        assert exchange._exports.in_flight == 0
    finally:
        server.should_exit = True
        await task
