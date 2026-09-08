"""Fixtures shared by every suite."""

from __future__ import annotations

import math
import os
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

import pytest

from ea.domain.archimate import RelationshipType as R
from ea.domain.documents import Document, DocumentSummary
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    DocumentNotFoundError,
    DuplicateDocumentError,
    ElementNotFoundError,
)
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, read_vrf
from ea.domain.model import Element, Relationship
from ea.domain.ports import ElementFilter, GraphView
from ea.domain.search import (
    DEFAULT_SEARCH_LIMIT,
    EMBEDDING_DIMENSIONS,
    EmbeddedChunk,
    Passage,
)
from ea.services.architecture import ArchitectureService
from ea.services.documents import DocumentService
from ea.services.indexing import DocumentIndexer
from ea.services.ipam import IpamService

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
        self._refuse_a_taken_address(element)
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
        self._refuse_a_taken_address(element)
        self.elements[element.id] = element
        return element

    def _refuse_a_taken_address(self, element: Element) -> None:
        """Stand in for the `(p_vrf, p_ip_address)` uniqueness constraint.

        Reproduced rather than skipped, for the same reason the document double
        reproduces its own: it is the rule that survives two agents allocating
        at the same instant, and a double that ignored it would let a service
        test pass on a graph the real one would refuse — see docs/adr/0020.
        """
        address = element.properties.get(ADDRESS_PROPERTY)
        if not address:
            return
        scope = read_vrf(element.properties)
        clash = next(
            (
                stored
                for stored in self.elements.values()
                if stored.id != element.id
                and stored.properties.get(ADDRESS_PROPERTY) == address
                and read_vrf(stored.properties) == scope
            ),
            None,
        )
        if clash is not None:
            msg = f"{address} is already assigned to {clash.name!r} in VRF {scope!r}"
            raise AddressAlreadyAssignedError(msg)

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

    # --- IPAM: the queries `ElementFilter` cannot express (docs/adr/0020) ---

    async def networks(self) -> tuple[Element, ...]:
        return tuple(
            element for element in self.elements.values() if element.properties.get(PREFIX_PROPERTY)
        )

    async def addressed_elements(self) -> tuple[Element, ...]:
        return tuple(
            element
            for element in self.elements.values()
            if element.properties.get(ADDRESS_PROPERTY)
        )

    async def element_at(self, address: str, *, vrf: str) -> Element | None:
        return next(
            (
                element
                for element in self.elements.values()
                if element.properties.get(ADDRESS_PROPERTY) == address
                and read_vrf(element.properties) == vrf
            ),
            None,
        )

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
        self.chunks: dict[UUID, tuple[EmbeddedChunk, ...]] = {}

    async def add(self, document: Document, chunks: Sequence[EmbeddedChunk] = ()) -> Document:
        taken = any(
            stored.element_id == document.element_id and stored.filename == document.filename
            for stored in self.documents.values()
        )
        if taken:
            msg = f"{document.filename!r} is already attached to this element"
            raise DuplicateDocumentError(msg)
        self.documents[document.id] = document
        self.chunks[document.id] = tuple(chunks)
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

    async def replace(self, document: Document, chunks: Sequence[EmbeddedChunk] = ()) -> Document:
        if document.id not in self.documents:
            raise DocumentNotFoundError(str(document.id))
        self.documents[document.id] = document
        self.chunks[document.id] = tuple(chunks)
        return document

    async def delete(self, document_id: UUID) -> bool:
        """The passages go with the document — here by hand, in PostgreSQL by
        the foreign key `element_documents` could never have."""
        self.chunks.pop(document_id, None)
        return self.documents.pop(document_id, None) is not None

    async def discard_for_element(self, element_id: UUID) -> int:
        doomed = [
            document_id
            for document_id, stored in self.documents.items()
            if stored.element_id == element_id
        ]
        for document_id in doomed:
            del self.documents[document_id]
            self.chunks.pop(document_id, None)
        return len(doomed)

    async def all_document_ids(self) -> tuple[UUID, ...]:
        return tuple(
            document.id
            for document in sorted(self.documents.values(), key=lambda stored: stored.created_at)
        )

    async def search(
        self,
        embedding: Sequence[float],
        *,
        model: str,
        element_id: UUID | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> tuple[Passage, ...]:
        """Cosine similarity in Python — the same ordering pgvector computes.

        The `model` filter is reproduced rather than skipped: it is the rule
        that makes a half-finished reindex return too little instead of
        nonsense, and a double that ignored it would hide the day it broke.
        """
        hits = [
            Passage(
                document_id=document.id,
                element_id=document.element_id,
                filename=document.filename,
                heading_path=chunk.heading_path,
                text=chunk.text,
                score=_cosine(embedding, chunk.embedding),
            )
            for document in self.documents.values()
            for chunk in self.chunks.get(document.id, ())
            if chunk.model == model and (element_id is None or document.element_id == element_id)
        ]
        hits.sort(key=lambda passage: passage.score, reverse=True)
        return tuple(hits[:limit])


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    product = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return product / norms if norms else 0.0


class FakeEmbedder:
    """An embedding service that never leaves the process.

    It hashes words into the vector and normalises, so two texts sharing words
    come out close and two that share none come out far apart. That is nowhere
    near a real model, and it is exactly enough to prove the wiring: that the
    heading trail is what gets embedded, that a query reaches the right method,
    and that the model name follows every vector into the store.
    """

    def __init__(self, *, model: str = "fake-embed") -> None:
        self._model = model
        self.passages: list[str] = []
        self.queries: list[str] = []
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return EMBEDDING_DIMENSIONS

    def rename(self, model: str) -> None:
        """Stand in for a deployment that changed model without reindexing."""
        self._model = model

    def reset(self) -> None:
        self.passages, self.queries, self.calls = [], [], 0

    async def embed_passages(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        self.calls += 1
        self.passages.extend(texts)
        return tuple(_bag_of_words(text) for text in texts)

    async def embed_query(self, text: str) -> tuple[float, ...]:
        self.calls += 1
        self.queries.append(text)
        return _bag_of_words(text)


def _bag_of_words(text: str) -> tuple[float, ...]:
    weights = [0.0] * EMBEDDING_DIMENSIONS
    for word in re.findall(r"\w+", text.lower()):
        weights[hash(word) % EMBEDDING_DIMENSIONS] += 1.0
    norm = math.sqrt(sum(weight * weight for weight in weights)) or 1.0
    return tuple(weight / norm for weight in weights)


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
def ipam(repository: InMemoryRepository, service: ArchitectureService) -> IpamService:
    """The IP use cases over the same graph double, which answers both ports.

    `InMemoryRepository` satisfies `IpamRepository` as well as
    `ArchitectureRepository`, exactly as the Neo4j class does — an address is
    an attribute of an element, not a second store (docs/adr/0020).
    """
    return IpamService(service, repository)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def indexer(embedder: FakeEmbedder) -> DocumentIndexer:
    return DocumentIndexer(embedder)


@pytest.fixture
def document_service(
    documents: InMemoryDocuments, service: ArchitectureService, indexer: DocumentIndexer
) -> DocumentService:
    """The document use cases over the same in-memory pair, same frozen clock."""
    return DocumentService(documents, service, clock=lambda: FIXED_NOW, indexer=indexer)


@pytest.fixture
def document_service_without_an_index(
    documents: InMemoryDocuments, service: ArchitectureService
) -> DocumentService:
    """The deployment with `EA_EMBEDDINGS_ENABLED` off: it stores, it cannot find."""
    return DocumentService(documents, service, clock=lambda: FIXED_NOW)
