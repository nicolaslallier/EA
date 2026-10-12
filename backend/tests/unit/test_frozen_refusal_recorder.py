"""The trace of a refused write: one event, bounded, never a flood."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ea.domain.audit import AuditAction
from ea.domain.package_versions import FrozenRefusal
from ea.services.caller import acting_as
from ea.services.frozen_refusals import FrozenRefusalRecorder
from tests.conftest import an_editor

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 10, 12, tzinfo=UTC)


class Sink:
    def __init__(self) -> None:
        self.events: list = []  # type: ignore[type-arg]

    async def __call__(self, events) -> None:  # type: ignore[no-untyped-def]
        self.events.extend(events)


def a_refusal() -> FrozenRefusal:
    return FrozenRefusal(uuid4(), "v1.0.0", "elements", "UPDATE")


async def test_a_refusal_leaves_one_event_about_the_package_that_froze() -> None:
    sink, refusal = Sink(), a_refusal()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    await recorder(refusal)
    (event,) = sink.events
    assert event.action is AuditAction.PACKAGE_WRITE_REFUSED_FROZEN
    assert event.entity_id == refusal.package_id
    assert event.after == {"version": "v1.0.0", "table": "elements", "operation": "UPDATE"}
    assert event.actor_sub == "editor-1"


async def test_past_thirty_a_minute_one_caller_is_only_logged() -> None:
    sink, now = Sink(), [0.0]
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: now[0])
    for _ in range(45):
        await recorder(a_refusal())
    assert len(sink.events) == 30
    now[0] = 61.0
    await recorder(a_refusal())
    assert len(sink.events) == 31


async def test_each_caller_has_its_own_budget() -> None:
    sink = Sink()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    for _ in range(40):
        await recorder(a_refusal())
    with acting_as(an_editor(subject="editor-2", username="two")):
        await recorder(a_refusal())
    assert len(sink.events) == 31


async def test_a_refusal_with_nobody_calling_is_logged_not_recorded(nobody_calling: None) -> None:
    sink = Sink()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    await recorder(a_refusal())
    assert sink.events == []


async def test_a_refusal_whose_package_could_not_be_read_is_recorded_without_an_entity() -> None:
    sink = Sink()
    recorder = FrozenRefusalRecorder(sink, clock=lambda: NOW, monotonic=lambda: 0.0)
    await recorder(FrozenRefusal(None, None, None, None))
    assert sink.events[0].entity_id is None
