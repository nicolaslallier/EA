"""Places for the imports in flight, taken before their body is read (docs/adr/0048).

The parsing gate of `ExchangeService` bounds the files *analysed* at once; it is entered
after the route has read the whole body, so N simultaneous editors would hold N bodies of
20 MB to have two analysed. A place here is taken by the ASGI door, after the token and the
role and before the first read, and given back when the request is over *and* the thread
that analysed its file has really ended (a thread cannot be stopped: a client that hangs up
leaves it running). Memory is bounded by `limit` bodies per process: with several uvicorn
workers it is workers x limit x the ceiling.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any

from ea.domain.errors import ImportInProgressError


class ImportAdmission:
    """The imports in flight in this process. Single-threaded: no await between check and take."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.in_flight = 0

    def admit(self) -> Ticket:
        if self.in_flight >= self.limit:
            msg = "other imports are being processed: try again in a moment"
            raise ImportInProgressError(msg)
        self.in_flight += 1
        return Ticket(self)

    def _leave(self) -> None:
        self.in_flight -= 1


class Ticket:
    """One place. `release` is idempotent; it takes effect once the held work has ended."""

    def __init__(self, admission: ImportAdmission) -> None:
        self._admission = admission
        self._work: list[asyncio.Future[Any]] = []
        self._released = False
        self._waiting = 0

    def hold(self, work: asyncio.Future[Any]) -> None:
        """Keep the place until `work` (an analysis running in a thread) has ended."""
        self._work.append(work)

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        pending = [work for work in self._work if not work.done()]
        self._waiting = len(pending)
        if not pending:
            self._admission._leave()
            return
        for work in pending:
            work.add_done_callback(self._ended)

    def _ended(self, _: asyncio.Future[Any]) -> None:
        self._waiting -= 1
        if self._waiting == 0:
            self._admission._leave()


#: The ticket of the request being served, set by the door that admitted it.
current_ticket: ContextVar[Ticket | None] = ContextVar("import_ticket", default=None)
