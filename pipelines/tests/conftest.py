"""Fixtures shared by every suite."""

from __future__ import annotations

import ipaddress
import socket
from typing import Any

import pytest


class NetworkAccessInTestError(RuntimeError):
    """A test tried to reach another machine.

    A `RuntimeError` and deliberately not an `OSError`: an `OSError` would be
    misread as "service unreachable" rather than "this test forgot to inject
    a double" — see `backend/tests/conftest.py`, from which this is copied.
    """


def _is_loopback(host: object) -> bool:
    """`localhost`, `127.0.0.0/8`, `::1` and their IPv4-mapped spelling."""
    if host is None:
        return True  # a passive lookup (`getaddrinfo(None, port)`) goes nowhere
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(address.is_loopback or (mapped is not None and mapped.is_loopback))


@pytest.fixture(autouse=True)
def _no_network_beyond_this_machine(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse, at once, any connection a test opens to another machine.

    No test may reach the EA API, LiteLLM, MinIO, or the shared PostgreSQL: a
    test that needs one of them injects a double (`httpx.MockTransport`, a
    fake S3 object) instead. Loopback stays open for `prefect_test_harness`.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def refuse(what: str) -> NetworkAccessInTestError:
        return NetworkAccessInTestError(
            f"{request.node.nodeid} tried to reach {what}. No test may leave "
            "this machine: inject a double (httpx.MockTransport, a fake S3 object)."
        )

    def _target(address: object) -> tuple[bool, str]:
        if isinstance(address, str | bytes):  # AF_UNIX: a path on this machine
            return True, repr(address)
        if isinstance(address, tuple) and address:
            return _is_loopback(address[0]), ":".join(str(part) for part in address[:2])
        return False, repr(address)

    def connect(self: socket.socket, address: Any) -> None:
        local, shown = _target(address)
        if not local:
            raise refuse(shown)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        local, shown = _target(address)
        if not local:
            raise refuse(shown)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_loopback(host):
            raise refuse(f"{host!r} (name lookup)")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
