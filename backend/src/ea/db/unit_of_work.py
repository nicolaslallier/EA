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
import re
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import func, select, text
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError, SQLAlchemyError

from ea.domain.errors import (
    DomainError,
    ImportInProgressError,
    NotAuthenticatedError,
    NotAuthorisedError,
    TooManyRefusedRowsError,
    WriteConflictError,
)
from ea.domain.ports import RowRefusal, Transaction

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = logging.getLogger(__name__)

#: The advisory lock one import at a time holds ("EAIX"); `package_store.TREE_LOCK` is another.
IMPORT_LOCK: Final = 0x45414958

#: Rows per `SAVEPOINT`. A savepoint that wrote stays counted against the transaction until
#: it ends, even once released, and PostgreSQL stops tracking sub-transactions at 64 per
#: transaction (it then slows every other session's snapshots): 50 000 rows / 1 000 = 50.
BATCH_SIZE: Final = 1_000
#: Refused rows an import tolerates. Each costs a replay of the rows before it in its batch.
MAX_REFUSED_ROWS: Final = 100

#: What a row can be refused with. Anything else (a lost lock, a missing role) is not its fault.
_ROW_REFUSALS: Final = (DomainError, IntegrityError, DataError)
_NOT_THE_ROWS: Final = (
    WriteConflictError,
    ImportInProgressError,
    NotAuthenticatedError,
    NotAuthorisedError,
    TooManyRefusedRowsError,
)

_CONDEMNED: Final = "another change got in the way of this one: run the import again"


class _Unit:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.condemned = False
        #: Open batches (or replays): a call inside one adds no savepoint of its own.
        self.batched = 0

    def refuse_if_condemned(self) -> None:
        if self.condemned:
            raise WriteConflictError(_CONDEMNED)


_current: ContextVar[_Unit | None] = ContextVar("ea_unit_of_work", default=None)


def is_serialization_failure(error: DBAPIError) -> bool:
    """SQLSTATE 40001, 40P01 (a deadlock) or 55P03 (a lock wait that timed out).

    All three mean "another change got in the way: run it again", and the
    sub-state they leave a transaction in is not one to carry on from.
    """
    cause = getattr(error.orig, "__cause__", None)
    code = getattr(cause, "sqlstate", None) or getattr(error.orig, "sqlstate", None)
    return code in ("40001", "40P01", "55P03")


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


_SAVEPOINT_NAME: Final = re.compile(r"sa_savepoint_\d+")


@asynccontextmanager
async def _nested(session: AsyncSession) -> AsyncIterator[None]:
    """`session.begin_nested()` that also lets go of its savepoint when it is rolled back.

    `ROLLBACK TO SAVEPOINT` undoes the writes but leaves the savepoint alive, as an empty
    sub-transaction one level deeper. The next write then gives an id to every level above
    it, so each refusal would add one tracked sub-transaction until PostgreSQL's cache of 64
    overflows (measured: docs/adr/0050). Releasing the empty level right away costs nothing.
    """
    transaction = await session.begin_nested()
    await session.connection()  # SQLAlchemy emits the SAVEPOINT on first use of the connection
    # SQLAlchemy's own name for it: there is no public way to ask for it.
    held: Any = transaction.sync_transaction
    name: str = next(entry[1]._savepoint for entry in held._connections.values())
    if not _SAVEPOINT_NAME.fullmatch(name):  # SQLAlchemy changed its naming: do not guess
        msg = f"unexpected savepoint name {name!r}"
        raise RuntimeError(msg)
    try:
        yield
    except BaseException:
        await transaction.rollback()
        try:
            await session.execute(text(f"RELEASE SAVEPOINT {name}"))
        except SQLAlchemyError:  # the connection is gone: nothing to release, the unit is lost
            logger.warning("savepoint not released", extra={"action": "savepoint_release_failed"})
        raise
    else:
        await transaction.commit()


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
        if unit.batched:
            yield session
        else:
            async with _nested(session):
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
    """A `SAVEPOINT` around several calls: any exception undoes all of them, and re-raises.

    Calls made inside it open no savepoint of their own. Prefer `apply_rows`, which
    batches rows so an import stays under PostgreSQL's 64 tracked sub-transactions.
    """
    unit = _current.get()
    if unit is None:
        msg = "a savepoint needs a shared transaction"
        raise RuntimeError(msg)
    unit.refuse_if_condemned()
    unit.batched += 1
    try:
        async with _nested(unit.session):
            yield
    finally:
        unit.batched -= 1


async def apply_rows[Row](
    rows: Sequence[Row],
    apply: Callable[[Row], Awaitable[None]],
    *,
    batch_size: int = BATCH_SIZE,
    max_refused: int = MAX_REFUSED_ROWS,
) -> list[RowRefusal]:
    """Apply each row in the shared transaction; a refused row is skipped, the others stay.

    Rows run in batches of `batch_size`, one `SAVEPOINT` each. A refusal (a rule of the
    domain, a constraint) at row k rolls the batch back, replays rows 0..k-1 of the batch
    *without* a savepoint, drops row k and starts a new batch at k+1: about one savepoint
    per `batch_size` rows, none per refusal. A replay that fails condemns the whole import
    (`WriteConflictError`). Anything that is not the row's fault (`WriteConflictError`, a
    missing role) propagates; so does the `max_refused`+1th refusal
    (`TooManyRefusedRowsError`). Nothing is committed here.

    `apply` must derive its state from what it does *now*: a replayed row creates its
    elements again, under new ids, and the first pass is gone.
    """
    unit = _current.get()
    if unit is None:
        msg = "applying rows needs a shared transaction"
        raise RuntimeError(msg)
    refused: list[RowRefusal] = []
    start = 0
    while start < len(rows):
        end = min(start + batch_size, len(rows))
        position = start
        try:
            async with savepoint():
                for position in range(start, end):
                    await apply(rows[position])
        except _NOT_THE_ROWS:
            raise
        except _ROW_REFUSALS as error:
            refused.append(RowRefusal(position, error))
            if len(refused) > max_refused:
                msg = f"more than {max_refused} rows were refused: the file was rejected as a whole"
                raise TooManyRefusedRowsError(msg) from error
            await _replay(unit, [rows[i] for i in range(start, position)], apply)
            start = position + 1
        else:
            start = end
    return refused


async def _replay[Row](
    unit: _Unit, rows: Sequence[Row], apply: Callable[[Row], Awaitable[None]]
) -> None:
    """Apply again, in the open transaction and with no savepoint, rows that were applied once."""
    unit.batched += 1
    try:
        for row in rows:
            await apply(row)
    except _NOT_THE_ROWS:
        raise
    except _ROW_REFUSALS as error:
        unit.condemned = True  # part of that row is written and there is no savepoint to undo it
        msg = "rows applied once were refused the second time"
        raise WriteConflictError(f"{msg}: {_CONDEMNED}") from error
    finally:
        unit.batched -= 1
