"""One big file is parsed at a time; the queue is short, and a full queue is a typed 409."""

from __future__ import annotations

import asyncio
import threading

import pytest

from ea.domain.errors import ImportInProgressError
from ea.domain.exchange import ExchangeFormat, ExchangeModel, ImportContent, ImportOptions
from ea.services.architecture import ArchitectureService
from ea.services.exchanges import ExchangeService
from tests.conftest import InMemoryExchange

pytestmark = pytest.mark.asyncio
CONTENT = ImportContent(ExchangeFormat.ARCHIMATE_XML, b"<model/>")


class SlowParser:
    """Stands in for a parsing that takes a while: holds every caller until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.running = 0
        self.peak = 0
        self.calls = 0

    def __call__(self, data: bytes) -> ExchangeModel:
        with self._lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
            self.calls += 1
        self.entered.set()
        self.release.wait(5)
        with self._lock:
            self.running -= 1
        return ExchangeModel()


async def let_the_loop_run() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


async def test_two_parsings_never_overlap_and_a_third_caller_is_refused(
    exchange_store: InMemoryExchange, service: ArchitectureService
) -> None:
    parser = SlowParser()
    importer = ExchangeService(exchange_store, service, parser=parser)
    first = asyncio.create_task(importer.import_exchange(CONTENT, ImportOptions()))
    await asyncio.to_thread(parser.entered.wait, 5)  # the first is inside the parser
    second = asyncio.create_task(importer.import_exchange(CONTENT, ImportOptions()))
    await let_the_loop_run()  # the second waits its turn

    with pytest.raises(ImportInProgressError):
        await importer.import_exchange(CONTENT, ImportOptions())  # the queue is full
    assert parser.calls == 1  # the second has not started parsing

    parser.release.set()
    await first
    await second
    assert parser.peak == 1 and parser.calls == 2


async def test_the_gate_opens_again_after_a_refusal_or_a_failure(
    exchange_store: InMemoryExchange, service: ArchitectureService
) -> None:
    def broken(data: bytes) -> ExchangeModel:
        raise RuntimeError("boom")

    importer = ExchangeService(exchange_store, service, parser=broken)
    for _ in range(4):  # more failures than the queue holds
        with pytest.raises(RuntimeError):
            await importer.import_exchange(CONTENT, ImportOptions())


async def test_a_cancelled_import_keeps_its_place_until_its_thread_has_really_finished(
    exchange_store: InMemoryExchange, service: ArchitectureService
) -> None:
    parser = SlowParser()
    importer = ExchangeService(exchange_store, service, parser=parser)
    first = asyncio.create_task(importer.import_exchange(CONTENT, ImportOptions()))
    await asyncio.to_thread(parser.entered.wait, 5)  # the thread is parsing

    first.cancel()  # the client hung up; the thread cannot be stopped
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(importer.import_exchange(CONTENT, ImportOptions()))
    await let_the_loop_run()

    assert parser.calls == 1 and parser.running == 1  # the second waits for the first thread
    with pytest.raises(ImportInProgressError):  # and the place is still taken in the queue
        await importer.import_exchange(CONTENT, ImportOptions())

    parser.release.set()
    await second
    assert parser.peak == 1 and parser.calls == 2


async def test_a_cancellation_while_waiting_gives_the_place_back(
    exchange_store: InMemoryExchange, service: ArchitectureService
) -> None:
    parser = SlowParser()
    importer = ExchangeService(exchange_store, service, parser=parser)
    first = asyncio.create_task(importer.import_exchange(CONTENT, ImportOptions()))
    await asyncio.to_thread(parser.entered.wait, 5)
    waiting = asyncio.create_task(importer.import_exchange(CONTENT, ImportOptions()))
    await let_the_loop_run()
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting

    parser.release.set()
    await first
    await importer.import_exchange(CONTENT, ImportOptions())  # nothing is left taken
    assert parser.peak == 1
