"""The audit trail over HTTP: one question, newest first, a page at a time.

Reads only. A trail that could be written to over HTTP would not be one: events
are made by the services, in the transaction of the write they describe.
Any authenticated caller reads (docs/adr/0044).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from ea.api.dependencies import Audit
from ea.api.schemas import (
    Actor,
    AuditPageRead,
    CursorToken,
    ErrorResponse,
    Limit,
    RequestIdFilter,
)
from ea.domain.audit import DEFAULT_AUDIT_PAGE, AuditAction, AuditFilter, EntityKind, aware

router = APIRouter(tags=["audit"])

LimitQuery = Annotated[Limit, Query()]


@router.get(
    "/audit",
    response_model=AuditPageRead,
    responses={422: {"model": ErrorResponse}},
)
async def list_audit_events(
    service: Audit,
    entity_kind: Annotated[EntityKind | None, Query()] = None,
    entity_id: Annotated[UUID | None, Query()] = None,
    actor: Annotated[Actor | None, Query()] = None,
    action: Annotated[AuditAction | None, Query()] = None,
    since: Annotated[datetime | None, Query()] = None,
    until: Annotated[datetime | None, Query()] = None,
    request_id: Annotated[RequestIdFilter | None, Query()] = None,
    cursor: Annotated[CursorToken | None, Query()] = None,
    limit: LimitQuery = DEFAULT_AUDIT_PAGE,
) -> AuditPageRead:
    """Who changed what, and when — newest first. `since` is inclusive, `until` exclusive."""
    return AuditPageRead.of(
        await service.list_events(
            AuditFilter(
                entity_kind=entity_kind,
                entity_id=entity_id,
                actor=actor,
                action=action,
                since=aware(since),
                until=aware(until),
                request_id=request_id,
                cursor=cursor,
                limit=limit,
            )
        )
    )


@router.get(
    "/elements/{element_id}/history",
    response_model=AuditPageRead,
    responses={422: {"model": ErrorResponse}},
)
async def read_element_history(
    element_id: UUID,
    service: Audit,
    cursor: Annotated[CursorToken | None, Query()] = None,
    limit: LimitQuery = DEFAULT_AUDIT_PAGE,
) -> AuditPageRead:
    """The trail of one element — still readable once the element is deleted."""
    return AuditPageRead.of(
        await service.list_events(
            AuditFilter(
                entity_kind=EntityKind.ELEMENT, entity_id=element_id, cursor=cursor, limit=limit
            )
        )
    )
