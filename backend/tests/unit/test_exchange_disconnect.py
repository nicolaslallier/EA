"""What the server does when the client hangs up in the middle of an import.

Measured against a real uvicorn on the loopback, because the answer is not in our code:
it is in what the ASGI server does to the task of a request whose peer has gone. The answer
is written in docs/adr/0048 and the proxy settings it drives (an import outlasts NGINX's
`proxy_read_timeout`, so clients are told to replay): if this test changes, so must the ADR.
"""

from __future__ import annotations

import asyncio
import threading

import pytest
import uvicorn

from ea.core.config import Settings
from ea.domain.exchange import ExchangeModel
from ea.formats.archimate_xml import parse_model
from ea.main import create_app
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService
from tests.conftest import FIXED_NOW, InMemoryExchange, InMemoryRepository
from tests.exchange_files import el, exchange_xml

pytestmark = pytest.mark.asyncio


class HeldParser:
    """The real parser, but it waits for the test's signal before returning."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, data: bytes) -> ExchangeModel:
        self.entered.set()
        self.release.wait(10)
        return parse_model(data)


def a_request(dry_run: str) -> bytes:
    file_part = (
        b'--B\r\nContent-Disposition: form-data; name="file"; filename="m.xml"\r\n'
        b"Content-Type: application/xml\r\n\r\n" + exchange_xml(el("a", "Node", "A")) + b"\r\n"
    )
    field = (
        b'--B\r\nContent-Disposition: form-data; name="dry_run"\r\n\r\n'
        + dry_run.encode()
        + b"\r\n--B--\r\n"
    )
    body = file_part + field
    head = (
        b"POST /exchanges/imports HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: multipart/form-data; boundary=B\r\n"
        b"Content-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n"
    )
    return head + body


async def serve(app: object) -> tuple[uvicorn.Server, asyncio.Task[None], int]:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="off", log_level="error")  # type: ignore[arg-type]
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    return server, task, server.servers[0].sockets[0].getsockname()[1]


async def test_a_client_that_hangs_up_does_not_stop_the_import_which_commits() -> None:
    repository = InMemoryRepository()
    architecture = ArchitectureService(repository, clock=lambda: FIXED_NOW)
    store = InMemoryExchange(repository)
    app = create_app(
        Settings(debug=True, auth_enabled=False),
        architecture_service=architecture,
        exchanges=store,  # type: ignore[arg-type]
    )
    parser = HeldParser()
    app.state.exchange_service = ExchangeService(store, architecture, parser=parser)
    server, task, port = await serve(app)
    try:
        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(a_request("false"))
        await writer.drain()
        await asyncio.to_thread(parser.entered.wait, 10)  # the server is inside the import
        writer.transport.abort()  # the client is gone: NGINX's 60 s, a closed laptop, a Ctrl-C
        parser.release.set()
        for _ in range(500):
            if repository.elements:
                break
            await asyncio.sleep(0.01)
    finally:
        server.should_exit = True
        await task

    assert [e.name for e in repository.elements.values()] == ["A"]  # it went through
