"""Who `/mcp` answers, decided before the MCP transport reads a byte.

Until authentication exists, the tools write to the graph for whoever reaches
them, and the API binds every interface (docs/adr/0016). The SDK's `Host`
allowlist does not narrow that: it is a defence against DNS rebinding — a
*browser* tricked into calling us cannot choose the `Host` it sends — and
nothing more, because a script on the LAN writes `Host: localhost:8000`
itself. The TCP peer is the one fact about a caller the caller does not write,
so that is what decides. See docs/adr/0023.

Behind a reverse proxy the peer is the proxy: this then admits whatever the
proxy admits, which is why remote callers are an explicit opt-in
(`EA_MCP_ALLOW_REMOTE_CLIENTS`) rather than something inferred.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any, Final

from starlette import status
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

#: The stable code a refused caller reads, in the API's own error envelope.
REMOTE_CLIENT_REFUSED: Final = "remote_client_refused"


def is_loopback_peer(client: Any) -> bool:
    """Whether an ASGI `client` — `(host, port)` or `None` — is this machine.

    Only an address counts. A server that reports no peer, or a name instead
    of an address, has told us nothing that can be checked, and an unchecked
    caller on a write path is refused rather than waved in.
    """
    if not client:
        return False
    try:
        address = ipaddress.ip_address(client[0])
    except (TypeError, ValueError):
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        # A dual-stack socket reports an IPv4 caller as `::ffff:a.b.c.d`.
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


class LoopbackClientsOnly:
    """Answer 403 to an HTTP caller whose TCP peer is not loopback."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not is_loopback_peer(scope.get("client")):
            refusal = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": REMOTE_CLIENT_REFUSED,
                    "detail": (
                        "/mcp only answers clients on this machine until authentication "
                        "exists; set EA_MCP_ALLOW_REMOTE_CLIENTS to serve others."
                    ),
                },
            )
            await refusal(scope, receive, send)
            return
        await self._app(scope, receive, send)
