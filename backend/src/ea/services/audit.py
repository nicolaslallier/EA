"""Building audit events, and reading the trail — see docs/adr/0044.

`AuditTrail` is the one place an `AuditEvent` is made: the actor comes from the
verified caller and never from a parameter, the moment from the service's
injected clock, the request from the logging context, the door from the
adapter. A use case says *what* happened; the repository inserts the event in
the same transaction as the write.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from ea.core.logging import request_id
from ea.domain.audit import MAX_AUDIT_PAGE, AuditAction, AuditEvent, AuditFilter, AuditPage
from ea.services.caller import current_channel, require_caller

if TYPE_CHECKING:
    from ea.domain.ports import AuditRepository

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class AuditTrail:
    """Makes the events of one service; holds no state but the clock."""

    def __init__(self, clock: Clock = _utc_now) -> None:
        self._now = clock

    def event(
        self,
        action: AuditAction,
        *,
        entity_id: UUID | None,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
        correlation: str | None = None,
    ) -> AuditEvent:
        caller = require_caller()
        return AuditEvent(
            id=uuid4(),
            at=self._now(),
            actor_sub=caller.subject,
            actor_name=caller.username,
            action=action,
            entity_kind=action.kind,
            entity_id=entity_id,
            before=before,
            after=after,
            request_id=request_id.get(),
            channel=current_channel.get(),
            correlation=correlation,
        )


class AuditService:
    """The single entry point `api/` and `mcp/` use to read the trail."""

    def __init__(self, repository: AuditRepository) -> None:
        self._repository = repository

    async def list_events(self, criteria: AuditFilter) -> AuditPage:
        """Any authenticated caller reads (spec §8, R7: restricting it is one guard)."""
        require_caller()
        if criteria.limit < 1:
            msg = "a page has at least 1 event"
            raise ValueError(msg)
        if criteria.limit > MAX_AUDIT_PAGE:
            msg = f"a page has at most {MAX_AUDIT_PAGE} events"
            raise ValueError(msg)
        return await self._repository.page(criteria)
