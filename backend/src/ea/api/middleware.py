"""One log line per request, and the id that ties every other line to it.

Written as a plain ASGI middleware rather than a `BaseHTTPMiddleware`
subclass, for one reason that matters here: `/mcp` is spliced onto this app
(docs/adr/0014) and answers over a streaming transport, which
`BaseHTTPMiddleware` buffers through an anyio stream. This wraps `send`
instead, so a streamed answer stays streamed.

What it does is deliberately small. It gives the request an id, puts it where
`RequestIdFilter` will find it, hands it back in a header, and — once the
answer is on its way — says what happened, at a level that depends on what
happened. Everything else a call site logs during that window carries the same
id, which is the whole point: an access line on its own is a fact with no
context, and twelve context lines with no request id are a puzzle.
"""

from __future__ import annotations

import logging
import re
from time import perf_counter
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

from ea.core.logging import REQUESTS_LOGGER, request_id

if TYPE_CHECKING:
    from collections.abc import Iterable

    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(REQUESTS_LOGGER)

#: The header the id is read from and answered in. The conventional spelling,
#: so a proxy or a caller that already sets one is followed rather than fought.
REQUEST_ID_HEADER: Final = "X-Request-Id"

#: Paths whose success says nothing. A liveness probe polls `/health` forever
#: and a log full of its 200s is a log nobody reads; its *failures* still
#: surface, because the level below is decided by the status first.
QUIET_PATHS: Final[frozenset[str]] = frozenset({"/health"})

#: What an id from outside may look like. It is echoed into a header and into
#: every log line of the request, so it is bounded and it is not free text: a
#: newline in a log line is a second, forged log line.
_ID: Final = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")


def _identifier(scope: Scope) -> str:
    """The caller's id when it brought one we can safely repeat, else a fresh one."""
    wanted = REQUEST_ID_HEADER.lower().encode()
    headers: Iterable[tuple[bytes, bytes]] = scope.get("headers", ())
    for name, value in headers:
        if name.lower() == wanted:
            candidate = value.decode("latin-1", "replace")
            if _ID.match(candidate):
                return candidate
            break
    return uuid4().hex[:12]


def _level(status: int, path: str) -> int:
    """A fault, a refusal, a probe, or an ordinary answer — in that order."""
    if status >= 500:
        return logging.ERROR
    if status >= 400:
        return logging.WARNING
    if path in QUIET_PATHS:
        return logging.DEBUG
    return logging.INFO


class RequestLogging:
    """Log every HTTP request, once, with its id, its outcome and its duration."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        identifier = _identifier(scope)
        token = request_id.set(identifier)
        started = perf_counter()
        # The value used when the application raises before answering: nothing
        # was sent, and what the client will be told is a 500.
        status = 500

        async def sending(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"].append((REQUEST_ID_HEADER.lower().encode(), identifier.encode()))
            await send(message)

        try:
            await self._app(scope, receive, sending)
        finally:
            path = scope.get("path", "")
            elapsed = (perf_counter() - started) * 1000
            fields: dict[str, Any] = {
                "method": scope.get("method", ""),
                "path": path,
                "query": scope.get("query_string", b"").decode("latin-1", "replace"),
                "status": status,
                "duration_ms": round(elapsed, 1),
            }
            logger.log(
                _level(status, path),
                "%s %s -> %s in %.1f ms",
                fields["method"],
                path,
                status,
                elapsed,
                extra=fields,
            )
            request_id.reset(token)
