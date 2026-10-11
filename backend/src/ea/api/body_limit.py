"""A ceiling on one endpoint's request body, enforced as the bytes arrive.

Starlette parses a multipart body *before* the route runs, so a handler that reads
`limit + 1` bytes of an `UploadFile` has already let the whole upload be received and
spooled. This middleware counts the bytes of the ASGI `receive` stream itself and answers
`413` the moment the ceiling is crossed. It trusts no `Content-Length`: a chunked body has
none, and a lying one costs nothing; an honest one that is already too large is refused
before a byte is read.

Pure ASGI, like the other middlewares here, so a stream is never buffered. It is added
*inside* `AnsweringUnexpectedFailures`: the disconnect it hands the application on
overflow makes the application raise, and that must reach this class — which swallows it
and answers 413 — not be logged there as an unexpected failure. CORS and the request id,
which wrap both, still stamp the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from ea.api.errors import domain_error_response
from ea.domain.errors import ExchangeFileTooLargeError

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: The multipart envelope around a file: boundaries, headers, form fields.
BODY_ENVELOPE_MARGIN: Final = 64 * 1024


class LimitingRequestBodies:
    def __init__(self, app: ASGIApp, *, path_suffix: str, limit: int) -> None:
        self._app = app
        self._suffix = path_suffix
        self._limit = limit

    def _too_large(self) -> ExchangeFileTooLargeError:
        ceiling = self._limit - BODY_ENVELOPE_MARGIN
        return ExchangeFileTooLargeError(f"an exchange file is at most {ceiling} bytes")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `endswith`, not `==`: behind the NGINX prefix, `path` may carry `/api`.
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].endswith(self._suffix)
        ):
            await self._app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length", b"0")
        if declared.isdigit() and int(declared) > self._limit:
            await domain_error_response(self._too_large())(scope, receive, send)
            return

        seen = 0
        exceeded = False

        async def counting() -> Message:
            nonlocal seen, exceeded
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self._limit:
                    exceeded = True
                    # Hang up on the application: it stops reading, and whatever it
                    # answers next is muted below and replaced by the 413.
                    return {"type": "http.disconnect"}
            return message

        async def muted(message: Message) -> None:
            if not exceeded:
                await send(message)

        try:
            await self._app(scope, counting, muted)
        except Exception:
            if not exceeded:
                raise
        if exceeded:
            await domain_error_response(self._too_large())(scope, receive, send)
