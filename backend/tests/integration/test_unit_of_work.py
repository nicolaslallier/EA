"""The shared transaction against a real PostgreSQL (docs/adr/0050).

Rollback and commit, one SAVEPOINT per row, the dry run that leaves nothing
behind, one import at a time, the audit trail that follows the transaction, and
what an import's long-held locks do to the writers around it.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.core.config import Settings
from ea.db.models.architecture import ElementRecord
from ea.db.postgres import create_api_engine, create_session_factory
from ea.db.unit_of_work import (
    IMPORT_LOCK,
    ambient_session,
    apply_rows,
    in_unit,
    savepoint,
    shared_transaction,
)
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import AuditChannel
from ea.domain.errors import (
    DuplicateElementError,
    ElementLockedError,
    IllegalRelationshipError,
    ImportInProgressError,
    TooManyRefusedRowsError,
    WriteConflictError,
)
from ea.domain.ports import ElementFilter
from ea.repositories import architecture_store
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.repositories.baseline_store import PostgresBaselineRepository
from ea.services.architecture import ArchitectureService
from ea.services.caller import acting_as, acting_through
from tests.conftest import an_approver
from tests.integration.test_element_review import (
    put_in_state,
    state_of,
    wait_until_a_statement_waits_for_a_lock,
)
from tests.unit.test_baseline_store_pure import driver_error

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


class Bench:
    def __init__(self, engine: AsyncEngine) -> None:
        self.engine = engine
        self.sessions = create_session_factory(engine)
        self.repository = PostgresArchitectureRepository(self.sessions)
        self.graph = ArchitectureService(self.repository)

    def unit(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        return shared_transaction(self.sessions, **{"commit": True, **kwargs})

    async def counts(self) -> tuple[int, int, int]:
        """(elements, relationships, audit events), read from a connection of its own."""
        async with self.engine.connect() as connection:
            row = await connection.execute(
                text(
                    "SELECT (SELECT count(*) FROM elements), (SELECT count(*) FROM relationships),"
                    " (SELECT count(*) FROM audit_events)"
                )
            )
            return tuple(row.one())  # type: ignore[return-value]

    async def names(self) -> list[str]:
        async with self.engine.connect() as connection:
            rows = await connection.execute(text("SELECT name FROM elements ORDER BY name"))
            return [name for (name,) in rows]


@pytest.fixture
def bench(engine_at_head: AsyncEngine) -> Bench:
    return Bench(engine_at_head)


async def test_writes_in_a_rolled_back_unit_vanish_but_are_visible_inside(bench: Bench) -> None:
    async with bench.unit(commit=False):
        await bench.graph.create_element(element_type=E.NODE, name="a")
        assert await bench.repository.count_elements(ElementFilter()) == 1
        assert await bench.counts() == (0, 0, 0)  # nobody else sees it, the trail included

    assert await bench.counts() == (0, 0, 0)
    assert not in_unit()


async def test_writes_in_a_committed_unit_stay_with_their_events(bench: Bench) -> None:
    async with bench.unit():
        a = await bench.graph.create_element(element_type=E.NODE, name="a")
        b = await bench.graph.create_element(element_type=E.NODE, name="b")
        await bench.graph.connect(relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id)

    assert await bench.counts() == (2, 1, 3)


# --- One SAVEPOINT per row -------------------------------------------------


async def _duplicate(bench: Bench) -> None:
    await bench.graph.create_element(element_type=E.NODE, name="valid first")
    await bench.graph.create_element(element_type=E.NODE, name="kept")  # the one refused


async def _illegal_link(bench: Bench) -> None:
    data = await bench.graph.create_element(element_type=E.DATA_OBJECT, name="data")
    node = await bench.graph.create_element(element_type=E.NODE, name="node")
    await bench.graph.connect(relationship_type=R.ASSIGNMENT, source_id=data.id, target_id=node.id)


async def _link_to_a_locked_element(bench: Bench) -> None:
    locked = (await bench.graph.list_elements(ElementFilter(search="locked")))[0]
    fresh = await bench.graph.create_element(element_type=E.NODE, name="fresh")
    await bench.graph.connect(
        relationship_type=R.ASSOCIATION, source_id=fresh.id, target_id=locked.id
    )


async def _raw_integrity_error(bench: Bench) -> None:
    session = ambient_session()
    assert session is not None
    kept = (await bench.graph.list_elements(ElementFilter(search="kept")))[0]
    await bench.graph.create_element(element_type=E.NODE, name="raw")
    session.add(ElementRecord(**architecture_store.element_row(kept)))  # the same primary key
    await session.flush()


@pytest.mark.parametrize(
    ("row", "refusal"),
    [
        (_duplicate, DuplicateElementError),
        (_illegal_link, IllegalRelationshipError),
        (_link_to_a_locked_element, ElementLockedError),
        (_raw_integrity_error, IntegrityError),
    ],
    ids=["duplicate", "metamodel", "locked", "integrity"],
)
async def test_a_refused_row_rolls_back_to_its_savepoint_only(
    bench: Bench,
    engine_at_head: AsyncEngine,
    row: Callable[[Bench], Awaitable[None]],
    refusal: type[Exception],
) -> None:
    """Its elements, its links and its audit events go; the rows around it stay."""
    with acting_as(an_approver()):
        locked = await bench.graph.create_element(element_type=E.NODE, name="locked")
        await put_in_state(engine_at_head, locked.id, "approved", locked=True)

        async with bench.unit():
            await bench.graph.create_element(element_type=E.NODE, name="kept")
            with pytest.raises(refusal):
                async with savepoint():
                    await row(bench)
            await bench.graph.create_element(element_type=E.NODE, name="after")

    assert await bench.names() == ["after", "kept", "locked"]
    assert (await bench.counts())[1] == 0  # no link survived the refused row
    # locked + kept + after, one event each: nothing orphaned from the refused row
    assert (await bench.counts())[2] == 3


async def test_a_link_to_an_approved_element_reopens_it_and_a_dry_run_undoes_that(
    bench: Bench, engine_at_head: AsyncEngine
) -> None:
    with acting_as(an_approver()):
        approved = await bench.graph.create_element(element_type=E.NODE, name="approved")
        await put_in_state(engine_at_head, approved.id, "approved")

        async with bench.unit(commit=False):
            fresh = await bench.graph.create_element(element_type=E.NODE, name="fresh")
            await bench.graph.connect(
                relationship_type=R.ASSOCIATION, source_id=fresh.id, target_id=approved.id
            )
            reopened = await bench.graph.get_element(approved.id)
            assert reopened.approval_state.value == "in_review"  # what the report must say

    assert await state_of(engine_at_head, approved.id) == ("approved", False, 1)


# --- A serialization failure cannot be caught row by row --------------------


async def test_a_serialization_failure_sinks_the_whole_import(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def aborted(session: Any, element: Any) -> None:
        await session.execute(
            text("DO $$ BEGIN RAISE EXCEPTION 'abort' USING ERRCODE = '40001'; END $$")
        )

    with pytest.raises(WriteConflictError):
        async with bench.unit():
            await bench.graph.create_element(element_type=E.NODE, name="first")
            monkeypatch.setattr(architecture_store, "_flush_refusing", aborted)
            with pytest.raises(WriteConflictError):
                async with savepoint():
                    await bench.graph.create_element(element_type=E.NODE, name="second")
            # the row-level handler above is all an import has: the next row is refused too
            with pytest.raises(WriteConflictError):
                await bench.graph.create_element(element_type=E.NODE, name="third")

    assert await bench.counts() == (0, 0, 0)  # not even the row before the failure


async def test_a_deadlock_with_a_normal_write_is_a_replayable_conflict_never_a_hang(
    bench: Bench,
) -> None:
    first = await bench.graph.create_element(element_type=E.NODE, name="first")
    second = await bench.graph.create_element(element_type=E.NODE, name="second")
    low, high = sorted([first.id, second.id])
    await bench.graph.connect(relationship_type=R.ASSOCIATION, source_id=low, target_id=high)
    outside = contextvars.copy_context()  # a request that is not part of the import

    async def import_file() -> None:
        async with bench.unit():
            await bench.graph.update_element(high, description="import")  # locks `high`
            writer.set_result(None)
            await wait_until_a_statement_waits_for_a_lock(bench.engine)
            await bench.graph.update_element(low, description="import")  # wants what W holds

    writer: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def normal_write() -> None:
        await writer
        # locks `low`, then waits for `high`, which the import holds
        await bench.graph.delete_element(low)

    results = await asyncio.gather(
        asyncio.create_task(import_file()),
        asyncio.create_task(normal_write(), context=outside),
        return_exceptions=True,
    )

    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1, results  # PostgreSQL picks one victim
    assert isinstance(failures[0], WriteConflictError), results  # replayable, never a 500


# --- A dry run leaves nothing behind -----------------------------------------


async def test_a_dry_run_changes_nothing_and_holds_no_lock(
    bench: Bench, engine_at_head: AsyncEngine
) -> None:
    kept = await bench.graph.create_element(element_type=E.NODE, name="kept")
    before = await bench.counts()

    async with bench.unit(commit=False, exclusive=True):
        a = await bench.graph.create_element(element_type=E.NODE, name="a")
        await bench.graph.connect(
            relationship_type=R.ASSOCIATION, source_id=a.id, target_id=kept.id
        )
        await bench.graph.update_element(kept.id, description="touched")  # a row lock, held

    assert await bench.counts() == before
    assert (await bench.graph.get_element(kept.id)).description == ""
    async with engine_at_head.connect() as connection:
        locks = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' OR "
                    "(locktype = 'transactionid' AND NOT granted)"
                )
            )
        ).scalar_one()
        idle = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE state LIKE 'idle in transaction%' AND pid <> pg_backend_pid()"
                )
            )
        ).scalar_one()
    assert (locks, idle) == (0, 0)
    async with engine_at_head.begin() as connection:  # the row can be locked without waiting
        await connection.execute(text("SET LOCAL lock_timeout = '200ms'"))
        await connection.execute(
            text("SELECT 1 FROM elements WHERE id = :id FOR UPDATE"), {"id": kept.id}
        )


# --- One import at a time ----------------------------------------------------


async def test_a_second_import_is_refused_at_once_and_the_lock_goes_with_the_first(
    bench: Bench,
) -> None:
    outside = contextvars.copy_context()

    async def second() -> None:
        async with bench.unit(commit=False, exclusive=True):
            pytest.fail("the second import must not run")

    async with bench.unit(commit=False, exclusive=True):
        started = time.monotonic()
        with pytest.raises(ImportInProgressError):
            await asyncio.wait_for(asyncio.create_task(second(), context=outside), timeout=5)
        assert time.monotonic() - started < 1  # refused, not queued

    with pytest.raises(RuntimeError, match="boom"):  # and an import that dies releases it too
        async with bench.unit(commit=False, exclusive=True):
            raise RuntimeError("boom")
    async with bench.unit(commit=False, exclusive=True):
        pass

    async with bench.engine.connect() as connection:
        free = (
            await connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": IMPORT_LOCK}
            )
        ).scalar_one()
    assert free is True


async def test_an_unrelated_request_is_not_stopped_by_the_import_lock(bench: Bench) -> None:
    outside = contextvars.copy_context()
    async with bench.unit(commit=False, exclusive=True):
        await asyncio.create_task(
            bench.graph.create_element(element_type=E.NODE, name="ordinary"), context=outside
        )

    assert await bench.names() == ["ordinary"]


# --- Contexts never mix --------------------------------------------------------


async def test_a_normal_request_beside_an_import_commits_on_its_own_session(
    bench: Bench,
) -> None:
    opened = asyncio.Event()
    ordinary_done = asyncio.Event()

    async def importing() -> None:
        async with bench.unit(commit=False):
            await bench.graph.create_element(element_type=E.NODE, name="imported")
            opened.set()
            await ordinary_done.wait()

    async def ordinary() -> None:
        await opened.wait()
        assert ambient_session() is None
        await bench.graph.create_element(element_type=E.NODE, name="ordinary")
        assert await bench.names() == ["ordinary"]  # committed while the import is still open
        ordinary_done.set()

    await asyncio.gather(importing(), ordinary())

    assert await bench.names() == ["ordinary"]  # the dry run took only its own


async def test_two_imports_in_a_row_in_two_tasks_never_see_each_other(bench: Bench) -> None:
    barrier = asyncio.Barrier(2)

    async def one(name: str) -> list[str]:
        async with bench.unit(commit=False):
            await bench.graph.create_element(element_type=E.NODE, name=name)
            await barrier.wait()
            return sorted(e.name for e in await bench.graph.list_elements(ElementFilter()))

    assert sorted(await asyncio.gather(one("x"), one("y"))) == [["x"], ["y"]]


async def test_a_read_after_a_lock_sees_what_another_transaction_committed(
    bench: Bench, engine_at_head: AsyncEngine
) -> None:
    element = await bench.graph.create_element(element_type=E.NODE, name="old")

    async with bench.unit(commit=False):
        session = ambient_session()
        assert session is not None
        held = await session.get(ElementRecord, element.id)  # cached, and kept alive
        assert held is not None and held.name == "old"
        async with engine_at_head.begin() as connection:
            await connection.execute(
                text("UPDATE elements SET name = 'new' WHERE id = :id"), {"id": element.id}
            )
        assert (await bench.graph.get_element(element.id)).name == "new"


async def test_a_call_that_sets_its_own_isolation_level_cannot_join_an_import(
    bench: Bench,
) -> None:
    baselines = PostgresBaselineRepository(bench.sessions)

    async with bench.unit(commit=False):
        with pytest.raises(RuntimeError, match="transaction of its own"):
            await baselines.current_snapshot()

    assert await baselines.current_snapshot() is not None  # alone, as always


# --- The trail follows the transaction ------------------------------------------


async def test_the_events_of_an_import_are_attributed_to_the_caller_and_the_door(
    bench: Bench,
) -> None:
    with acting_as(an_approver(subject="importer-1", username="importer")):
        with acting_through(AuditChannel.MCP):
            async with bench.unit():
                a = await bench.graph.create_element(element_type=E.NODE, name="a")
                b = await bench.graph.create_element(element_type=E.NODE, name="b")
                await bench.graph.connect(
                    relationship_type=R.ASSOCIATION, source_id=a.id, target_id=b.id
                )
        with acting_through(AuditChannel.HTTP):
            async with bench.unit(commit=False):
                await bench.graph.create_element(element_type=E.NODE, name="dry")

    async with bench.engine.connect() as connection:
        rows = (
            await connection.execute(text("SELECT DISTINCT actor_sub, channel FROM audit_events"))
        ).all()
    assert [tuple(r) for r in rows] == [("importer-1", "mcp")]  # three events, none for the dry run
    assert (await bench.counts())[2] == 3


async def test_the_unit_is_forgotten_after_an_exception(bench: Bench) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with bench.unit():
            await bench.graph.create_element(element_type=E.NODE, name=str(uuid4()))
            raise RuntimeError("boom")

    assert ambient_session() is None
    assert await bench.counts() == (0, 0, 0)
    await bench.graph.create_element(element_type=E.NODE, name="alone")  # a normal write again
    assert await bench.names() == ["alone"]


# --- Locks held for the length of an import ------------------------------------------


async def test_a_normal_write_waits_for_the_import_to_commit_then_goes_through(
    bench: Bench,
) -> None:
    element = await bench.graph.create_element(element_type=E.NODE, name="n")
    outside = contextvars.copy_context()

    async with bench.unit():
        await bench.graph.update_element(element.id, description="imported")  # row locked
        writer = asyncio.create_task(
            bench.graph.update_element(element.id, name="renamed"), context=outside
        )
        await wait_until_a_statement_waits_for_a_lock(bench.engine)
        assert not writer.done()  # it waits; it neither fails nor overwrites

    final = await writer
    assert (final.name, final.description) == ("renamed", "imported")  # neither undid the other


# --- A call outside a batch is atomic by itself ---------------------------------


async def test_a_call_that_fails_halfway_outside_a_batch_undoes_its_own_writes(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`work()` opens a savepoint of its own when no batch covers it."""

    def failing_trail(session: Any, events: Any) -> None:
        raise RuntimeError("the trail failed after the row was flushed")

    async with bench.unit():
        with monkeypatch.context() as patched:
            patched.setattr(architecture_store, "write_events", failing_trail)
            with pytest.raises(RuntimeError, match="trail failed"):
                await bench.graph.create_element(element_type=E.NODE, name="half")
        await bench.graph.create_element(element_type=E.NODE, name="whole")

    assert await bench.names() == ["whole"]


async def test_a_serialization_failure_outside_work_still_condemns_the_unit(
    bench: Bench, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `replayable` wrapper, not `work()`, sees an error raised before the write opens."""

    def conflicting(audit: Any, element: Any) -> Any:
        raise DBAPIError("", {}, driver_error("40001"))

    with pytest.raises(WriteConflictError):
        async with bench.unit():
            monkeypatch.setattr(architecture_store, "events_for", conflicting)
            with pytest.raises(WriteConflictError):
                await bench.graph.create_element(element_type=E.NODE, name="a")
            monkeypatch.undo()
            with pytest.raises(WriteConflictError):  # the unit is condemned, whatever follows
                await bench.graph.create_element(element_type=E.NODE, name="b")

    assert await bench.counts() == (0, 0, 0)


# --- Batches keep PostgreSQL's sub-transaction cache from overflowing -------------


async def subxacts(bench: Bench) -> tuple[int, bool]:
    """(tracked sub-transactions, overflowed) of the backend running the open unit."""
    session = ambient_session()
    assert session is not None
    await session.execute(text("SELECT pg_stat_clear_snapshot()"))
    # PostgreSQL 17 takes a backend id here, not a pid: look ours up.
    row = await session.execute(
        text(
            "SELECT s.subxact_count, s.subxact_overflowed "
            "FROM pg_stat_get_backend_idset() AS id, LATERAL pg_stat_get_backend_subxact(id) s "
            "WHERE pg_stat_get_backend_pid(id) = pg_backend_pid()"
        )
    )
    count, overflowed = row.one()
    return int(count or 0), bool(overflowed)


async def create_named(bench: Bench, name: str) -> None:
    await bench.graph.create_element(element_type=E.NODE, name=name)


async def test_a_savepoint_per_row_would_overflow_the_cache_which_is_why_rows_are_batched(
    bench: Bench,
) -> None:
    """The control: it shows the measurement below can fail."""
    async with bench.unit(commit=False):
        for i in range(100):
            async with savepoint():
                await create_named(bench, f"row-{i}")
        assert await subxacts(bench) == (64, True)


async def test_two_thousand_rows_stay_under_the_cache(bench: Bench) -> None:
    async with bench.unit(commit=False):
        refused = await apply_rows(
            [f"n{i}" for i in range(2000)], lambda name: create_named(bench, name)
        )
        count, overflowed = await subxacts(bench)

    assert refused == []
    assert count <= 64 and not overflowed
    assert (await bench.counts())[0] == 0  # a dry run


async def test_six_thousand_rows_with_twenty_refused_stay_under_the_cache(bench: Bench) -> None:
    names = [f"n{i}" for i in range(6000)]
    wrong = list(range(150, 6000, 300))  # twenty rows that repeat the name of row 1
    for index in wrong:
        names[index] = "n1"

    async with bench.unit():
        refused = await apply_rows(names, lambda name: create_named(bench, name))
        count, overflowed = await subxacts(bench)

    assert [r.index for r in refused] == wrong
    assert all(isinstance(r.error, DuplicateElementError) for r in refused)
    assert count <= 64 and not overflowed, (count, overflowed)
    assert (await bench.counts())[0] == 6000 - 20
    assert (await bench.counts())[2] == 6000 - 20  # one event per row kept, none for the others


async def test_a_hundred_consecutive_refusals_stay_under_the_cache_and_one_more_is_too_many(
    bench: Bench,
) -> None:
    await bench.graph.create_element(element_type=E.NODE, name="kept")

    async with bench.unit(commit=False):
        refused = await apply_rows(["kept"] * 100 + ["new"], lambda name: create_named(bench, name))
        count, overflowed = await subxacts(bench)
    assert len(refused) == 100
    assert count <= 64 and not overflowed

    with pytest.raises(TooManyRefusedRowsError):
        async with bench.unit():
            await apply_rows(["new"] + ["kept"] * 101, lambda name: create_named(bench, name))
    assert await bench.names() == ["kept"]  # and nothing of the rejected import


# --- A lock wait has an end -----------------------------------------------------------


@pytest_asyncio.fixture
async def api_bench(engine_at_head: AsyncEngine):  # type: ignore[no-untyped-def]
    engine = create_api_engine(Settings(debug=True, postgres_enabled=True, db_lock_timeout_ms=300))
    try:
        yield Bench(engine)
    finally:
        await engine.dispose()


async def hold_row(engine: AsyncEngine, element_id: Any) -> Any:
    """A transaction of its own that locks the row; the caller ends it with `rollback()`."""
    connection = await engine.connect()
    transaction = await connection.begin()
    await connection.execute(
        text("SELECT 1 FROM elements WHERE id = :id FOR UPDATE"), {"id": element_id}
    )
    return transaction, connection


async def test_the_api_engine_asks_postgresql_for_the_lock_timeout(api_bench: Bench) -> None:
    async with api_bench.engine.connect() as connection:
        assert (await connection.execute(text("SHOW lock_timeout"))).scalar_one() == "300ms"


async def test_a_normal_write_waits_for_the_timeout_then_gets_a_replayable_conflict(
    bench: Bench, api_bench: Bench
) -> None:
    element = await bench.graph.create_element(element_type=E.NODE, name="n")
    transaction, connection = await hold_row(bench.engine, element.id)
    try:
        started = time.monotonic()
        with pytest.raises(WriteConflictError, match="retry"):
            await api_bench.graph.update_element(element.id, description="late")
        waited = time.monotonic() - started
    finally:
        await transaction.rollback()
        await connection.close()

    assert 0.25 <= waited < 5
    assert (await bench.graph.get_element(element.id)).description == ""


async def test_an_import_survives_a_short_wait_and_is_condemned_by_a_long_one(
    bench: Bench, api_bench: Bench
) -> None:
    element = await bench.graph.create_element(element_type=E.NODE, name="n")

    transaction, connection = await hold_row(bench.engine, element.id)
    release = asyncio.get_running_loop().call_later(
        0.1, lambda: asyncio.ensure_future(transaction.rollback())
    )
    async with api_bench.unit():
        await api_bench.graph.update_element(element.id, description="after a short wait")
    release.cancel()
    await connection.close()
    assert (await bench.graph.get_element(element.id)).description == "after a short wait"

    transaction, connection = await hold_row(bench.engine, element.id)
    try:
        with pytest.raises(WriteConflictError):
            async with api_bench.unit():
                await create_named(api_bench, "written first")
                await api_bench.graph.update_element(element.id, description="too late")
    finally:
        await transaction.rollback()
        await connection.close()
    assert await bench.names() == ["n"]  # the whole import, not the row
