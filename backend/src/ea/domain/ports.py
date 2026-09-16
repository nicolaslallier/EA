"""The ports the domain declares and the infrastructure implements.

`repositories/` depends on this module; this module depends on nothing. That is
the arrow `services -> domain <- repositories` from `CLAUDE.md`, made explicit.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.auth import Caller
from ea.domain.documents import Document, DocumentSummary
from ea.domain.files import FileDetails, FileListing, FileMetadata, StoredFile
from ea.domain.model import Element, Relationship
from ea.domain.search import DEFAULT_SEARCH_LIMIT, EmbeddedChunk, Passage

if TYPE_CHECKING:
    # Only for annotations: `diagrams` imports `GraphView` from here.
    from ea.domain.diagrams import Diagram, DiagramNode


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

    Every method is async because the only implementation talks to PostgreSQL over
    asyncpg; a synchronous in-memory double satisfying this protocol would still
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

    async def view_of(self, element_ids: Sequence[UUID]) -> GraphView:
        """The elements among these ids that exist, and the links with both ends among them.

        What a saved diagram shows (docs/adr/0031): an id the graph no longer
        holds is simply absent from the answer, never an error.
        """
        ...


class DocumentRepository(Protocol):
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


class DiagramRepository(Protocol):
    """Persistence for saved diagrams — see docs/adr/0031."""

    async def list_all(self) -> tuple[Diagram, ...]:
        """Every diagram, sorted by name, each with its node count."""
        ...

    async def add(self, diagram: Diagram) -> Diagram:
        """Store a new diagram, or refuse a name already taken."""
        ...

    async def get(self, diagram_id: UUID) -> Diagram | None: ...

    async def nodes_of(self, diagram_id: UUID) -> tuple[DiagramNode, ...]: ...

    async def save(self, diagram: Diagram) -> Diagram:
        """Overwrite the name, description and update time of a stored diagram."""
        ...

    async def delete(self, diagram_id: UUID) -> bool: ...

    async def replace_layout(
        self, diagram_id: UUID, nodes: Sequence[DiagramNode], *, now: datetime
    ) -> bool:
        """Replace every node of a diagram in one transaction; `False` if it is gone."""
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


class IpamRepository(Protocol):
    """The three reads the IP address management needs and the catalogue lacks.

    An address is a *property* of an element (docs/adr/0020), so none of these
    is a new store: they are the queries `ElementFilter` cannot express, which
    filters on type, layer and name and never on an attribute.

    They are declared apart from `ArchitectureRepository` because they are a
    different use case, and satisfied by the same PostgreSQL class, which implements
    both — structural typing, so neither protocol has to know about the other.
    """

    async def networks(self) -> tuple[Element, ...]:
        """Every element declaring a prefix, whatever its scope."""
        ...

    async def addressed_elements(self) -> tuple[Element, ...]:
        """Every element carrying an IP address.

        The whole inventory rather than a page: an address is only meaningful
        against the others — which subnet holds it, whether it is free — and a
        page of them would answer none of those questions. It is bounded by how
        many machines are modelled, not by how large the graph is.
        """
        ...

    async def element_at(self, address: str, *, vrf: str) -> Element | None:
        """The one element answering on that address in that scope, if any.

        One and not many: `(vrf, ip_address)` is unique in PostgreSQL — the
        partial unique index `uq_elements_vrf_ip_address` — which is the whole
        reason an element holds a single address rather than a list — see
        `domain/ipam.py`.
        """
        ...


class AccessTokenVerifier(Protocol):
    """Whatever proves who a bearer token belongs to — Keycloak's JWKS in production."""

    async def verify(self, token: str) -> Caller:
        """The caller the token proves, or `NotAuthenticatedError`."""
        ...


class FileMetadataRepository(Protocol):
    """What PostgreSQL knows about the objects of the bucket — see docs/adr/0039.

    It is a catalogue *of* a store and not the store: the bytes are in MinIO,
    which stays the source of truth for what exists. Every method is keyed by
    `object_key`, because that is the one name both sides share.

    The write side is two methods and not one, because an upload and a
    reconcile know different things. `record` is what an upload learned — it
    read the bytes, so it has a digest and a caller. `note_seen` is what a
    listing learned — a size and a modification time, and nothing about who
    wrote the object or what is in it. Folding them into one call would mean a
    reconcile blanking the uploader of every file the API had stored.

    Neither touches the title, the description or the tags: what a person wrote
    about a file survives that file being replaced. `describe` is the only way
    those change, and it is the only thing an editor can change here.
    """

    async def record(self, metadata: FileMetadata) -> FileMetadata:
        """Store what an upload learned about an object, keeping any description."""
        ...

    async def note_seen(self, stored: StoredFile, *, now: datetime) -> FileMetadata:
        """Store what a listing learned, keeping the description, the uploader and the digest.

        A stored digest is dropped when the object's `etag` says it was replaced
        since: it described bytes that are no longer there, and a digest that is
        quietly wrong is worse than none.
        """
        ...

    async def get(self, key: str) -> FileMetadata | None: ...

    async def for_keys(self, keys: Sequence[str]) -> dict[str, FileMetadata]:
        """What is known about each of these objects, keyed by path.

        A batch and not a call per row: a folder of a thousand files is one
        query, which is the reason this table mirrors the bucket's own facts.
        """
        ...

    async def describe(
        self, key: str, details: FileDetails, *, now: datetime
    ) -> FileMetadata | None:
        """Replace what a person wrote about a file; `None` when no row holds it."""
        ...

    async def forget(self, key: str) -> bool:
        """Drop the row for an object that is no longer in the bucket."""
        ...

    async def all_keys(self) -> tuple[str, ...]:
        """Every object this catalogue holds a row for — the reconcile walks these."""
        ...

    async def with_digest(self, sha256: str) -> tuple[FileMetadata, ...]:
        """Every file whose content is these exact bytes — the same file under two names."""
        ...


class ObjectStore(Protocol):
    """Where files are kept — MinIO in a deployment, a dict in the unit tests.

    `stat` answers `None` for a missing key rather than raising, because
    "is something already there?" is a question, not a failure. `open`
    raises `StoredFileNotFoundError`, because there it is one. See docs/adr/0036.
    """

    async def list_folder(self, prefix: str, *, limit: int) -> FileListing: ...

    async def stat(self, key: str) -> StoredFile | None: ...

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredFile: ...

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]: ...

    def walk(self, prefix: str = "") -> AsyncIterator[StoredFile]:
        """Every object under `prefix`, however deep and however many.

        An iterator and not a `FileListing`: this is what a reconcile walks
        (docs/adr/0039), and it is the one read here with no cap on it — a cap
        would silently make the catch-up skip the rest of the bucket. It is
        also why it is not `list_folder(recursive=True)`: a listing is a folder
        a person is looking at, bounded on purpose.
        """
        ...

    async def delete(self, key: str) -> None: ...
