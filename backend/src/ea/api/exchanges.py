"""Import and export of the model as exchange files — see docs/adr/0048.

The export is a download streamed from one database snapshot; the file name
is made by the service from fixed words and the date, never from anything the
caller sent.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from ea.api.dependencies import Exchanges
from ea.api.files import attachment
from ea.api.schemas import ErrorResponse
from ea.domain.errors import UnknownScopeError
from ea.domain.exchange import ExchangeFormat, ExportRequest, ExportScope, ScopeKind

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
