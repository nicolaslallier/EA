"""A place is a lease in a set, not a count that can drift (docs/adr/0050).

A counter incremented on one path and decremented on another is wrong the day one path forgets,
and a counter stuck at its maximum refuses everybody for good. Here every figure is computed from
the set of live leases; a lease leaves the set by identity, once, however the block ends; and one
that is never given back expires by itself — which is counted, because it proves a leak.
The clock is injected: nothing waits.
"""

from __future__ import annotations

import asyncio
import random

import pytest

from ea.domain.errors import ImportInProgressError
from ea.services.exchange_admission import Admission

pytestmark = pytest.mark.asyncio


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def admission(clock: Clock, limit: int = 2, per_caller: int = 1, lease: float = 100.0) -> Admission:
    return Admission(limit, per_caller=per_caller, lease_seconds=lease, clock=clock)


async def test_an_exception_before_the_body_takes_the_lease_out() -> None:
    adm = admission(Clock())

    with pytest.raises(RuntimeError):
        async with adm.lease("a"):
            raise RuntimeError("before anything was done")

    assert adm.in_flight == 0 and adm.callers == {}


@pytest.mark.parametrize("cancelled_at", range(4))
async def test_a_cancellation_at_any_await_takes_the_lease_out(cancelled_at: int) -> None:
    adm = admission(Clock())
    reached = asyncio.Event()
    gates = [asyncio.Event() for _ in range(4)]

    async def work() -> None:
        async with adm.lease("a"):
            for number, gate in enumerate(gates):
                if number == cancelled_at:
                    reached.set()
                await gate.wait()

    task = asyncio.create_task(work())
    for number, gate in enumerate(gates):
        if number < cancelled_at:
            await asyncio.sleep(0)
            gate.set()
    await reached.wait()
    assert adm.in_flight == 1
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert adm.in_flight == 0 and adm.callers == {}


async def test_taking_a_lease_out_twice_changes_nothing_and_nothing_goes_negative() -> None:
    adm = admission(Clock())
    async with adm.lease("a") as first, adm.lease("b"):
        adm._finish(first)
        adm._finish(first)
        assert adm.in_flight == 1 and adm.callers == {"b": 1}
    # (the block's own exit is a third time)
    assert adm.in_flight == 0 and adm.callers == {}
    async with adm.lease("a"), adm.lease("b"):  # and the places are really free again
        assert adm.in_flight == 2


async def test_a_lease_that_is_never_given_back_expires_and_is_counted() -> None:
    clock = Clock()
    adm = admission(clock, limit=1, lease=100.0)
    leaked = adm.lease("a")
    await leaked.__aenter__()  # the path that forgets to leave the block
    with pytest.raises(ImportInProgressError):
        async with adm.lease("b"):
            pytest.fail("the only place is taken")

    clock.now += 101  # past the deadline
    async with adm.lease("b"):
        assert adm.in_flight == 1 and adm.callers == {"b": 1}

    assert adm.expired_leases == 1


async def test_a_lease_not_yet_due_is_not_taken_back() -> None:
    clock = Clock()
    adm = admission(clock, limit=1, lease=100.0)
    async with adm.lease("a"):
        clock.now += 99
        with pytest.raises(ImportInProgressError):
            async with adm.lease("b"):
                pytest.fail("not due")
    assert adm.expired_leases == 0


async def test_the_ceilings_are_computed_from_the_set_of_leases() -> None:
    adm = admission(Clock(), limit=3, per_caller=2)
    async with adm.lease("a"), adm.lease("a"), adm.lease("b"):
        assert adm.callers == {"a": 2, "b": 1} and adm.in_flight == 3
        with pytest.raises(ImportInProgressError):  # the global one
            async with adm.lease("c"):
                pytest.fail("full")
        adm._leases.clear()
        assert adm.in_flight == 0 and adm.callers == {}
        async with adm.lease("c"):
            pass


async def test_a_caller_over_their_own_ceiling_is_refused_while_others_enter() -> None:
    adm = admission(Clock(), limit=4, per_caller=2)
    async with adm.lease("a"), adm.lease("a"):
        with pytest.raises(ImportInProgressError):
            async with adm.lease("a"):
                pytest.fail("a third place for a")
        async with adm.lease("b"):
            assert adm.in_flight == 3


async def test_a_lease_whose_thread_has_not_ended_stays_until_it_has() -> None:
    adm = admission(Clock())
    loop = asyncio.get_running_loop()
    work: asyncio.Future[None] = loop.create_future()

    async with adm.lease("a") as lease:
        lease.hold(work)
    assert adm.in_flight == 1  # the block is over, the analysis is not

    work.set_result(None)
    await asyncio.sleep(0)
    assert adm.in_flight == 0


async def test_an_expired_lease_whose_analysis_still_runs_stays_counted_until_it_ends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    clock = Clock()
    adm = admission(clock, limit=1, per_caller=1, lease=100.0)
    work: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    async with adm.lease("a") as lease:
        lease.hold(work)

    clock.now += 101  # past the deadline, the thread still running
    with caplog.at_level(logging.WARNING, logger="ea"):
        for _ in range(3):
            with pytest.raises(ImportInProgressError):  # the global ceiling still holds...
                async with adm.lease("b"):
                    pytest.fail("the analysis is still running")
    assert adm.callers == {"a": 1} and adm.expired_leases == 0  # ...and so does a's own
    assert caplog.text.count("overdue") == 1  # said once, not at every attempt

    work.set_result(None)
    await asyncio.sleep(0)
    assert adm.in_flight == 0
    async with adm.lease("b"):
        pass


async def test_an_expired_lease_with_only_finished_work_is_taken_back() -> None:
    clock = Clock()
    adm = admission(clock, limit=1, lease=100.0)
    done: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    done.set_result(None)
    leaked = adm.lease("a")
    entered = await leaked.__aenter__()
    entered.hold(done)  # (its thread ended, its block never did)

    clock.now += 101
    async with adm.lease("b"):
        pass

    assert adm.expired_leases == 1


async def test_a_thousand_random_cycles_leave_the_set_empty() -> None:
    chance = random.Random(2026)
    adm = admission(Clock(), limit=5, per_caller=2)

    async def cycle(number: int) -> None:
        key = f"caller-{chance.randrange(4)}"
        fate = chance.choice(["fine", "raise", "cancel", "refused"])
        try:
            async with adm.lease(key):
                if fate == "raise":
                    raise RuntimeError("boom")
                if fate == "cancel":
                    await asyncio.Event().wait()
                await asyncio.sleep(0)
        except (RuntimeError, ImportInProgressError):
            pass

    for number in range(1_000):
        task = asyncio.create_task(cycle(number))
        await asyncio.sleep(0)
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert adm.in_flight == 0 and adm.callers == {} and adm.expired_leases == 0


async def test_the_one_way_to_take_a_place_is_the_context_manager() -> None:
    adm = admission(Clock())

    assert not any(hasattr(adm, name) for name in ("admit", "acquire", "release"))
    assert callable(adm.lease)
