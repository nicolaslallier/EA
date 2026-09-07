"""Fixtures shared by every suite."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import ElementNotFoundError
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementFilter, GraphView
from ea.services.architecture import ArchitectureService

#: Every suite that needs a timestamp uses this one, so nothing depends on
#: when the tests happen to run.
FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _neo4j_credentials_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the deployment that provides the database credentials.

    `Settings` refuses an empty Neo4j password outside debug, so a suite that
    builds settings must look like a configured process. Tests that are *about*
    the credentials pass their own values, which take precedence over this.

    A password already in the environment wins: that is the integration run,
    which needs the credentials of the container it is about to talk to.
    """
    if not os.environ.get("EA_NEO4J_PASSWORD"):
        monkeypatch.setenv("EA_NEO4J_PASSWORD", "test-password")


class InMemoryRepository:
    """A dictionary pretending to be a graph. Enough for the service's rules."""

    def __init__(self) -> None:
        self.elements: dict[UUID, Element] = {}
        self.relationships: dict[UUID, Relationship] = {}

    async def add_element(self, element: Element) -> Element:
        self.elements[element.id] = element
        return element

    async def get_element(self, element_id: UUID) -> Element | None:
        return self.elements.get(element_id)

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        return tuple(self.elements.values())[criteria.offset : criteria.offset + criteria.limit]

    async def count_elements(self, criteria: ElementFilter) -> int:
        return len(self.elements)

    async def save_element(self, element: Element) -> Element:
        if element.id not in self.elements:
            raise ElementNotFoundError(str(element.id))
        self.elements[element.id] = element
        return element

    async def delete_element(self, element_id: UUID) -> bool:
        return self.elements.pop(element_id, None) is not None

    async def add_relationship(self, relationship: Relationship) -> Relationship:
        self.relationships[relationship.id] = relationship
        return relationship

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None:
        return self.relationships.get(relationship_id)

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[R] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Relationship, ...]:
        return tuple(self.relationships.values())

    async def delete_relationship(self, relationship_id: UUID) -> bool:
        return self.relationships.pop(relationship_id, None) is not None

    async def neighbourhood(
        self, element_id: UUID, *, depth: int = 1, relationship_types: Sequence[R] = ()
    ) -> GraphView:
        return GraphView(elements=(self.elements[element_id],), relationships=())

    async def impacted_by(
        self, element_id: UUID, *, depth: int = 5, relationship_types: Sequence[R] = ()
    ) -> GraphView:
        return GraphView(elements=(self.elements[element_id],), relationships=())

    async def would_close_a_containment_cycle(self, source_id: UUID, target_id: UUID) -> bool:
        """Walk the stored containment edges from target back to source."""
        if source_id == target_id:
            return True
        seen: set[UUID] = set()
        frontier = [target_id]
        while frontier:
            current = frontier.pop()
            for link in self.relationships.values():
                if link.relationship_type not in (R.COMPOSITION, R.AGGREGATION):
                    continue
                if link.source_id != current or link.target_id in seen:
                    continue
                if link.target_id == source_id:
                    return True
                seen.add(link.target_id)
                frontier.append(link.target_id)
        return False


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def service(repository: InMemoryRepository) -> ArchitectureService:
    """The service wired to the in-memory graph and to a clock that never moves."""
    return ArchitectureService(repository, clock=lambda: FIXED_NOW)
