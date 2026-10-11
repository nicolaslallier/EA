"""The door of an upload endpoint: who, then what role, then how big, and only then the body.

Starlette parses a multipart body *before* the route runs, so a handler that reads
`limit + 1` bytes of an `UploadFile` has already let the whole upload be received and
spooled. This middleware counts the bytes of the ASGI `receive` stream itself and answers
`413` the moment the ceiling is crossed. It trusts no `Content-Length`: a chunked body has
none, and a lying one costs nothing; an honest one that is already too large is refused
before a byte is read.

FastAPI parses a multipart body *before* it resolves any dependency, so `Authenticated` on
the router comes too late for an upload: an anonymous caller would have 20 MB buffered
(and spooled to disk) per request before being told 401. So the door is here, in this
order, none of it reading a byte of the body: the bearer token is verified by the app's own
verifier (`auth.caller_of`, not a second implementation), the caller must be an editor (the
service asks again, as defence in depth), the declared length must fit, and only then does
the application get a stream, counted as it arrives.

Pure ASGI, like the other middlewares here, so a stream is never buffered. It is added
*inside* `AnsweringUnexpectedFailures`: the disconnect it hands the application on
overflow makes the application raise, and that must reach this class — which swallows it
and answers 413 — not be logged there as an unexpected failure. CORS and the request id,
which wrap both, still stamp the answer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from fastapi.security.utils import get_authorization_scheme_param

from ea.api.auth import caller_of
from ea.api.errors import domain_error_response
from ea.domain.auth import EDITOR_ROLE
from ea.domain.errors import DomainError, ExchangeFileTooLargeError, NotAuthorisedError
from ea.services.exchange_admission import ImportAdmission, current_ticket

if TYPE_CHECKING:
    from fastapi import FastAPI
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

#: The multipart envelope around a file: boundaries, headers, form fields.
BODY_ENVELOPE_MARGIN: Final = 64 * 1024


class LimitingRequestBodies:
    def __init__(
        self,
        app: ASGIApp,
        *,
        path_suffix: str,
        limit: int,
        admission: ImportAdmission | None = None,
    ) -> None:
        self._app = app
        self._suffix = path_suffix
        self._limit = limit
        self._admission = admission

    def _too_large(self) -> ExchangeFileTooLargeError:
        ceiling = self._limit - BODY_ENVELOPE_MARGIN
        return ExchangeFileTooLargeError(f"an exchange file is at most {ceiling} bytes")

    @staticmethod
    async def _admit(app: FastAPI, authorization: bytes | None) -> None:
        """Verify the bearer token and require the editor role; raise the typed refusal."""
        scheme, token = get_authorization_scheme_param(
            authorization.decode("latin-1") if authorization else None
        )
        caller = await caller_of(app, token if scheme.lower() == "bearer" and token else None)
        if not caller.can_write:
            msg = f"changing the catalogue needs the {EDITOR_ROLE} role"
            raise NotAuthorisedError(msg)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # `endswith`, not `==`: behind the NGINX prefix, `path` may carry `/api`.
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or not scope["path"].endswith(self._suffix)
        ):
            await self._app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        try:
            await self._admit(scope["app"], headers.get(b"authorization"))
        except DomainError as refusal:  # 401, or 403: answered without touching the body
            await domain_error_response(refusal)(scope, receive, send)
            return
        declared = headers.get(b"content-length", b"0")
        if declared.isdigit() and int(declared) > self._limit:
            await domain_error_response(self._too_large())(scope, receive, send)
            return
        ticket = None
        if self._admission is not None:
            try:
                ticket = self._admission.admit()  # a place before the first byte is read
            except DomainError as refusal:  # 409: the body is never touched
                await domain_error_response(refusal)(scope, receive, send)
                return
        marked = current_ticket.set(ticket)
        try:
            await self._receive_and_serve(scope, receive, send)
        finally:
            current_ticket.reset(marked)
            if ticket is not None:
                ticket.release()

    async def _receive_and_serve(self, scope: Scope, receive: Receive, send: Send) -> None:
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
