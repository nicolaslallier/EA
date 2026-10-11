"""The report endpoints (docs/adr/0062).

`GET /reports/render` writes nothing, so any caller can have a PDF without touching the
bucket; `POST /reports` deposits one under `reports/` and is for editors, a rule the service
enforces and the router never restates. A download is always an attachment, named by the
server (never by the client), with `nosniff` and a closed policy: the report is made of
people's words, and an HTML file served inline on this origin would run with the session of
whoever opened it.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Reports
from ea.api.files import attachment
from ea.api.schemas import (
    ErrorResponse,
    FileRead,
    ReportCreate,
    ReportTemplateId,
    ReportTemplateRead,
)
from ea.domain.reports import (
    CONTENT_TYPES,
    ReportFormat,
    ReportRequest,
    ReportSubject,
    SubjectKind,
)

router = APIRouter(tags=["reports"])

_ERROR = {"model": ErrorResponse}
REFUSED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
    status.HTTP_429_TOO_MANY_REQUESTS: _ERROR,
    status.HTTP_501_NOT_IMPLEMENTED: _ERROR,
}
_DOWNLOAD: dict[int | str, dict[str, Any]] = {
    status.HTTP_200_OK: {
        "description": "The report, as an attachment.",
        "content": {
            content_type: {"schema": {"type": "string", "format": "binary"}}
            for content_type in CONTENT_TYPES.values()
        },
    },
    **REFUSED,
}


@router.get("/report-templates", response_model=list[ReportTemplateRead])
async def list_report_templates(reports: Reports) -> list[ReportTemplateRead]:
    """The built-in templates, with the subjects they take and the formats served."""
    return [
        ReportTemplateRead.of(template, reports.formats)
        for template in await reports.list_templates()
    ]


@router.get("/reports/render", response_class=Response, responses=_DOWNLOAD)
async def render_report(
    reports: Reports,
    template: Annotated[ReportTemplateId, Query()],
    subject_kind: Annotated[SubjectKind, Query()],
    subject_id: Annotated[UUID, Query()],
    report_format: Annotated[ReportFormat, Query(alias="format")],
) -> Response:
    """Build a report and return it. Nothing is stored."""
    rendered = await reports.build(
        ReportRequest(template, ReportSubject(subject_kind, subject_id), report_format)
    )
    return Response(
        content=rendered.content,
        media_type=rendered.content_type,
        headers={
            "Content-Disposition": attachment(rendered.filename),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.post(
    "/reports", response_model=FileRead, status_code=status.HTTP_201_CREATED, responses=REFUSED
)
async def create_report(body: ReportCreate, reports: Reports) -> FileRead:
    """Build a report and deposit it in the bucket under `reports/`. Editors only."""
    return FileRead.of(await reports.deposit(body.request()))
