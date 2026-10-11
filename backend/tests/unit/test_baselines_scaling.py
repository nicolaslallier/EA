"""A bound on size means nothing if the algorithm inside it is quadratic.

No clock: the work done is counted as the lines executed in `domain/baselines.py`,
which is deterministic. Doubling the model must roughly double that count (a
linear or n log n algorithm gives about 2 to 2.3; a scan per element gives 4).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from types import FrameType
from typing import Any
from uuid import UUID

from ea.domain import baselines
from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.baselines import BoxSnapshot, ModelSnapshot, diff_snapshots, plan_restore
from ea.domain.model import Element, Relationship

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
SMALL, LARGE = 600, 1200
MAX_GROWTH = 3.0  # linear is 2, n log n a little more, a scan per element is 4


def lines_executed(work: Callable[[], Any]) -> int:
    """Lines run in the module under test while `work` runs."""
    target = baselines.__file__
    count = 0

    def local(frame: FrameType, event: str, arg: object) -> Any:
        nonlocal count
        if event == "line":
            count += 1
        return local

    def global_(frame: FrameType, event: str, arg: object) -> Any:
        return local if frame.f_code.co_filename == target else None

    sys.settrace(global_)
    try:
        work()
    finally:
        sys.settrace(None)
    return count


def grows_linearly(build: Callable[[int], Callable[[], Any]]) -> None:
    small, large = lines_executed(build(SMALL)), lines_executed(build(LARGE))
    assert large / small < MAX_GROWTH, f"{small} -> {large} lines when the model doubled"


def nodes(n: int, prefix: str = "n") -> list[Element]:
    return [Element.create(element_type=E.NODE, name=f"{prefix}{i}", now=NOW) for i in range(n)]


def links(elements: list[Element], per_element: int) -> list[Relationship]:
    count = len(elements)
    return [
        Relationship.between(
            R.ASSOCIATION, elements[i], elements[(i + 1 + k) % count], now=NOW, name=f"{i}-{k}"
        )
        for i in range(count)
        for k in range(per_element)
    ]


def plan(snapshot: ModelSnapshot, current: ModelSnapshot, **more: Any) -> None:
    plan_restore(snapshot, current, keep_new=False, packages_present=frozenset(), **more)


def test_deleting_many_elements_with_their_links_and_boxes() -> None:
    def build(n: int) -> Callable[[], Any]:
        kept, gone = nodes(1, "kept"), nodes(n, "gone")
        everything = [*kept, *gone]
        diagram = UUID(int=1)
        boxes = tuple(BoxSnapshot(diagram, e.id, 0, 0, 132, 46) for e in gone)
        current = ModelSnapshot(tuple(everything), tuple(links(everything, 3)), boxes)
        return lambda: plan(ModelSnapshot(tuple(kept), ()), current)

    grows_linearly(build)


def test_a_hub_with_thousands_of_links() -> None:
    def build(n: int) -> Callable[[], Any]:
        hub, spokes = nodes(1, "hub")[0], nodes(n, "spoke")
        spokes_links = [Relationship.between(R.ASSOCIATION, s, hub, now=NOW) for s in spokes]
        current = ModelSnapshot((hub, *spokes), tuple(spokes_links))
        return lambda: plan(ModelSnapshot(tuple(spokes), ()), current)

    grows_linearly(build)


def test_a_chain_of_renames_the_length_of_the_model() -> None:
    def build(n: int) -> Callable[[], Any]:
        original = nodes(n)
        shifted = [replace(e, name=f"n{(i + 1) % n}") for i, e in enumerate(original)]
        return lambda: plan(ModelSnapshot(tuple(original), ()), ModelSnapshot(tuple(shifted), ()))

    grows_linearly(build)


def test_every_element_protected() -> None:
    def build(n: int) -> Callable[[], Any]:
        original = nodes(n)
        edited = [replace(e, name=f"{e.name}-late") for e in original]
        everyone = frozenset(e.id for e in original)
        return lambda: plan(
            ModelSnapshot(tuple(original), tuple(links(original, 2))),
            ModelSnapshot(tuple(edited), ()),
            protected_ids=everyone,
        )

    grows_linearly(build)


def test_a_diff_of_a_whole_model_changing() -> None:
    def build(n: int) -> Callable[[], Any]:
        original = nodes(n)
        edited = [replace(e, name=f"{e.name}-late") for e in original]
        return lambda: diff_snapshots(
            ModelSnapshot(tuple(original), tuple(links(original, 2))),
            ModelSnapshot(tuple(edited), ()),
        )

    grows_linearly(build)
