"""Who `/mcp` answers, decided from the TCP peer — see docs/adr/0023.

Until authentication exists, the MCP tools write to the graph for whoever
reaches them. The `Host` allowlist cannot narrow that: a script sets `Host`
to whatever the allowlist wants. The peer address is the one fact about a
caller the caller does not write, so that is what is checked.
"""

from __future__ import annotations

import pytest

from ea.mcp.transport import is_loopback_peer


@pytest.mark.parametrize(
    "client",
    [
        ("127.0.0.1", 50000),
        ("127.0.0.53", 50000),
        ("::1", 50000),
        ("::ffff:127.0.0.1", 50000),
    ],
)
def test_a_caller_on_this_machine_is_loopback(client: tuple[str, int]) -> None:
    assert is_loopback_peer(client) is True


@pytest.mark.parametrize(
    "client",
    [
        ("192.168.1.40", 50000),
        ("10.0.0.5", 50000),
        ("0.0.0.0", 50000),
        ("::ffff:192.168.1.40", 50000),
        ("fe80::1", 50000),
        # A name is not an address: Starlette's `TestClient` reports
        # `testclient`, and a server that reports a name has told us nothing
        # we can check.
        ("localhost", 50000),
        ("testclient", 50000),
    ],
)
def test_a_caller_anywhere_else_is_not(client: tuple[str, int]) -> None:
    assert is_loopback_peer(client) is False


def test_a_peer_the_server_did_not_report_is_not_trusted() -> None:
    """The ASGI `client` is optional; an unknown caller is refused, not waved in."""
    assert is_loopback_peer(None) is False
