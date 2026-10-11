"""Baselines over HTTP: capture, compare, restore (docs/adr/0047).

Reads need a caller; capturing, deleting and restoring need an editor — which
`BaselineService` decides, not this module. A restoration is a dry run unless
told otherwise, and applying it means handing back the digest of the plan that
was read.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Baselines
from ea.api.schemas import (
    Against,
    BaselineCreate,
    BaselineDiffRead,
    BaselineRead,
    ErrorResponse,
    RestoreRead,
    RestoreRequest,
)

router = APIRouter(tags=["baselines"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}


def against_of(raw: str | None) -> UUID | None:
    """`current` and nothing mean the live model; anything else must be a baseline id."""
    return None if raw in (None, "", "current") else UUID(raw)  # ValueError -> 422 invalid_input


@router.get("/baselines", response_model=list[BaselineRead])
async def list_baselines(service: Baselines) -> list[BaselineRead]:
    return [BaselineRead.of(b) for b in await service.list_baselines()]


@router.post(
    "/baselines",
    response_model=BaselineRead,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": ErrorResponse}},
)
async def create_baseline(payload: BaselineCreate, service: Baselines) -> BaselineRead:
    """Freeze a copy of the whole model under a name."""
    return BaselineRead.of(await service.create(name=payload.name, description=payload.description))


@router.get("/baselines/{baseline_id}", response_model=BaselineRead, responses=NOT_FOUND)
async def read_baseline(baseline_id: UUID, service: Baselines) -> BaselineRead:
    return BaselineRead.of(await service.get(baseline_id))


@router.delete(
    "/baselines/{baseline_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_baseline(baseline_id: UUID, service: Baselines) -> Response:
    await service.delete(baseline_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/baselines/{baseline_id}/diff", response_model=BaselineDiffRead, responses=NOT_FOUND)
async def read_baseline_diff(
    baseline_id: UUID,
    service: Baselines,
    against: Annotated[Against | None, Query()] = None,
) -> BaselineDiffRead:
    """What separates a baseline from another one, or from the model as it is now."""
    return BaselineDiffRead.of(await service.diff(baseline_id, against_of(against)))


@router.post(
    "/baselines/{baseline_id}/restore",
    response_model=RestoreRead,
    responses={
        **NOT_FOUND,
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    },
)
async def restore_baseline(
    baseline_id: UUID, payload: RestoreRequest, service: Baselines
) -> RestoreRead:
    """Put the model back as the baseline had it — a dry run unless told otherwise.

    Applying needs `dry_run=false` **and** the `digest` of the dry run you read,
    as `expected_digest`. One transaction: a conflict (`restore_conflict`, read
    the plan again) or a locked element (`restore_blocked`) changes nothing.
    """
    return RestoreRead.of(
        await service.restore(
            baseline_id,
            dry_run=payload.dry_run,
            expected_digest=payload.expected_digest,
            keep_new=payload.keep_new,
        )
    )
