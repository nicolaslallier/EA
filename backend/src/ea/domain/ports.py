"""The ports the domain declares and the infrastructure implements.

`repositories/` depends on this module; this module depends on nothing. That is
the arrow `services -> domain <- repositories` from `CLAUDE.md`, made explicit.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.documents import Document, DocumentSummary
from ea.domain.model import Element, Relationship
from ea.domain.search import DEFAULT_SEARCH_LIMIT, EmbeddedChunk, Passage


@dataclass(frozen=True, slots=True)
class GraphView:
    """A connected slice of the repository: the nodes and the edges between them.

    Returned by every traversal, so the caller never has to stitch two lists
    together to draw a diagram.
    """

    elements: tuple[Element, ...]
    relationships: tuple[Relationship, ...]

    @property
    def is_empty(self) -> bool:
        return not self.elements


@dataclass(frozen=True, slots=True)
class ElementFilter:
    """The ways the catalogue can be narrowed. All criteria are combined with AND."""

    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    search: str | None = None
    limit: int = 50
    offset: int = 0


class ArchitectureRepository(Protocol):
    """Persistence for the architecture graph.

    Every method is async because the only implementation talks to Neo4j over
    Bolt; a synchronous in-memory double satisfying this protocol would still
    have to declare `async def`.
    """

    async def add_element(self, element: Element) -> Element: ...

    async def get_element(self, element_id: UUID) -> Element | None: ...

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]: ...

    async def count_elements(self, criteria: ElementFilter) -> int: ...

    async def save_element(self, element: Element) -> Element:
        """Persist an already-existing element, overwriting its stored form."""
        ...

    async def delete_element(self, element_id: UUID) -> bool:
        """Remove an element and every relationship attached to it."""
        ...

    async def add_relationship(self, relationship: Relationship) -> Relationship: ...

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None: ...

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Relationship, ...]: ...

    async def delete_relationship(self, relationship_id: UUID) -> bool: ...

    async def relations_of(
        self,
        element_id: UUID,
        *,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """The links attached to one element, with the elements at both ends.

        A `GraphView` rather than a list of relationships because a link is
        unreadable on its own: it stores the ids and the types of its endpoints,
        never their names, which belong to the elements and change without it.
        """
        ...

    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """Everything reachable from an element within `depth` hops, either way."""
        ...

    async def impacted_by(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """What depends on this element — the answer to "what breaks if it fails".

        Follows relationships *against* their direction: if an application
        service serves a business process, the process is impacted by the
        service, not the reverse.
        """
        ...

    async def would_close_a_containment_cycle(self, source_id: UUID, target_id: UUID) -> bool:
        """Whether composing `target` under `source` would make containment cyclic."""
        ...


class ElementAttachments(Protocol):
    """The only thing the architecture graph needs to know about attachments.

    Deleting an element has to take its documents with it, and the two live in
    different stores — one in Neo4j, one in PostgreSQL — so no foreign key can
    do it. That cascade is the *whole* of the coupling, so it is the whole of
    this port: `ArchitectureService` depends on this and never on the document
    repository, which knows how to upload, list and read.
    """

    async def discard_for_element(self, element_id: UUID) -> int:
        """Delete every document attached to an element, and say how many."""
        ...


class DocumentRepository(ElementAttachments, Protocol):
    """Persistence for the markdown attached to elements, and for its index.

    A `DocumentSummary` is what a listing returns and a `Document` what a read
    returns: the difference is the content, and loading a megabyte per row to
    render a list of file names is the mistake the two types prevent.

    The passages are part of this port rather than a second one because they
    are part of the same aggregate: a document's chunks are derived from its
    text and are meaningless beside another version of it. `add` and `replace`
    therefore take both, and write both in one transaction — the two tables are
    in the same database, which is the first time in this codebase that is
    true. See docs/adr/0019.
    """

    async def add(self, document: Document, chunks: Sequence[EmbeddedChunk] = ()) -> Document:
        """Store a new document and its passages, or refuse a name already taken."""
        ...

    async def get(self, document_id: UUID) -> Document | None: ...

    async def list_for_element(self, element_id: UUID) -> tuple[DocumentSummary, ...]:
        """Every document attached to an element, oldest first, without content."""
        ...

    async def replace(self, document: Document, chunks: Sequence[EmbeddedChunk] = ()) -> Document:
        """Overwrite a stored document, and replace its passages with these."""
        ...

    async def delete(self, document_id: UUID) -> bool: ...

    async def all_document_ids(self) -> tuple[UUID, ...]:
        """Every stored document, for the one job that walks the whole corpus.

        Ids and not documents: a reindex reads them back one at a time, so that
        rebuilding the index of a large corpus never holds it all in memory.
        """
        ...

    async def search(
        self,
        embedding: Sequence[float],
        *,
        model: str,
        element_id: UUID | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> tuple[Passage, ...]:
        """The passages closest to a query vector, nearest first.

        `model` is a filter and not a label: comparing vectors produced by two
        different models yields a number that means nothing, so a passage
        embedded by another one is not a worse match, it is not a match.
        """
        ...


class Embedder(Protocol):
    """Whatever turns text into a vector — a hosted service, or a fake in a test.

    Two methods rather than one because several embedding families ask for a
    different prefix on a stored passage than on a query, and a model that
    wants none implements both the same way. Getting that backwards costs
    nothing visible and a good deal of recall, so the port makes it a choice
    someone had to make.
    """

    @property
    def model(self) -> str:
        """The name stored beside every vector this produces."""
        ...

    async def embed_passages(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        """Embed passages for storage, in the order they were given."""
        ...

    async def embed_query(self, text: str) -> tuple[float, ...]:
        """Embed one question, to be compared against stored passages."""
        ...
