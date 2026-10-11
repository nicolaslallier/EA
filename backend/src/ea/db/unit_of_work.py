"""One transaction shared by several use cases (docs/adr/0050).

Every repository opens a unit of work per call — right for a use case that
writes once, wrong for an import that creates hundreds of elements and links
through the services (so every rule applies), undoes a refused row and is
rolled back as a whole in a dry run.

`shared_transaction` opens one session and binds it to the *current context*;
`work` (a write) and `reading` (a read) are the only ways a repository gets its
session, and hand the shared one back while it is bound. The binding is a
`ContextVar`, like the caller of `services/caller.py`: invisible to the routes,
private to the task that opened it and the tasks created from it, absent for
every other request. Whoever opens the transaction owns commit and rollback;
a repository inside it never commits.

Inside a unit, a write is a `SAVEPOINT`, so a call that fails undoes its own
writes (its audit events included) and the unit stays usable. A serialization
failure or a deadlock is the one exception: PostgreSQL has aborted the
transaction, so the unit is *condemned* — every later call is refused with the
same replayable `WriteConflictError`, and the end of the block rolls back.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError

from ea.domain.errors import ImportInProgressError, WriteConflictError
from ea.domain.ports import Transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

#: The advisory lock one import at a time holds ("EAIX"); `package_store.TREE_LOCK` is another.
IMPORT_LOCK: Final = 0x45414958

_CONDEMNED: Final = "another change got in the way of this one: run the import again"


class _Unit:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.condemned = False

    def refuse_if_condemned(self) -> None:
        if self.condemned:
            raise WriteConflictError(_CONDEMNED)


_current: ContextVar[_Unit | None] = ContextVar("ea_unit_of_work", default=None)


def is_serialization_failure(error: DBAPIError) -> bool:
    """SQLSTATE 40001 (and 40P01, a deadlock) — carried by the driver's own exception."""
    cause = getattr(error.orig, "__cause__", None)
    code = getattr(cause, "sqlstate", None) or getattr(error.orig, "sqlstate", None)
    return code in ("40001", "40P01")


def ambient_session() -> AsyncSession | None:
    """The session of the shared transaction open in this context, if any."""
    unit = _current.get()
    return unit.session if unit is not None else None


def in_unit() -> bool:
    return _current.get() is not None


def condemn_unit() -> None:
    """Mark the open unit (if any) as aborted by the database: nothing more can be tried in it."""
    unit = _current.get()
    if unit is not None:
        unit.condemned = True


def _joined(unit: _Unit, *, alone: bool) -> AsyncSession:
    if alone:
        msg = "this call needs a transaction of its own (an isolation level), not an import's"
        raise RuntimeError(msg)
    unit.refuse_if_condemned()
    # Rows loaded earlier are cached in the session; another transaction may have committed
    # a change since (READ COMMITTED), and a lock taken now must be followed by a fresh read.
    # Everything this unit wrote is flushed by the end of the previous call.
    unit.session.expire_all()
    return unit.session


@asynccontextmanager
async def work(
    sessions: async_sessionmaker[AsyncSession], *, alone: bool = False
) -> AsyncIterator[AsyncSession]:
    """The unit of work of one repository *write*.

    Outside a shared transaction: a session of its own, committed when the call
    ends — what every repository did before this module existed. Inside: the
    shared session, in a `SAVEPOINT` the call releases or rolls back by itself.
    `alone` is for a call that sets its own isolation level and cannot join.
    """
    unit = _current.get()
    if unit is None:
        async with sessions.begin() as own:
            yield own
        return
    session = _joined(unit, alone=alone)
    try:
        async with session.begin_nested():
            yield session
    except DBAPIError as error:
        if is_serialization_failure(error):
            unit.condemned = True
            logger.warning(
                "unit of work condemned by the database",
                extra={"action": "unit_condemned"},
            )
            raise WriteConflictError(_CONDEMNED) from error
        raise


@asynccontextmanager
async def reading(
    sessions: async_sessionmaker[AsyncSession], *, alone: bool = False
) -> AsyncIterator[AsyncSession]:
    """The session of one repository *read*: the shared one inside a unit (it sees its writes)."""
    unit = _current.get()
    if unit is None:
        async with sessions() as own:
            yield own
        return
    yield _joined(unit, alone=alone)


@asynccontextmanager
async def shared_transaction(
    sessions: async_sessionmaker[AsyncSession], *, commit: bool, exclusive: bool = False
) -> AsyncIterator[Transaction]:
    """Open the transaction every `work` and `reading` call in this context will join.

    It ends with a commit only if `handle.commit` is still true when the block
    exits normally and the unit was not condemned; an exception, `commit=False`
    (a dry run) or a condemned unit roll everything back. `exclusive` takes
    `IMPORT_LOCK` without waiting, as the first statement: a second import is
    refused at once (`ImportInProgressError`), and the lock goes with the transaction.
    """
    if _current.get() is not None:
        msg = "a shared transaction is already open in this context"
        raise RuntimeError(msg)
    async with sessions() as session:
        outer = await session.begin()
        unit = _Unit(session)
        handle = Transaction(commit=commit)
        token = _current.set(unit)
        try:
            if exclusive and not await session.scalar(
                select(func.pg_try_advisory_xact_lock(IMPORT_LOCK))
            ):
                msg = "another import is running: wait for it to finish"
                raise ImportInProgressError(msg)
            yield handle
        except BaseException:
            if outer.is_active:
                await outer.rollback()
            raise
        else:
            if unit.condemned:
                await outer.rollback()
                raise WriteConflictError(_CONDEMNED)
            if handle.commit:
                try:
                    await outer.commit()
                except DBAPIError as error:
                    if not is_serialization_failure(error):
                        raise
                    raise WriteConflictError(_CONDEMNED) from error
            else:
                await outer.rollback()
        finally:
            _current.reset(token)


@asynccontextmanager
async def savepoint() -> AsyncIterator[None]:
    """A `SAVEPOINT` for one row: a refusal undoes only its own writes, and re-raises."""
    unit = _current.get()
    if unit is None:
        msg = "a savepoint needs a shared transaction"
        raise RuntimeError(msg)
    unit.refuse_if_condemned()
    async with unit.session.begin_nested():
        yield
