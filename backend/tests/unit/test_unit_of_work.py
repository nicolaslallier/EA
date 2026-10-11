"""The shared transaction, against fake sessions — no database needed (docs/adr/0052)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from ea.db.unit_of_work import (
    ambient_session,
    in_unit,
    reading,
    savepoint,
    shared_transaction,
    work,
)
from ea.domain.errors import ImportInProgressError, WriteConflictError
from ea.repositories.conflicts import replayable
from tests.unit.test_baseline_store_pure import driver_error

pytestmark = pytest.mark.asyncio


class FakeTransaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.is_active = True
        self.commit_fails: Exception | None = None

    async def commit(self) -> None:
        if self.commit_fails is not None:
            raise self.commit_fails
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


class FakeNested:
    """What `AsyncSession.begin_nested()` returns, as far as the unit of work looks at it."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        savepoint = SimpleNamespace(_savepoint=f"sa_savepoint_{session.savepoints}")
        self.sync_transaction = SimpleNamespace(_connections={0: (None, savepoint, True, True)})

    async def rollback(self) -> None:
        self.session.rolled_back_savepoints += 1

    async def commit(self) -> None:
        pass


class FakeSession:
    def __init__(self, *, lock_free: bool = True) -> None:
        self.transaction = FakeTransaction()
        self.savepoints = 0
        self.rolled_back_savepoints = 0
        self.expired = 0
        self.statements: list[str] = []
        self.lock_free = lock_free

    async def begin(self) -> FakeTransaction:
        return self.transaction

    async def begin_nested(self) -> FakeNested:
        self.savepoints += 1
        return FakeNested(self)

    async def connection(self) -> None:
        pass

    async def execute(self, statement: object) -> None:
        self.statements.append(str(statement))

    def expire_all(self) -> None:
        self.expired += 1

    async def scalar(self, statement: object) -> bool:
        return self.lock_free


class FakeFactory:
    def __init__(self, *, lock_free: bool = True) -> None:
        self.sessions: list[FakeSession] = []
        self.own_units = 0
        self.reads = 0
        self.lock_free = lock_free

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeSession]:
        session = FakeSession(lock_free=self.lock_free)
        self.sessions.append(session)
        yield session

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[FakeSession]:
        self.own_units += 1
        session = FakeSession()
        self.sessions.append(session)
        yield session


def shared(factory: FakeFactory, **kwargs: object):  # type: ignore[no-untyped-def]
    return shared_transaction(factory, **kwargs)  # type: ignore[arg-type]


def own(factory: FakeFactory):  # type: ignore[no-untyped-def]
    return work(factory)  # type: ignore[arg-type]


async def test_outside_a_shared_transaction_work_and_reading_behave_as_before() -> None:
    factory = FakeFactory()

    async with own(factory) as session:
        assert session is factory.sessions[0]
    async with reading(factory) as read:  # type: ignore[arg-type]
        assert read is factory.sessions[1]

    assert factory.own_units == 1  # the write opened a transaction, the read did not
    assert ambient_session() is None
    assert not in_unit()


async def test_inside_a_shared_transaction_every_call_joins_the_one_session() -> None:
    factory = FakeFactory()

    async with shared(factory, commit=True):
        async with own(factory) as first, own(factory) as second:
            assert first is second is factory.sessions[0]
        async with reading(factory) as read:  # type: ignore[arg-type]
            assert read is factory.sessions[0]

    assert factory.own_units == 0
    assert len(factory.sessions) == 1


async def test_each_write_is_a_savepoint_and_a_read_is_not() -> None:
    factory = FakeFactory()

    async with shared(factory, commit=True):
        async with own(factory):
            pass
        async with reading(factory):  # type: ignore[arg-type]
            pass

    assert factory.sessions[0].savepoints == 1


async def test_a_failed_call_rolls_back_to_its_own_savepoint_and_the_unit_goes_on() -> None:
    factory = FakeFactory()

    async with shared(factory, commit=True):
        with pytest.raises(ValueError, match="refused"):
            async with own(factory):
                raise ValueError("refused")
        async with own(factory):
            pass

    session = factory.sessions[0]
    assert (session.savepoints, session.rolled_back_savepoints) == (2, 1)
    assert session.transaction.committed


async def test_what_another_session_committed_is_seen_again_inside_the_unit() -> None:
    """The identity map would otherwise answer a read with a row from before a lock."""
    factory = FakeFactory()

    async with shared(factory, commit=True):
        async with own(factory):
            pass
        async with reading(factory):  # type: ignore[arg-type]
            pass

    assert factory.sessions[0].expired == 2


async def test_commit_true_commits_and_commit_false_rolls_back() -> None:
    factory = FakeFactory()
    async with shared(factory, commit=True):
        pass
    async with shared(factory, commit=False):
        pass

    assert factory.sessions[0].transaction.committed
    assert factory.sessions[1].transaction.rolled_back
    assert not factory.sessions[1].transaction.committed


async def test_the_handle_can_cancel_the_commit_from_inside() -> None:
    factory = FakeFactory()
    async with shared(factory, commit=True) as handle:
        handle.commit = False

    assert factory.sessions[0].transaction.rolled_back


async def test_an_exception_rolls_back_and_forgets_the_session() -> None:
    factory = FakeFactory()

    with pytest.raises(RuntimeError, match="boom"):
        async with shared(factory, commit=True):
            assert in_unit()
            raise RuntimeError("boom")

    assert factory.sessions[0].transaction.rolled_back
    assert not factory.sessions[0].transaction.committed
    assert ambient_session() is None


async def test_a_second_shared_transaction_in_the_same_context_is_refused() -> None:
    factory = FakeFactory()

    async with shared(factory, commit=False):
        with pytest.raises(RuntimeError, match="already open"):
            async with shared(factory, commit=False):
                pass
        assert in_unit()  # and the refusal did not end the first one
    assert not in_unit()


async def test_a_savepoint_needs_a_shared_transaction() -> None:
    with pytest.raises(RuntimeError, match="shared transaction"):
        async with savepoint():
            pass


async def test_a_savepoint_is_a_nested_transaction_of_the_shared_session() -> None:
    factory = FakeFactory()

    async with shared(factory, commit=False), savepoint():
        pass

    assert factory.sessions[0].savepoints == 1


async def test_a_call_that_needs_isolation_of_its_own_refuses_to_join() -> None:
    """A baseline sets REPEATABLE READ before its first statement: impossible mid-transaction."""
    factory = FakeFactory()

    async with shared(factory, commit=False):
        with pytest.raises(RuntimeError, match="transaction of its own"):
            async with work(factory, alone=True):  # type: ignore[arg-type]
                pass
        with pytest.raises(RuntimeError, match="transaction of its own"):
            async with reading(factory, alone=True):  # type: ignore[arg-type]
                pass

    async with work(factory, alone=True):  # type: ignore[arg-type]
        pass  # outside a unit it is what it always was


async def test_two_units_running_together_never_share_a_session() -> None:
    factory = FakeFactory()
    both_open = asyncio.Barrier(2)
    seen: list[object] = []

    async def import_file() -> None:
        async with shared(factory, commit=False):
            await both_open.wait()
            async with own(factory) as session:
                seen.append(session)

    await asyncio.gather(import_file(), import_file())

    assert len({id(session) for session in seen}) == 2


async def test_an_ordinary_request_beside_a_unit_does_not_join_it() -> None:
    factory = FakeFactory()
    opened = asyncio.Event()
    done = asyncio.Event()

    async def import_file() -> None:
        async with shared(factory, commit=False):
            opened.set()
            await done.wait()

    async def ordinary_request() -> object:
        await opened.wait()
        assert ambient_session() is None
        async with own(factory) as session:
            return session
        return None

    importing = asyncio.create_task(import_file())
    session = await asyncio.create_task(ordinary_request())
    done.set()
    await importing

    assert session is not factory.sessions[0]
    assert factory.own_units == 1


async def test_an_exclusive_unit_refuses_when_another_holds_the_import_lock() -> None:
    factory = FakeFactory(lock_free=False)

    with pytest.raises(ImportInProgressError):
        async with shared(factory, commit=True, exclusive=True):
            pytest.fail("the body must not run")

    assert factory.sessions[0].transaction.rolled_back
    assert not in_unit()


async def test_an_exclusive_unit_runs_when_the_lock_is_free() -> None:
    factory = FakeFactory(lock_free=True)
    async with shared(factory, commit=True, exclusive=True):
        assert in_unit()
    assert factory.sessions[0].transaction.committed


@pytest.mark.parametrize("sqlstate", ["40001", "40P01"])
async def test_a_serialization_failure_cannot_be_caught_and_gone_on_from(sqlstate: str) -> None:
    factory = FakeFactory()

    with pytest.raises(WriteConflictError, match="again"):
        async with shared(factory, commit=True):
            with pytest.raises(WriteConflictError):
                async with own(factory):
                    raise DBAPIError("", {}, driver_error(sqlstate))
            # the transaction is condemned: nothing else may be tried in it...
            with pytest.raises(WriteConflictError):
                async with own(factory):
                    pytest.fail("the body must not run")
            with pytest.raises(WriteConflictError):
                async with reading(factory):  # type: ignore[arg-type]
                    pytest.fail("the body must not run")
            with pytest.raises(WriteConflictError):
                async with savepoint():
                    pytest.fail("the body must not run")
            # ...and swallowing the error does not turn the end into a commit.

    assert factory.sessions[0].transaction.rolled_back
    assert not factory.sessions[0].transaction.committed


async def test_any_other_database_failure_leaves_the_unit_usable() -> None:
    factory = FakeFactory()

    async with shared(factory, commit=True):
        with pytest.raises(DBAPIError):
            async with own(factory):
                raise DBAPIError("", {}, driver_error("23505"))
        async with own(factory):
            pass

    assert factory.sessions[0].transaction.committed


async def test_a_failure_at_commit_time_is_the_same_replayable_conflict() -> None:
    factory = FakeFactory()

    with pytest.raises(WriteConflictError):
        async with shared(factory, commit=True):
            factory.sessions[0].transaction.commit_fails = DBAPIError("", {}, driver_error("40001"))


async def test_replayable_does_not_run_a_write_again_inside_a_unit_and_condemns_it() -> None:
    factory = FakeFactory()
    calls = 0

    @replayable
    async def write() -> None:
        nonlocal calls
        calls += 1
        raise DBAPIError("", {}, driver_error("40P01"))

    with pytest.raises(WriteConflictError):
        async with shared(factory, commit=True):
            with pytest.raises(WriteConflictError):
                await write()
            with pytest.raises(WriteConflictError):
                async with own(factory):
                    pytest.fail("a condemned unit takes no more work")

    assert calls == 1


async def test_a_rolled_back_savepoint_is_released_so_it_leaves_no_empty_level_behind() -> None:
    factory = FakeFactory()

    async with shared(factory, commit=True):
        with pytest.raises(ValueError, match="refused"):
            async with own(factory):
                raise ValueError("refused")
        async with own(factory):
            pass

    session = factory.sessions[0]
    assert session.rolled_back_savepoints == 1
    assert session.statements == ["RELEASE SAVEPOINT sa_savepoint_1"]  # and none for the other
