"""An event's moment is taken inside the write it describes (docs/adr/0046).

Taken before the transaction opens, a slow commit lets a *later* moment become
visible first, and a reader paging by `(at, id)` has already gone past the
earlier one. Taking it inside narrows the window to the write itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.query import ElementQuery
from ea.services.architecture import ArchitectureService
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService
from tests.conftest import (
    FIXED_NOW,
    InMemoryDiagrams,
    InMemoryDocuments,
    InMemoryPackages,
    InMemoryRepository,
    InMemorySavedQueries,
)

pytestmark = pytest.mark.asyncio


class Tape:
    """Records when the clock is read relative to a store write."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def clock(self) -> Any:
        self.calls.append("clock")
        return FIXED_NOW

    def watch(self, store: object, method: str) -> None:
        original: Callable[..., Awaitable[Any]] = getattr(store, method)

        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            self.calls.append("write-start")
            try:
                return await original(*args, **kwargs)
            finally:
                self.calls.append("write-end")

        setattr(store, method, wrapped)

    def moment_is_inside_the_write(self) -> bool:
        start = self.calls.index("write-start")
        end = self.calls.index("write-end")
        return "clock" in self.calls[start:end]


async def test_a_created_element_is_dated_inside_its_write(repository: InMemoryRepository) -> None:
    tape = Tape()
    tape.watch(repository, "add_element")

    await ArchitectureService(repository, clock=tape.clock).create_element(
        element_type=E.NODE, name="n"
    )

    assert tape.moment_is_inside_the_write(), tape.calls


async def test_a_created_link_is_dated_inside_its_write(repository: InMemoryRepository) -> None:
    service = ArchitectureService(repository, clock=lambda: FIXED_NOW)
    left = await service.create_element(element_type=E.NODE, name="a")
    right = await service.create_element(element_type=E.NODE, name="b")
    tape = Tape()
    tape.watch(repository, "add_relationship")

    await ArchitectureService(repository, clock=tape.clock).connect(
        relationship_type=R.ASSOCIATION, source_id=left.id, target_id=right.id
    )

    assert tape.moment_is_inside_the_write(), tape.calls


async def test_an_attached_document_is_dated_inside_its_write(
    repository: InMemoryRepository, documents: InMemoryDocuments
) -> None:
    graph = ArchitectureService(repository, clock=lambda: FIXED_NOW)
    element = await graph.create_element(element_type=E.NODE, name="a")
    tape = Tape()
    tape.watch(documents, "add")

    await DocumentService(documents, graph, clock=tape.clock).attach_text(
        element.id, filename="a.md", content="# A\n"
    )

    assert tape.moment_is_inside_the_write(), tape.calls


async def test_a_created_diagram_is_dated_inside_its_write(
    repository: InMemoryRepository, diagrams: InMemoryDiagrams
) -> None:
    graph = ArchitectureService(repository, clock=lambda: FIXED_NOW)
    tape = Tape()
    tape.watch(diagrams, "add")

    await DiagramService(diagrams, graph, clock=tape.clock).create(name="Vue")

    assert tape.moment_is_inside_the_write(), tape.calls


async def test_a_created_package_is_dated_inside_its_write(packages: InMemoryPackages) -> None:
    tape = Tape()
    tape.watch(packages, "add")

    await PackageService(packages, clock=tape.clock).create(name="P")

    assert tape.moment_is_inside_the_write(), tape.calls


async def test_a_saved_query_is_dated_inside_its_write(
    repository: InMemoryRepository,
    packages: InMemoryPackages,
    saved_queries: InMemorySavedQueries,
) -> None:
    graph = ArchitectureService(repository, clock=lambda: FIXED_NOW)
    tape = Tape()
    tape.watch(saved_queries, "add")

    await SavedQueryService(saved_queries, packages, graph, clock=tape.clock).create(
        name="q", query=ElementQuery()
    )

    assert tape.moment_is_inside_the_write(), tape.calls
