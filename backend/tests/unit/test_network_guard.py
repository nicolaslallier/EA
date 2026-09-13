"""The guard that keeps the DB-free suites off the network.

`tests/unit` and `tests/e2e` are the suites `make check` runs and CI will run,
and they promise to need no database. That promise broke silently once: the
settings defaults point PostgreSQL and the embedding service at the cluster, a
lifespan entered with those defaults went looking for them, and off the LAN the
suite took four minutes to fail nine tests with timeouts. The guard in
`tests/conftest.py` turns the next such test into an immediate failure that
says what it tried to reach.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from tests.conftest import NetworkAccessInTestError


def test_a_connection_to_another_machine_fails_at_once_and_names_it() -> None:
    with (
        socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock,
        pytest.raises(NetworkAccessInTestError, match=r"192\.0\.2\.1:5432"),
    ):
        sock.connect(("192.0.2.1", 5432))


@pytest.mark.asyncio
async def test_an_asyncio_connection_to_another_machine_fails_the_same_way() -> None:
    """The drivers connect through asyncio, not through a blocking `connect`."""
    with pytest.raises(NetworkAccessInTestError, match=r"192\.0\.2\.1"):
        await asyncio.open_connection("192.0.2.1", 1234)


def test_resolving_a_remote_name_fails_rather_than_waiting_on_dns() -> None:
    """Off any network a lookup can hang for the resolver's own timeout."""
    with pytest.raises(NetworkAccessInTestError, match=r"graph\.example"):
        socket.getaddrinfo("graph.example", 7687)


@pytest.mark.asyncio
async def test_loopback_is_still_reachable() -> None:
    """The throwaway PostgreSQL of `make test-postgres` listens on 127.0.0.1."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        reader, writer = await asyncio.open_connection("localhost", port)
        assert await reader.read() == b"ok"
        writer.close()
