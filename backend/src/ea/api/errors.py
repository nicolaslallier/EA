"""Turning domain errors into the API's single error envelope.

Which failure maps to which status is a decision, so it lives in one table
rather than being spread over `raise HTTPException` calls in the routers. The
message of a `DomainError` is written for a human and names no internals; a
failure with any other cause never reaches the client at all.

The envelope for *that* last case is sent by a middleware here rather than by
the handler Starlette installs for `Exception`, because the handler's answer is
sent from outside every middleware this app adds — see
`AnsweringUnexpectedFailures`.
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import TYPE_CHECKING, Any, Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from ea.domain.errors import (
    AddressAlreadyAssignedError,
    AddressNotAssignedError,
    AddressOutsideAnyNetworkError,
    AnalysisBusyError,
    ApprovalStaleError,
    BaselineNotFoundError,
    BaselineTooLargeError,
    CyclicContainmentError,
    CyclicPackageError,
    DiagramNotFoundError,
    DocumentNotFoundError,
    DomainError,
    DuplicateBaselineError,
    DuplicateDiagramError,
    DuplicateDocumentError,
    DuplicateElementError,
    DuplicateNetworkError,
    DuplicatePackageError,
    DuplicateSavedQueryError,
    ElementLockedError,
    ElementNotFoundError,
    ExchangeBodyTimeoutError,
    ExchangeFileTooLargeError,
    ExportInProgressError,
    ExportTooLargeError,
    FileNotTextError,
    FileStorageUnavailableError,
    FileTooLargeError,
    IllegalRelationshipError,
    ImportInProgressError,
    InvalidCursorError,
    InvalidExchangeFileError,
    InvalidFileKeyError,
    InvalidFileMetadataError,
    InvalidQueryError,
    InvalidTransitionError,
    MatrixAxisTooLargeError,
    NetworkExhaustedError,
    NotAddressableError,
    NotASubnetError,
    NotAuthenticatedError,
    NotAuthorisedError,
    PackageNotEmptyError,
    PackageNotFoundError,
    PackageTooDeepError,
    RestoreBlockedError,
    RestoreConflictError,
    RowRefusedError,
    SavedQueryNotFoundError,
    ScopeTooLargeError,
    SearchUnavailableError,
    StoredFileExistsError,
    StoredFileNotFoundError,
    TooManyPackagesError,
    TooManyRefusedRowsError,
    UnknownLayoutElementError,
    UnknownPackageError,
    UnknownScopeError,
    WriteConflictError,
)

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

#: Domain failure -> (HTTP status, stable machine-readable code).
_STATUS: Final[dict[type[Exception], tuple[int, str]]] = {
    ElementNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DocumentNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateElementError: (status.HTTP_409_CONFLICT, "duplicate"),
    DuplicateDocumentError: (status.HTTP_409_CONFLICT, "duplicate"),
    CyclicContainmentError: (status.HTTP_409_CONFLICT, "cyclic_containment"),
    # --- Saved diagrams (docs/adr/0031) ---
    DiagramNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateDiagramError: (status.HTTP_409_CONFLICT, "duplicate"),
    # --- Packages (the classification tree) ---
    # `package_not_empty` and `cyclic_package` are the two a caller acts on:
    # the first says to pass `move_contents_to`, the second to pick a target
    # outside the subtree.
    PackageNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicatePackageError: (status.HTTP_409_CONFLICT, "duplicate"),
    CyclicPackageError: (status.HTTP_409_CONFLICT, "cyclic_package"),
    PackageNotEmptyError: (status.HTTP_409_CONFLICT, "package_not_empty"),
    PackageTooDeepError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "package_too_deep"),
    TooManyPackagesError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_packages"),
    UnknownPackageError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_package"),
    # --- The audit trail (docs/adr/0046) ---
    # A cursor travels in a URL and anyone can edit one: a refusal, never a 500.
    InvalidCursorError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_cursor"),
    # --- Baselines (docs/adr/0047) ---
    # `restore_conflict` is the one a caller retries: the model moved under the
    # plan, or a name or an address was taken meanwhile, and nothing was written.
    # `restore_blocked` is the one it must not: an element is locked.
    BaselineNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateBaselineError: (status.HTTP_409_CONFLICT, "duplicate"),
    BaselineTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "baseline_too_large"),
    # --- Review and locking (docs/adr/0048) ---
    ApprovalStaleError: (status.HTTP_409_CONFLICT, "approval_stale"),
    WriteConflictError: (status.HTTP_409_CONFLICT, "write_conflict"),
    ElementLockedError: (status.HTTP_409_CONFLICT, "element_locked"),
    InvalidTransitionError: (status.HTTP_409_CONFLICT, "invalid_transition"),
    RestoreBlockedError: (status.HTTP_409_CONFLICT, "restore_blocked"),
    RestoreConflictError: (status.HTTP_409_CONFLICT, "restore_conflict"),
    # --- Structured search and saved queries ---
    InvalidQueryError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_query"),
    SavedQueryNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateSavedQueryError: (status.HTTP_409_CONFLICT, "duplicate"),
    UnknownLayoutElementError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_element"),
    # A deployment with `EA_EMBEDDINGS_ENABLED` off, which is a configuration
    # and not a request that was wrong — hence 503 and not 4xx.
    SearchUnavailableError: (status.HTTP_503_SERVICE_UNAVAILABLE, "search_unavailable"),
    IllegalRelationshipError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "illegal_relationship",
    ),
    # --- IP address management (docs/adr/0020) ---
    # Each refusal gets its own code rather than a shared 409, because these
    # are the ones a caller is expected to *act* on: an address already taken
    # means pick another, a subnet exhausted means declare a bigger one, and an
    # address outside every subnet means declare the subnet first. An agent
    # told only "conflict" would retry the call that just failed.
    AddressNotAssignedError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateNetworkError: (status.HTTP_409_CONFLICT, "duplicate"),
    AddressAlreadyAssignedError: (status.HTTP_409_CONFLICT, "address_taken"),
    NetworkExhaustedError: (status.HTTP_409_CONFLICT, "network_exhausted"),
    NotAddressableError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "not_addressable"),
    NotASubnetError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "not_a_subnet"),
    AddressOutsideAnyNetworkError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "address_outside_any_network",
    ),
    # --- Files in MinIO (docs/adr/0036) ---
    StoredFileNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    StoredFileExistsError: (status.HTTP_409_CONFLICT, "duplicate"),
    InvalidFileKeyError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_file_path"),
    # What a person wrote about a file, refused for a reason no schema can
    # express — a control character in a tag. The bounds themselves are
    # `api/schemas.py`'s and are refused before reaching the domain.
    InvalidFileMetadataError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "invalid_file_metadata",
    ),
    FileTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "file_too_large"),
    FileNotTextError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "not_text"),
    FileStorageUnavailableError: (status.HTTP_503_SERVICE_UNAVAILABLE, "storage_unavailable"),
    # --- Exchanges (docs/adr/0050) ---
    InvalidExchangeFileError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_exchange_file"),
    # 413 literally: the bucket's `FileTooLargeError` above is a 422 and a different class.
    ExchangeFileTooLargeError: (413, "file_too_large"),
    ExchangeBodyTimeoutError: (status.HTTP_408_REQUEST_TIMEOUT, "body_timeout"),
    ImportInProgressError: (status.HTTP_409_CONFLICT, "import_in_progress"),
    ExportInProgressError: (status.HTTP_429_TOO_MANY_REQUESTS, "export_in_progress"),
    ExportTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "export_too_large"),
    TooManyRefusedRowsError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "too_many_refused_rows"),
    UnknownScopeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "unknown_scope"),
    # Never sent: a refused row is a line of the import report. The row exists
    # because every `DomainError` needs one (tests/unit/test_error_statuses.py).
    RowRefusedError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_row"),
    # --- Model analysis (docs/adr/0056) ---
    MatrixAxisTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "matrix_axis_too_large"),
    ScopeTooLargeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "scope_too_large"),
    AnalysisBusyError: (status.HTTP_429_TOO_MANY_REQUESTS, "analysis_busy"),
    # --- Authentication (docs/adr/0032) ---
    NotAuthenticatedError: (status.HTTP_401_UNAUTHORIZED, "unauthenticated"),
    NotAuthorisedError: (status.HTTP_403_FORBIDDEN, "forbidden"),
}

_FALLBACK: Final = (status.HTTP_400_BAD_REQUEST, "invalid_request")


async def _handle_domain_error(_: Request, error: Exception) -> JSONResponse:
    return domain_error_response(error)


def domain_error_response(error: Exception) -> JSONResponse:
    """The envelope of a domain failure: for a handler, and for a middleware that answers alone."""
    http_status, code = _STATUS.get(type(error), _FALLBACK)
    headers = (
        {"WWW-Authenticate": "Bearer"} if http_status == status.HTTP_401_UNAUTHORIZED else None
    )
    if http_status == status.HTTP_429_TOO_MANY_REQUESTS or isinstance(error, ImportInProgressError):
        headers = {"Retry-After": str(RETRY_AFTER_SECONDS)}
    return JSONResponse(
        status_code=http_status, content={"error": code, "detail": str(error)}, headers=headers
    )


async def _handle_value_error(_: Request, error: Exception) -> JSONResponse:
    """A `ValueError` from the domain is a rejected input, not a server fault."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"error": "invalid_input", "detail": str(error)},
    )


def _internal_error() -> JSONResponse:
    """The one answer an unanticipated failure ever gets: typed, and mute.

    Written once and sent from three places — the middleware below, the
    `ValidationError` handler, and the `Exception` handler Starlette installs —
    because a client must not be able to tell from the body which of the three
    answered.
    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal_error", "detail": "The server failed to answer this request."},
    )


async def _handle_validation_error(_: Request, error: Exception) -> JSONResponse:
    """A `ValidationError` is a `ValueError`, and says far more than a client may read.

    Without this it would land on `_handle_value_error` above — because pydantic
    derives its error from `ValueError` — and be answered with `str(error)`,
    which names the model, the field and the value pydantic refused:

        1 validation error for ElementRead
        properties
          Input should be a valid dictionary [type=dict_type, input_value=None, ...]

    That is the internal shape of the server, handed to whoever on the LAN made
    the request fail (docs/adr/0016). A request body that does not validate is a
    different thing entirely: FastAPI raises `RequestValidationError`, which is
    not a `ValueError` and keeps its own 422 with the field report.

    Starlette picks a handler by walking `type(error).__mro__`, so this one wins
    over the `ValueError` entry whatever order they are registered in.
    """
    logger.exception("a model was built from data that does not validate", exc_info=error)
    return _internal_error()


async def _handle_unexpected_error(_: Request, error: Exception) -> JSONResponse:
    """The last-resort net, for a failure raised outside the middleware below.

    Registered for `Exception`, which Starlette installs in its *outermost*
    middleware — outside the CORS headers and outside the request id. That is
    why it is no longer the normal path: see `AnsweringUnexpectedFailures`.
    Anything still reaching here was raised by one of those two middlewares
    themselves, and Starlette re-raises it after this answer, so the traceback
    reaches the ASGI server's log.
    """
    return _internal_error()


#: What a client refused for lack of a place is told to wait before asking again.
RETRY_AFTER_SECONDS: Final = 5
_DRIVERS = ("sqlalchemy", "asyncpg", "psycopg")
#: The only things a database failure may say to a log: where and which rule, never with what.
_STRUCTURE = ("sqlstate", "constraint_name", "table_name", "column_name", "schema_name")
#: Exceptions followed through causes, contexts and groups before the chain is distrusted.
_CHAIN_LIMIT = 20
#: The doors an import comes through: a failure there is logged without its message.
IMPORTING_PATHS = ("/exchanges/imports", "/mcp")
ExcInfo = tuple[type[BaseException], BaseException, TracebackType | None]


def _chain(error: BaseException) -> tuple[list[BaseException], bool]:
    """The exception, then what it was raised from, in what context, or grouped with — breadth
    first, bounded; the flag says the bound was reached and the rest is unknown."""
    seen: list[BaseException] = []
    queue = [error]
    while queue:
        link = queue.pop(0)
        if any(link is known for known in seen):
            continue
        if len(seen) >= _CHAIN_LIMIT:
            return seen, True
        seen.append(link)
        queue.extend(
            e
            for e in (link.__cause__, link.__context__, getattr(link, "orig", None))
            if isinstance(e, BaseException)
        )
        queue.extend(getattr(link, "exceptions", ()))
    return seen, False


def _structure_of(database: BaseException) -> dict[str, object]:
    """Only the whitelisted structural fields, from the exception or the driver's under it."""
    orig = getattr(database, "orig", None)
    holders = [database, orig, getattr(orig, "__cause__", None), database.__cause__]
    found: dict[str, object] = {"db_error": type(database).__name__}
    for name in _STRUCTURE:
        for holder in holders:
            value = getattr(holder, name, None)
            if isinstance(value, str) and value:
                found[name] = value
                break
    return found


def _stand_in(message: str, error: BaseException) -> ExcInfo:
    """A fresh exception with our words and the real one's frames (source lines, not values)."""
    stand_in = RuntimeError(message)
    stand_in.__suppress_context__ = True
    return RuntimeError, stand_in, error.__traceback__


def safe_failure(error: BaseException, *, importing: bool) -> tuple[ExcInfo, dict[str, object]]:
    """What may be logged of a failure nobody handled: `(exc_info, extra fields)`.

    Fails closed. A failure with the database anywhere in its chain (a cause, a context, a member
    of a group) is logged as its structure alone, the traceback's frames and a fixed sentence:
    never its message, its statement, its parameters or the text of anything it was raised from.
    A failure of any other kind keeps its message outside an import and loses it during one
    (what is not recognised is replaced, not matched). A chain too long to follow is not trusted.
    If this function itself fails, one fixed sentence and the class are what remain.
    """
    try:
        chain, truncated = _chain(error)
        database = next((e for e in chain if type(e).__module__.startswith(_DRIVERS)), None)
        if database is not None:
            fields = _structure_of(database)
            outer = fields["db_error"]
            return _stand_in(
                f"{outer}: the database refused a statement (detail withheld)", error
            ), fields
        if importing or truncated:
            name = type(error).__name__
            return _stand_in(f"{name}: failure during an import (message withheld)", error), {
                "error": name
            }
        return (type(error), error, error.__traceback__), {}
    except Exception:
        fields = {"db_error": type(error).__name__}
        return _stand_in("unsanitisable database error", error), fields


class AnsweringUnexpectedFailures:
    """The `internal_error` envelope, sent from *inside* the middleware stack.

    Starlette answers an unhandled exception from `ServerErrorMiddleware`, which
    wraps everything this app adds. Its response therefore never passes through
    `CORSMiddleware` or `RequestLogging`, so a 500 used to arrive with neither
    `Access-Control-Allow-Origin` nor `X-Request-Id`. A browser then refused to
    let the SPA read it at all: `fetch` rejected, and `lib/api.ts` reported
    "Backend injoignable" for an API that was running and had just answered —
    while the one answer that most needs joining to a log line carried no id to
    join it by (docs/adr/0021).

    So the envelope is sent here instead, as the innermost middleware: CORS
    wraps it, `RequestLogging` wraps that and stamps the id, and the answer
    looks like every other one.

    Two things it deliberately does not do. It does not re-raise, because the
    traceback is logged here — with the request id on it, which the ASGI
    server's own log has no way to know. And it does not answer once the
    response has started: a download that fails halfway has already sent its
    status line, and there is nothing left to replace it with.

    `except Exception` and not `BaseException`: a client that hangs up cancels
    the task, and `CancelledError` is not a fault to report.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        started = False

        async def sending(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self._app(scope, receive, sending)
        except Exception as error:
            if started:
                raise
            fields: dict[str, Any] = {
                "action": "unhandled",
                "method": scope.get("method", ""),
                "path": scope.get("path", ""),
            }
            path = str(scope.get("path", ""))
            info, said = safe_failure(error, importing=path.endswith(IMPORTING_PATHS))
            logger.exception(
                "the request failed with no handler for it", exc_info=info, extra={**fields, **said}
            )
            await _internal_error()(scope, receive, send)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(ValidationError, _handle_validation_error)
    app.add_exception_handler(ValueError, _handle_value_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
