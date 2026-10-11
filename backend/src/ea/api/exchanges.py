"""Import and export of the model as exchange files — see docs/adr/0050.

The export is a download streamed from one database snapshot; the file name
is made by the service from fixed words and the date, never from anything the
caller sent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from starlette.types import Send

from ea.api.dependencies import Exchanges
from ea.api.files import attachment
from ea.api.schemas import ErrorResponse, ImportReportRead
from ea.domain.errors import InvalidExchangeFileError, UnknownScopeError
from ea.domain.exchange import (
    ConflictPolicy,
    ExchangeFormat,
    ExportRequest,
    ExportScope,
    ImportContent,
    ImportOptions,
    ScopeKind,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/exchanges", tags=["exchanges"])

REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}


class BoundedDownload(StreamingResponse):
    """A download that a client must take within a delay, and that always gives its file back.

    The delay covers the whole sending, so a client that stops reading is cut when it runs out
    (the connection is closed, the response left incomplete), and one that reads slowly but
    steadily is not. However it ends — complete, cut, cancelled by a disconnect — the body is
    closed: the temporary file is deleted and the place in the queue of downloads is freed.
    """

    def __init__(self, *args: Any, timeout: float, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._timeout = timeout

    async def stream_response(self, send: Send) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                await super().stream_response(send)
        except TimeoutError:
            logger.warning("a download was cut: the client did not take it in time")
        finally:
            await self.body_iterator.aclose()  # type: ignore[attr-defined]


def _scope_of(name: str) -> ScopeKind:
    """A typed refusal (`unknown_scope`) rather than the generic validation report."""
    try:
        return ScopeKind(name)
    except ValueError:
        known = ", ".join(kind.value for kind in ScopeKind)
        msg = f"unknown export scope; expected one of: {known}"
        raise UnknownScopeError(msg) from None


@router.get(
    "/export",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {
                "application/xml": {"schema": {"type": "string", "format": "binary"}},
                "application/zip": {"schema": {"type": "string", "format": "binary"}},
            },
            "headers": {
                "X-Export-Skipped-Views": {
                    "description": "Views left out: a box shows an element outside the scope.",
                    "schema": {"type": "integer"},
                },
                "X-Export-Cells": {
                    "description": "CSV only: `safe` (formulas neutralised) or `raw`.",
                    "schema": {"type": "string"},
                },
            },
            "description": (
                "The model as an attachment: an ArchiMate exchange file (XML), or a zip of "
                "`elements.csv`, `relations.csv` and `properties.csv` (CSV)."
            ),
        },
        **REJECTED,
    },
)
async def export_model(
    exchanges: Exchanges,
    request: Request,
    export_format: Annotated[ExchangeFormat, Query(alias="format")] = ExchangeFormat.ARCHIMATE_XML,
    scope: Annotated[
        str,
        Query(description="What to export: `model` (the whole catalogue), `package` or `query`."),
    ] = (ScopeKind.MODEL.value),
    package_id: Annotated[
        UUID | None, Query(description="`scope=package`: the package to export.")
    ] = None,
    query_id: Annotated[
        UUID | None, Query(description="`scope=query`: the saved query whose elements to export.")
    ] = None,
    include_subpackages: Annotated[
        bool, Query(description="`scope=package`: the sub-packages come too.")
    ] = True,
    with_views: Annotated[bool, Query()] = True,
    safe_cells: Annotated[
        bool,
        Query(
            description=(
                "CSV only. Prefix with `'` a cell a spreadsheet would run as a formula "
                "(the default). Turning it off writes the values as they are."
            )
        ),
    ] = True,
) -> StreamingResponse:
    """Download the model: ArchiMate Exchange XML, or Archi-style CSV in a zip (a reader may).

    The file is prepared before the first byte is sent, and no database connection is held while
    a client reads it. A client has `EA_EXCHANGE_SEND_TIMEOUT_S` to take it; at most
    `EA_EXCHANGE_MAX_EXPORTS` are prepared or sent at once (429 and `Retry-After` beyond).
    """
    export = await exchanges.export(
        ExportRequest(
            format=export_format,
            scope=ExportScope(
                kind=_scope_of(scope),
                package_id=package_id,
                query_id=query_id,
                include_subpackages=include_subpackages,
            ),
            with_views=with_views,
            safe_cells=safe_cells,
        )
    )
    return BoundedDownload(
        export.chunks,
        timeout=request.app.state.settings.exchange_send_timeout_s,
        media_type=export.media_type,
        headers={
            "Content-Length": str(export.size),
            "Content-Disposition": attachment(export.filename),
            "X-Content-Type-Options": "nosniff",
            "X-Export-Skipped-Views": str(export.skipped_views),
            **({"X-Export-Cells": export.cells} if export.cells else {}),
        },
    )


IMPORT_ERRORS: dict[int | str, dict[str, type[ErrorResponse]]] = {
    **REJECTED,
    status.HTTP_408_REQUEST_TIMEOUT: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_413_CONTENT_TOO_LARGE: {"model": ErrorResponse},
}


#: The parts every import's form may carry, and those of each format. Anything else is
#: refused, not ignored: an unknown field is a client that believes it asked for something
#: the server will not do, and a file of the other format is a client that sent the wrong one.
SHARED_FIELDS = frozenset({"format", "dry_run", "on_conflict", "strict", "target_package_id"})
XML_FIELDS = frozenset({"file", "with_views"})
CSV_FIELDS = frozenset({"elements", "relations", "properties", "safe_cells"})
IMPORT_FIELDS = SHARED_FIELDS | XML_FIELDS | CSV_FIELDS


async def only_the_declared_parts(request: Request) -> None:
    """Refuse a form with a part nobody declared, one of the other format, or one sent twice.

    FastAPI has already parsed the form for the parameters below; this reads it back from the
    request's cache. Names are not echoed: they come from the caller and mean nothing here.
    """
    form = await request.form()
    chosen = form.get("format")
    own = CSV_FIELDS if chosen == ExchangeFormat.CSV.value else XML_FIELDS
    unknown = set(form.keys()) - SHARED_FIELDS - own
    repeated = [name for name in IMPORT_FIELDS if len(form.getlist(name)) > 1]
    if unknown or repeated:
        raise RequestValidationError(
            [{"type": "extra_forbidden", "loc": ("body",), "msg": "unexpected or repeated part"}]
        )


@router.post(
    "/imports",
    response_model=ImportReportRead,
    responses=IMPORT_ERRORS,
    dependencies=[Depends(only_the_declared_parts)],
)
async def import_model(
    exchanges: Exchanges,
    file: Annotated[
        UploadFile | None, File(description="An ArchiMate exchange file (XML); `archimate-xml`.")
    ] = None,
    elements: Annotated[
        UploadFile | None, File(description="`elements.csv`; required for `csv`.")
    ] = None,
    relations: Annotated[UploadFile | None, File(description="`relations.csv` (`csv`).")] = None,
    properties: Annotated[UploadFile | None, File(description="`properties.csv` (`csv`).")] = None,
    import_format: Annotated[ExchangeFormat, Form(alias="format")] = ExchangeFormat.ARCHIMATE_XML,
    dry_run: Annotated[
        bool, Form(description="Simulate and roll back. Writing needs `false`, said outright.")
    ] = True,
    on_conflict: Annotated[ConflictPolicy, Form()] = ConflictPolicy.REUSE,
    strict: Annotated[
        bool, Form(description="If any row is refused, write nothing at all.")
    ] = False,
    target_package_id: Annotated[
        UUID | None,
        Form(description="Where new packages and new elements outside any folder are put."),
    ] = None,
    with_views: Annotated[
        bool, Form(description="XML only. Recreate the views of the file (their boxes).")
    ] = True,
    safe_cells: Annotated[
        bool,
        Form(description="CSV only. Give back a cell the export had prefixed with `'`."),
    ] = True,
) -> ImportReportRead:
    """Simulate (the default) or apply an exchange file; editors only, simulation included.

    Writing needs `dry_run=false`, said outright: a client that forgets destroys nothing.
    To confirm, send the same file(s) again with `dry_run=false`; the report's `sha256` says
    it is what was simulated. The import is idempotent (an existing element or link is
    reused), so a client whose connection was cut can send it again. XML takes `file`; CSV
    takes `elements` and, optionally, `relations` and `properties` — three files, never an
    archive. The ceiling is on their sum. Neither a file's name nor its declared type is read.
    """
    if import_format is ExchangeFormat.CSV:
        if elements is None:
            msg = "a CSV import needs the `elements` file"
            raise InvalidExchangeFileError(msg)
        main, companion, props = await _read_parts(
            exchanges.max_bytes, elements, relations, properties
        )
        content = ImportContent(import_format, main or b"", companion, props)
    else:
        if file is None:
            msg = "an XML import needs the `file` part"
            raise InvalidExchangeFileError(msg)
        (raw,) = await _read_parts(exchanges.max_bytes, file)
        content = ImportContent(import_format, raw or b"")
    report = await exchanges.import_exchange(
        content,
        ImportOptions(
            dry_run=dry_run,
            on_conflict=on_conflict,
            strict=strict,
            safe_cells=safe_cells,
            with_views=with_views,
            target_package_id=target_package_id,
        ),
    )
    return ImportReportRead.of(report)


async def _read_parts(ceiling: int, *parts: UploadFile | None) -> list[bytes | None]:
    """The bytes of each part present, never more than `ceiling + 1` of them all together.

    The ceiling is on the sum, not on each file: three files of 20 MB would otherwise be
    60 MB held at once. One byte past the ceiling is enough for the service to refuse.
    """
    remaining = ceiling + 1
    read: list[bytes | None] = []
    for part in parts:
        data = None if part is None else await part.read(remaining)
        remaining -= len(data or b"")
        read.append(data)
    return read
