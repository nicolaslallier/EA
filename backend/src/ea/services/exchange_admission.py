"""Places for the imports and the exports in flight, kept as leases (docs/adr/0050).

The parsing gate of `ExchangeService` bounds the files *analysed* at once; it is entered after the
route has read the whole body, so N simultaneous editors would hold N bodies of 20 MB to have two
analysed. A place is taken by the ASGI door — after the token and the role, before the first read
— and given back when the request is over *and* the thread that analysed its file has really
ended (a thread cannot be stopped: a client that hangs up leaves it running).

**A place is a lease, not a count.** The admission keeps the set of live leases, one object per
place, and every figure — in flight, per caller — is computed from that set, never stored. A
counter that is incremented on one path and decremented on another drifts the day one path
forgets, and a counter stuck at its maximum refuses everybody until the process restarts. A lease
is removed by identity (taking it out twice does nothing), and it expires by itself at its
deadline: the next admission purges the expired ones that do nothing any more and counts them
(`expired_leases`), which is the proof that some path leaked. An expired lease whose analysis
thread still runs stays counted — the thread still costs the place — and is logged as overdue.
A lease is taken and given back by one asynchronous context manager, `lease()`, and by nothing
else.

Two ceilings: the global one, and one per caller, keyed by the `sub` of the verified token and by
nothing the request says. What this does not stop: someone with several accounts still fills the
global places. The set is in memory, per process: with several workers the ceilings multiply.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from ea.domain.errors import DomainError, ImportInProgressError

logger = logging.getLogger(__name__)


@dataclass(eq=False, slots=True)
class Lease:
    """One place. Compared and removed by identity."""

    key: str
    acquired_at: float
    deadline: float
    work: list[asyncio.Future[Any]] = field(default_factory=list)
    overdue_said: bool = False

    def hold(self, work: asyncio.Future[Any]) -> None:
        """Keep the place until `work` (an analysis running in a thread) has ended."""
        self.work.append(work)


class Admission:
    """The leases in flight in this process. Single-threaded: no await between check and take."""

    def __init__(
        self,
        limit: int,
        *,
        per_caller: int = 1,
        lease_seconds: float = 1_200.0,
        refusal: Callable[[], DomainError] = lambda: ImportInProgressError(
            "other imports are being processed: try again in a moment"
        ),
        kind: str = "import",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.per_caller = per_caller
        self.kind = kind
        self.expired_leases = 0
        self._lease_seconds = lease_seconds
        self._refusal = refusal
        self._clock = clock
        self._leases: set[Lease] = set()

    # --- What is true, computed from the leases -------------------------------------------

    @property
    def in_flight(self) -> int:
        return len(self._leases)

    @property
    def callers(self) -> dict[str, int]:
        found: dict[str, int] = {}
        for lease in self._leases:
            found[lease.key] = found.get(lease.key, 0) + 1
        return found

    # --- The one way in and out -------------------------------------------------------------

    @asynccontextmanager
    async def lease(self, key: str) -> AsyncIterator[Lease]:
        """A place for `key`, or the refusal; gone from the set however the block ends."""
        self._purge()
        held = self.callers.get(key, 0)
        if len(self._leases) >= self.limit or held >= self.per_caller:
            raise self._refusal()
        now = self._clock()
        taken = Lease(key, now, now + self._lease_seconds)
        self._leases.add(taken)
        try:
            yield taken
        finally:
            self._finish(taken)

    def _finish(self, taken: Lease) -> None:
        """Take the lease out — now, or when the thread that analysed its file has ended."""
        pending = [work for work in taken.work if not work.done()]
        if not pending:
            self._leases.discard(taken)  # by identity: a second call finds nothing to remove
            return
        for work in pending:
            work.add_done_callback(lambda _: self._finish(taken))

    def _purge(self) -> None:
        """Drop what outlived its deadline and does nothing any more: a path that never gave its
        place back. A lease whose analysis thread is still running is **not** dropped: the thread
        still costs what the place stands for, and counting it out would be a way round the
        ceilings (an analysis that outlasts the deadline is logged, `admission_lease_overdue`,
        and leaves with its thread)."""
        now = self._clock()
        expired = [lease for lease in self._leases if lease.deadline <= now]
        gone = [lease for lease in expired if all(work.done() for work in lease.work)]
        for lease in gone:
            self._leases.discard(lease)
        if gone:
            self.expired_leases += len(gone)
            logger.warning(
                "admission leases expired",
                extra={"action": "admission_lease_expired", "kind": self.kind, "count": len(gone)},
            )
        overdue = [lease for lease in expired if lease not in gone and not lease.overdue_said]
        for lease in overdue:
            lease.overdue_said = True
        if overdue:
            logger.warning(
                "admission leases overdue: an analysis still runs",
                extra={
                    "action": "admission_lease_overdue",
                    "kind": self.kind,
                    "count": len(overdue),
                },
            )


#: The lease of the request being served, set by the door that admitted it.
current_lease: ContextVar[Lease | None] = ContextVar("exchange_lease", default=None)
