"""Fixtures shared by every suite."""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ea.domain.archimate import RelationshipType as R
from ea.domain.documents import Document, DocumentSummary
from ea.domain.errors import (
    DocumentNotFoundError,
    DuplicateDocumentError,
    ElementNotFoundError,
)
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementFilter, GraphView
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService

#: Every suite that needs a timestamp uses this one, so nothing depends on
#: when the tests happen to run.
FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _database_credentials_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the deployment that provides the database credentials.

    `Settings` refuses an empty password outside debug — for Neo4j always, and
    for PostgreSQL since `postgres_enabled` defaults to on (docs/adr/0017) — so
    a suite that builds settings must look like a configured process. Tests
    that are *about* the credentials pass their own values, which take
    precedence over this.

    A password already in the environment wins: that is the integration run,
    which needs the credentials of the database it is about to talk to.
    """
    for variable in ("EA_NEO4J_PASSWORD", "EA_POSTGRES_PASSWORD"):
        if not os.environ.get(variable):
            monkeypatch.setenv(variable, "test-password")


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

    async def relations_of(
        self, element_id: UUID, *, relationship_types: Sequence[R] = ()
    ) -> GraphView:
        """The links touching one element, plus every element they reach."""
        links = tuple(
            link
            for link in self.relationships.values()
            if element_id in (link.source_id, link.target_id)
            and (not relationship_types or link.relationship_type in relationship_types)
        )
        reached = (
            {element_id} | {link.source_id for link in links} | {link.target_id for link in links}
        )
        return GraphView(
            elements=tuple(self.elements[reached_id] for reached_id in reached),
            relationships=links,
        )

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


class InMemoryDocuments:
    """A dictionary pretending to be the `element_documents` table.

    It enforces the one rule the real table enforces with a constraint — one
    file name per element — so a service test sees the same refusal an upload
    against PostgreSQL would get.
    """

    def __init__(self) -> None:
        self.documents: dict[UUID, Document] = {}

    async def add(self, document: Document) -> Document:
        taken = any(
            stored.element_id == document.element_id and stored.filename == document.filename
            for stored in self.documents.values()
        )
        if taken:
            msg = f"{document.filename!r} is already attached to this element"
            raise DuplicateDocumentError(msg)
        self.documents[document.id] = document
        return document

    async def get(self, document_id: UUID) -> Document | None:
        return self.documents.get(document_id)

    async def list_for_element(self, element_id: UUID) -> tuple[DocumentSummary, ...]:
        return tuple(
            document.summary
            for document in sorted(
                (stored for stored in self.documents.values() if stored.element_id == element_id),
                key=lambda stored: (stored.created_at, stored.filename),
            )
        )

    async def replace(self, document: Document) -> Document:
        if document.id not in self.documents:
            raise DocumentNotFoundError(str(document.id))
        self.documents[document.id] = document
        return document

    async def delete(self, document_id: UUID) -> bool:
        return self.documents.pop(document_id, None) is not None

    async def discard_for_element(self, element_id: UUID) -> int:
        doomed = [
            document_id
            for document_id, stored in self.documents.items()
            if stored.element_id == element_id
        ]
        for document_id in doomed:
            del self.documents[document_id]
        return len(doomed)


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def documents() -> InMemoryDocuments:
    return InMemoryDocuments()


@pytest.fixture
def service(repository: InMemoryRepository, documents: InMemoryDocuments) -> ArchitectureService:
    """The service wired to the in-memory graph and to a clock that never moves.

    It is handed the attachments too, because deleting an element has to take
    its documents with it and no foreign key says so — see docs/adr/0017.
    """
    return ArchitectureService(repository, clock=lambda: FIXED_NOW, attachments=documents)


@pytest.fixture
def document_service(documents: InMemoryDocuments, service: ArchitectureService) -> DocumentService:
    """The document use cases over the same in-memory pair, same frozen clock."""
    return DocumentService(documents, service, clock=lambda: FIXED_NOW)
