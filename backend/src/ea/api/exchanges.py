"""Import and export of the model as exchange files — see docs/adr/0048.

The export is a download streamed from one database snapshot; the file name
is made by the service from fixed words and the date, never from anything the
caller sent.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse

from ea.api.dependencies import Exchanges
from ea.api.files import attachment
from ea.api.schemas import ErrorResponse, ImportReportRead
from ea.domain.errors import UnknownScopeError
from ea.domain.exchange import (
    ConflictPolicy,
    ExchangeFormat,
    ExportRequest,
    ExportScope,
    ImportContent,
    ImportOptions,
    ScopeKind,
)

router = APIRouter(prefix="/exchanges", tags=["exchanges"])

REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}


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
            },
            "description": "The model as an ArchiMate exchange file, as an attachment.",
        },
        **REJECTED,
    },
)
async def export_model(
    exchanges: Exchanges,
    export_format: Annotated[ExchangeFormat, Query(alias="format")] = ExchangeFormat.ARCHIMATE_XML,
    scope: Annotated[str, Query(description="What to export: `model` (the whole catalogue).")] = (
        ScopeKind.MODEL.value
    ),
    with_views: Annotated[bool, Query()] = True,
) -> StreamingResponse:
    """Download the model as an ArchiMate Exchange XML file (a reader may)."""
    export = await exchanges.export(
        ExportRequest(
            format=export_format,
            scope=ExportScope(kind=_scope_of(scope)),
            with_views=with_views,
        )
    )
    return StreamingResponse(
        export.chunks,
        media_type=export.media_type,
        headers={
            "Content-Disposition": attachment(export.filename),
            "X-Content-Type-Options": "nosniff",
        },
    )


IMPORT_ERRORS: dict[int | str, dict[str, type[ErrorResponse]]] = {
    **REJECTED,
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_413_CONTENT_TOO_LARGE: {"model": ErrorResponse},
}


#: Every part an import's form may carry. Anything else is refused, not ignored: an unknown
#: field is a client that believes it asked for something the server will not do.
IMPORT_FIELDS = frozenset({"file", "format", "dry_run", "on_conflict", "strict"})


async def only_the_declared_parts(request: Request) -> None:
    """Refuse a form with a part nobody declared, or one declared twice (two files, say).

    FastAPI has already parsed the form for the parameters below; this reads it back from the
    request's cache. Names are not echoed: they come from the caller and mean nothing here.
    """
    form = await request.form()
    unknown = set(form.keys()) - IMPORT_FIELDS
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
    file: Annotated[UploadFile, File(description="An ArchiMate exchange file (XML).")],
    import_format: Annotated[ExchangeFormat, Form(alias="format")] = ExchangeFormat.ARCHIMATE_XML,
    dry_run: Annotated[
        bool, Form(description="Simulate and roll back. Writing needs `false`, said outright.")
    ] = True,
    on_conflict: Annotated[ConflictPolicy, Form()] = ConflictPolicy.REUSE,
    strict: Annotated[
        bool, Form(description="If any row is refused, write nothing at all.")
    ] = False,
) -> ImportReportRead:
    """Simulate (the default) or apply an exchange file; editors only, simulation included.

    Writing needs `dry_run=false`, said outright: a client that forgets destroys nothing.
    To confirm, send the same file again with `dry_run=false`; the report's `sha256` says it
    is the file that was simulated. The import is idempotent (an existing element or link is
    reused), so a client whose connection was cut can send it again.
    Neither the file's name nor its declared type is read.
    """
    raw = await file.read(exchanges.max_bytes + 1)
    report = await exchanges.import_exchange(
        ImportContent(import_format, raw),
        ImportOptions(dry_run=dry_run, on_conflict=on_conflict, strict=strict),
    )
    return ImportReportRead.of(report)
