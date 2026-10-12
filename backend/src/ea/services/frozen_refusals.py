"""The trace of a write that a published subtree refused (docs/adr/0074).

The refusal rolls the write back, so the event is written in a transaction of its own,
after it. A caller who loops on refusals must not turn the journal into a flood: past
`per_minute` events from one subject the event is no longer written, and the log says
how many were left out.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ea.domain.audit import AuditAction
from ea.domain.package_versions import FrozenRefusal
from ea.services.audit import AuditTrail
from ea.services.caller import current_caller

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from ea.domain.audit import AuditEvent

logger = logging.getLogger("ea.refusals")

Sink = Callable[[Sequence["AuditEvent"]], Awaitable[None]]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def to_database(sessions: async_sessionmaker[AsyncSession]) -> Sink:
    """The sink that writes the events in a transaction of their own."""
    from ea.repositories.audit_store import write_events

    async def write(events: Sequence[AuditEvent]) -> None:
        async with sessions.begin() as session:
            write_events(session, events)

    return write


class FrozenRefusalRecorder:
    def __init__(
        self,
        sink: Sink,
        *,
        clock: Callable[[], datetime] = _utc_now,
        per_minute: int = 30,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self._trail = AuditTrail(clock)
        self._per_minute = per_minute
        self._monotonic = monotonic
        self._recent: defaultdict[str, deque[float]] = defaultdict(deque)
        self._suppressed: defaultdict[str, int] = defaultdict(int)

    def _admit(self, subject: str) -> bool:
        now = self._monotonic()
        recent = self._recent[subject]
        while recent and now - recent[0] >= 60.0:
            recent.popleft()
        if len(recent) >= self._per_minute:
            self._suppressed[subject] += 1
            return False
        recent.append(now)
        return True

    async def __call__(self, refusal: FrozenRefusal) -> None:
        caller = current_caller.get()
        facts = {
            "package_id": str(refusal.package_id) if refusal.package_id else None,
            "version": refusal.version,
            "table": refusal.table,
            "operation": refusal.operation,
        }
        if caller is None or not self._admit(caller.subject):
            logger.warning(
                "write refused by a published package",
                extra={
                    "action": "write_refused_frozen",
                    "recorded": False,
                    "suppressed": self._suppressed.get(caller.subject, 0) if caller else 0,
                    **facts,
                },
            )
            return
        event = self._trail.event(
            AuditAction.PACKAGE_WRITE_REFUSED_FROZEN,
            entity_id=refusal.package_id,
            after={
                "version": refusal.version,
                "table": refusal.table,
                "operation": refusal.operation,
            },
        )
        await self._sink([event])
        logger.warning(
            "write refused by a published package",
            extra={"action": "write_refused_frozen", "recorded": True, **facts},
        )
