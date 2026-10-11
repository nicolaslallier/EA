"""The endpoints of the references from an element to objects in other tools (docs/adr/0063).

A caller names a *target* the deployment configured; it never gives an address to call.
Reading serves the cache and makes no outbound call; creating, refreshing and opening a ticket
do, through the service, and are for editors.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import ExternalReferences
from ea.api.schemas import (
    ErrorResponse,
    ExternalIssueOpen,
    ExternalIssueRead,
    ExternalReferenceCreate,
    ExternalReferenceRead,
    ExternalTargetRead,
    StaleLimit,
)
from ea.domain.external import MAX_STALE_PAGE

router = APIRouter(tags=["external"])

_ERROR = {"model": ErrorResponse}
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
    status.HTTP_429_TOO_MANY_REQUESTS: _ERROR,
    status.HTTP_502_BAD_GATEWAY: _ERROR,
    status.HTTP_503_SERVICE_UNAVAILABLE: _ERROR,
    status.HTTP_504_GATEWAY_TIMEOUT: _ERROR,
}


@router.get("/external/targets", response_model=list[ExternalTargetRead])
async def list_targets(external: ExternalReferences) -> list[ExternalTargetRead]:
    """The targets the deployment declared, by name and kind. Never an address or a secret."""
    return [ExternalTargetRead.of(target) for target in await external.targets()]


@router.get(
    "/elements/{element_id}/external-references",
    response_model=list[ExternalReferenceRead],
    responses=NOT_FOUND,
)
async def list_references(
    element_id: UUID, external: ExternalReferences
) -> list[ExternalReferenceRead]:
    """The cached title and state of what an element cites. Makes no outbound call."""
    return [ExternalReferenceRead.of(v) for v in await external.list_for_element(element_id)]


@router.post(
    "/elements/{element_id}/external-references",
    response_model=ExternalReferenceRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def link_reference(
    element_id: UUID, body: ExternalReferenceCreate, external: ExternalReferences
) -> ExternalReferenceRead:
    """Cite an object. The provider confirms it exists first; if it is down, nothing is stored."""
    view = await external.link(
        element_id, kind=body.kind, reference=body.reference, target=body.target
    )
    return ExternalReferenceRead.of(view)


@router.post(
    "/elements/{element_id}/external-references/refresh",
    response_model=list[ExternalReferenceRead],
    responses=REJECTED,
)
async def refresh_element_references(
    element_id: UUID, external: ExternalReferences
) -> list[ExternalReferenceRead]:
    """Refresh up to 20 references of an element, oldest first; a provider down leaves stale."""
    return [ExternalReferenceRead.of(v) for v in await external.refresh_element(element_id)]


@router.post(
    "/external-references/{reference_id}/refresh",
    response_model=ExternalReferenceRead,
    responses=REJECTED,
)
async def refresh_reference(
    reference_id: UUID, external: ExternalReferences
) -> ExternalReferenceRead:
    """Ask the provider again; if it is down: 200, the line unchanged and `stale: true`."""
    return ExternalReferenceRead.of(await external.refresh(reference_id))


@router.delete(
    "/external-references/{reference_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def unlink_reference(reference_id: UUID, external: ExternalReferences) -> Response:
    """Remove the link — never the object it points at."""
    await external.unlink(reference_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/external-references/stale", response_model=list[ExternalReferenceRead])
async def list_stale_references(
    external: ExternalReferences,
    limit: Annotated[StaleLimit, Query()] = MAX_STALE_PAGE,
) -> list[ExternalReferenceRead]:
    """References whose cache is older than 24 hours, oldest first — what the refresh flow walks."""
    return [ExternalReferenceRead.of(v) for v in await external.list_stale(limit=limit)]


@router.post(
    "/elements/{element_id}/external-issues",
    response_model=ExternalIssueRead,
    responses={
        **REJECTED,
        status.HTTP_201_CREATED: {"model": ExternalIssueRead},
    },
)
async def open_issue(
    element_id: UUID, body: ExternalIssueOpen, external: ExternalReferences, response: Response
) -> ExternalIssueRead:
    """Open a ticket at a writable target and cite it. A dry run (the default) sends nothing.

    With `dry_run: false` the ticket is opened once — never retried — and linked; a 502 or 504
    means check the provider before trying again.
    """
    plan = await external.open_issue(
        element_id,
        target=body.target,
        scope=body.scope,
        title=body.title,
        body=body.body,
        dry_run=body.dry_run,
    )
    if not plan.dry_run:
        response.status_code = status.HTTP_201_CREATED
    return ExternalIssueRead.of(plan)
