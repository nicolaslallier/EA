"""The ports the domain declares and the infrastructure implements.

`repositories/` depends on this module; this module depends on nothing. That is
the arrow `services -> domain <- repositories` from `CLAUDE.md`, made explicit.
"""

from __future__ import annotations

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterable,
    Mapping,
    Sequence,
)
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from ea.domain.analytics import Dimension, RawGroup
from ea.domain.archimate import ElementType, Layer, RelationshipType
from ea.domain.audit import AuditFilter, AuditPage
from ea.domain.auth import Caller
from ea.domain.documents import Document, DocumentSummary
from ea.domain.files import FileDetails, FileListing, FileMetadata, StoredFile
from ea.domain.model import Element, Relationship
from ea.domain.packages import Package, PackageCounts
from ea.domain.query import ElementQuery, PropertyTest, QueryOrder, RelationTest
from ea.domain.saved_queries import SavedQuery, SavedQuerySummary
from ea.domain.search import DEFAULT_SEARCH_LIMIT, EmbeddedChunk, Passage

if TYPE_CHECKING:
    # Only for annotations: `diagrams` imports `GraphView` from here.
    from ea.domain.audit import AuditEvent, AuditOf
    from ea.domain.baselines import Baseline, ModelSnapshot, RestorePlan
    from ea.domain.diagrams import Diagram, DiagramNode
    from ea.domain.exchange import ExportedView, ExportScope, PackageNode


@dataclass(slots=True)
class Transaction:
    """The handle of a shared transaction: set `commit` to `False` to roll it back instead."""

    commit: bool = True


@dataclass(frozen=True, slots=True)
class RowRefusal:
    """A row of an import the rules refused: its position in the rows given, and why."""

    index: int
    error: Exception


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
class Held:
    """How many things a package holds, and the first few of them (`AUDIT_LISTED_IDS`)."""

    count: int = 0
    ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class ElementChange:
    """An element as the repository found it under its lock, and as the patch left it."""

    before: Element
    after: Element


#: A rule the repository applies to an element it holds locked, in the writing
#: transaction; it raises to refuse. A decision taken on a copy read earlier
#: would miss a lock put on the element in between.
ElementGuard = Callable[[Element], None]


@dataclass(frozen=True, slots=True)
class EndsReview:
    """What a link change does to the elements at its two ends (docs/adr/0048).

    `change` is applied to each end read under its row lock — it raises to refuse
    and returns the element as it must be stored — and `audit` says what the
    journal records for an end it really changed. Both are pure and run in the
    transaction that writes the link.
    """

    change: Callable[[Element], Element]
    audit: Callable[[ElementChange], Sequence[AuditEvent]]

    def apply(self, ends: Iterable[Element]) -> list[ElementChange]:
        """Every end, in id order, once each, as it must be stored.

        `ends` are the elements as read under their locks. A link is a change to
        both of its ends: each gets a new revision, and `change` decides what
        else (an approved end goes back to review). `change` raising refuses the
        whole link.
        """
        return [
            ElementChange(before, replace(self.change(before), revision=before.revision + 1))
            for before in sorted({e.id: e for e in ends}.values(), key=lambda e: e.id)
        ]


@dataclass(frozen=True, slots=True)
class ElementRemoval:
    """What a deletion takes: the element, its links, and what the foreign keys cascade.

    Read inside the transaction that deletes, so it is exactly what went.
    """

    element: Element
    #: Exact, while `relationships` holds only the first `AUDIT_LISTED_IDS`: an
    #: event about a hub of ten thousand links must not weigh megabytes.
    relationship_count: int = 0
    relationships: tuple[Relationship, ...] = ()
    documents: Held = Held()
    diagrams: Held = Held()


@dataclass(frozen=True, slots=True)
class PackageRemoval:
    """What deleting a package moves, read in the transaction that deletes it."""

    package: Package
    into: UUID | None
    sub_packages: Held = Held()
    elements: Held = Held()
    diagrams: Held = Held()


@dataclass(frozen=True, slots=True)
class ElementFilter:
    """The ways the catalogue can be narrowed. All criteria are combined with AND.

    What a repository compiles. `ElementQuery` is the validated form every
    adapter builds; this narrow struct is what it becomes, and the one place
    the SQL reads it is `architecture_store._matching`.
    """

    element_types: tuple[ElementType, ...] = ()
    layers: tuple[Layer, ...] = ()
    search: str | None = None
    package_id: UUID | None = None
    include_subpackages: bool = True
    root_only: bool = False
    properties: tuple[PropertyTest, ...] = ()
    relations: tuple[RelationTest, ...] = ()
    order: QueryOrder = QueryOrder.NAME
    limit: int = 50
    offset: int = 0
    #: Read the model as it is on this day (docs/adr/0060); `None` filters nothing.
    as_of: date | None = None

    @classmethod
    def from_query(cls, query: ElementQuery) -> ElementFilter:
        return cls(
            element_types=query.element_types,
            layers=query.layers,
            search=query.text,
            package_id=query.package_id,
            include_subpackages=query.include_subpackages,
            root_only=query.root_only,
            properties=query.properties,
            relations=query.relations,
            order=query.order,
            limit=query.limit,
            offset=query.offset,
        )


class ArchitectureRepository(Protocol):
    """Persistence for the architecture graph.

    Every method is async because the only implementation talks to PostgreSQL over
    asyncpg; a synchronous in-memory double satisfying this protocol would still
    have to declare `async def`.
    """

    async def add_element(self, element: Element, *, audit: AuditOf[Element]) -> Element:
        """Store a new element; `audit` is inserted in the same transaction."""
        ...

    async def get_element(self, element_id: UUID) -> Element | None: ...

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]: ...

    async def count_elements(self, criteria: ElementFilter) -> int: ...

    async def update_element(
        self,
        element_id: UUID,
        change: Callable[[Element], Element],
        *,
        audit: AuditOf[ElementChange],
    ) -> Element:
        """Apply `change` — a pure function — to the element read under its lock.

        Applied in the writer's own transaction, so a concurrent writer's change to
        another field is never undone by a copy read earlier. Only the columns the
        patch really changes are written; a patch that changes nothing writes
        nothing, not even an event (`audit` is not called). `audit` may be a
        function of the `ElementChange`, called in the transaction.
        """
        ...

    async def transition_element(
        self,
        element_id: UUID,
        change: Callable[[Element], Element],
        *,
        audit: AuditOf[ElementChange],
    ) -> Element:
        """Move an element through review, deciding on the row read under its lock.

        `change` receives that row — with its state, its revision and the subject
        that last changed it — and returns the element after the transition, or
        raises to refuse. Only the review columns are written.
        """
        ...

    async def delete_element(
        self,
        element_id: UUID,
        *,
        audit: AuditOf[ElementRemoval],
        guard: ElementGuard | None = None,
        ends: EndsReview | None = None,
    ) -> bool:
        """Remove an element and every relationship attached to it.

        `audit` is written only if a row was deleted; `False` writes nothing.
        `guard` sees the element under its lock, before anything is removed; `ends`
        reviews the elements at the other ends of the links that go with it.
        """
        ...

    async def add_relationship(
        self,
        relationship: Relationship,
        *,
        audit: AuditOf[Relationship],
        ends: EndsReview | None = None,
    ) -> Relationship:
        """Store a link; `ends` reviews its two elements in the same transaction."""
        ...

    async def get_relationship(self, relationship_id: UUID) -> Relationship | None: ...

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
        as_of: date | None = None,
    ) -> tuple[Relationship, ...]: ...

    async def delete_relationship(
        self,
        relationship_id: UUID,
        *,
        audit: AuditOf[Relationship],
        ends: EndsReview | None = None,
    ) -> bool: ...

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
        as_of: date | None = None,
    ) -> GraphView:
        """Everything reachable from an element within `depth` hops, either way."""
        ...

    async def impacted_by(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
        as_of: date | None = None,
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

    async def add(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: AuditOf[Document],
    ) -> Document:
        """Store a new document and its passages, or refuse a name already taken."""
        ...

    async def get(self, document_id: UUID) -> Document | None: ...

    async def list_for_element(self, element_id: UUID) -> tuple[DocumentSummary, ...]:
        """Every document attached to an element, oldest first, without content."""
        ...

    async def replace(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: AuditOf[Document],
    ) -> Document:
        """Overwrite a stored document, and replace its passages with these."""
        ...

    async def delete(self, document_id: UUID, *, audit: AuditOf[Document]) -> bool: ...

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

    async def list_all(
        self, *, package_ids: Collection[UUID] | None = None, root_only: bool = False
    ) -> tuple[Diagram, ...]:
        """Every diagram, sorted by name, each with its node count.

        `package_ids` keeps only the diagrams filed in one of those packages
        (the service resolves a subtree to ids); `root_only` only the unfiled.
        """
        ...

    async def add(self, diagram: Diagram, *, audit: AuditOf[Diagram]) -> Diagram:
        """Store a new diagram, or refuse a name already taken."""
        ...

    async def get(self, diagram_id: UUID) -> Diagram | None: ...

    async def nodes_of(self, diagram_id: UUID) -> tuple[DiagramNode, ...]: ...

    async def save(self, diagram: Diagram, *, audit: AuditOf[Diagram]) -> Diagram:
        """Overwrite the name, description and update time of a stored diagram."""
        ...

    async def delete(self, diagram_id: UUID, *, audit: AuditOf[Diagram]) -> bool: ...

    async def replace_layout(
        self,
        diagram_id: UUID,
        nodes: Sequence[DiagramNode],
        *,
        now: datetime,
        audit: AuditOf[tuple[DiagramNode, ...]],
    ) -> bool:
        """Replace every node of a diagram in one transaction; `False` if it is gone."""
        ...


class AuditRepository(Protocol):
    """Reads the audit trail. Writing is not here: an event is inserted by the
    repository that makes the write it describes, in the same transaction
    (docs/adr/0046)."""

    async def page(self, criteria: AuditFilter) -> AuditPage:
        """Newest first (`at`, then `id`), at most `criteria.limit` events and a cursor."""
        ...


class BaselineRepository(Protocol):
    """Persistence for baselines — docs/adr/0047.

    Every write names its audit, with no default, like the stores of the model
    (`tests/unit/test_audit_coverage.py`). The reads that feed a decision —
    `snapshot_of`, `current_snapshot`, `restore_plan` — each run in one
    transaction at one instant: a link is never read without its element.
    """

    async def list_all(self) -> tuple[Baseline, ...]:
        """Every baseline by name, each with the counts of what it holds."""
        ...

    async def get(self, baseline_id: UUID) -> Baseline | None: ...

    async def create(self, baseline: Baseline, *, audit: AuditOf[Baseline]) -> Baseline:
        """Copy the whole model under this baseline, in one REPEATABLE READ transaction.

        Raises `DuplicateBaselineError` for a taken name and
        `BaselineTooLargeError` past the limits; writes nothing then. `audit` is
        given the stored baseline, counts included.
        """
        ...

    async def delete(self, baseline_id: UUID, *, audit: AuditOf[Baseline]) -> bool:
        """Remove the copy only — never a row of the model."""
        ...

    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot: ...

    async def current_snapshot(self) -> ModelSnapshot:
        """The live elements, links and boxes of every diagram, as values."""
        ...

    async def restore_plan(self, baseline_id: UUID, *, keep_new: bool) -> RestorePlan:
        """What restoring would do now, documents counted. Raises `BaselineNotFoundError`."""
        ...

    async def apply_restore(
        self,
        baseline_id: UUID,
        *,
        keep_new: bool,
        expected: RestorePlan,
        now: datetime,
        audit: AuditOf[RestorePlan],
    ) -> None:
        """Re-plan in one SERIALIZABLE transaction and apply it only if it is `expected`.

        A different plan, a protected element touched, a clash or a concurrent
        write raise `RestoreConflictError` / `RestoreBlockedError` and write nothing.
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


class AnalysisRepository(Protocol):
    """The reads the matrix, the quality checks and the dashboards need.

    A fifth port, declared apart from `ArchitectureRepository` for the reason
    `IpamRepository` is: it is a different use case, satisfied by the same
    PostgreSQL class. Nothing here writes — an analysis computes, it does not
    store (docs/adr/0056).
    """

    async def elements_by_ids(
        self, element_ids: Sequence[UUID], *, limit: int | None = None
    ) -> tuple[Element, ...]:
        """The elements among these ids that exist, by name; at most `limit` (a SQL LIMIT).

        Absent ids are missing, not errors.
        """
        ...

    async def links_between(
        self, source_ids: Sequence[UUID], target_ids: Sequence[UUID], *, limit: int | None = None
    ) -> tuple[Relationship, ...]:
        """Every link running from one of `source_ids` to one of `target_ids`; at most `limit`."""
        ...

    async def links_touching(
        self, element_ids: Sequence[UUID], *, limit: int | None = None
    ) -> tuple[Relationship, ...]:
        """Every link with at least one end among these ids; at most `limit` (a SQL LIMIT)."""
        ...

    async def grouped(
        self,
        dimensions: Sequence[Dimension],
        metric_key: str | None,
        *,
        scope: Sequence[UUID] | None = None,
    ) -> tuple[RawGroup, ...]:
        """Elements grouped by the raw value of each dimension, with partial sums of `metric_key`.

        Raw groups and not buckets: folding spellings together is
        `summarise`'s job, once, so this and the in-memory reference cannot
        disagree. `scope` narrows to those element ids; `None` is the whole
        catalogue.
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


class PackageRepository(Protocol):
    """Persistence for the classification tree."""

    async def list_all(self) -> tuple[Package, ...]:
        """Every package, sorted by lower-cased name."""
        ...

    async def counts(self) -> dict[UUID, PackageCounts]:
        """What each package holds directly. A package holding nothing may be absent."""
        ...

    async def get(self, package_id: UUID) -> Package | None: ...

    async def fold_names(self, names: Sequence[str]) -> list[str]:
        """`lower()` of each name as the database computes it, in the same order: the key its
        unique indexes on sibling names compare by, which Python cannot be trusted to compute."""
        ...

    async def add(self, package: Package, *, audit: AuditOf[Package]) -> Package:
        """Store a new package, or refuse a sibling name already taken."""
        ...

    async def save(self, package: Package, *, audit: AuditOf[Package]) -> Package:
        """Overwrite name, description, parent and update time of a stored package.

        `audit` may be a function of the stored package, called in the transaction.
        """
        ...

    async def delete(
        self,
        package_id: UUID,
        *,
        into: UUID | None,
        audit: AuditOf[PackageRemoval],
        require_empty: bool = False,
    ) -> bool:
        """Move the direct sub-packages, elements and diagrams to `into`, then delete.

        `into=None` is the root. One transaction: a sibling name taken at the
        destination refuses everything and moves nothing. `False` if the
        package is gone.

        `require_empty` is the caller saying it asked for no move: nothing is
        moved, and a package that holds anything *at that moment* (counted in
        the same transaction) raises `PackageNotEmptyError`. `into=None` alone
        cannot say it — it means "the root".
        """
        ...

    async def move_elements(
        self,
        element_ids: Sequence[UUID],
        package_id: UUID | None,
        *,
        audit: AuditOf[Mapping[UUID, UUID | None]],
    ) -> None:
        """Put these elements in a package (`None`: the root), all or none.

        Raises `ElementNotFoundError` naming an absent id and
        `PackageNotFoundError` for an absent package; nothing is moved then.
        """
        ...


class SavedQueryRepository(Protocol):
    """Persistence for the named, shared filters of the catalogue."""

    async def list_all(self) -> tuple[SavedQuerySummary, ...]:
        """Every saved query, sorted by lower-cased name, without reading a stored query.

        A list that parsed every query would be unreadable the day one of them
        is not: one stale row must not hide the others.
        """
        ...

    async def get(self, query_id: UUID) -> SavedQuery | None:
        """One saved query; raises `InvalidQueryError` when its stored JSON cannot be read."""
        ...

    async def add(self, saved: SavedQuery, *, audit: AuditOf[SavedQuery]) -> SavedQuery:
        """Store a new saved query, or refuse a name already taken."""
        ...

    async def save(self, saved: SavedQuery, *, audit: AuditOf[Mapping[str, Any]]) -> SavedQuery:
        """Overwrite name, description, query and update time; never the author.

        `audit` is a function of the stored row as a plain mapping
        (`saved_query_record_view`), so a row whose JSON can no longer be read
        is still described.
        """
        ...

    async def delete(self, query_id: UUID, *, audit: AuditOf[Mapping[str, Any]]) -> bool: ...


class ExportSnapshot(Protocol):
    """A consistent, read-only reading of the graph, paged in a stable order.

    Pages rather than the whole graph so an export never holds the model in
    memory; one snapshot rather than one read per page so a write landing
    between two pages cannot tear the file (docs/adr/0050).
    """

    async def elements_page(self, offset: int, limit: int) -> tuple[Element, ...]: ...

    async def relationships_page(self, offset: int, limit: int) -> tuple[Relationship, ...]: ...

    async def skipped_views(self) -> int:
        """Views a scoped export leaves out because a box shows an element outside it — counted
        in this very snapshot, so the number and the file agree."""
        ...

    async def check_scope(self) -> None:
        """Run the scope's filter once; a filter the database cannot run is `UnknownScopeError`."""
        ...

    async def packages(self) -> tuple[PackageNode, ...]:
        """The packages of the export, with the parent each names (the scope may cut the tree)."""
        ...

    async def views_page(self, offset: int, limit: int) -> tuple[ExportedView, ...]:
        """Saved diagrams with their boxes, by name; boxes by exported identifier."""
        ...


class ExchangeRepository(Protocol):
    """What the import/export of an exchange file needs beyond the graph's own port."""

    def snapshot(self, scope: ExportScope) -> AbstractAsyncContextManager[ExportSnapshot]:
        """A `REPEATABLE READ`, read-only view of what `scope` selects."""
        ...

    def transaction(self, *, commit: bool) -> AbstractAsyncContextManager[Transaction]:
        """The one transaction every write of an import joins, held by one import at a time.

        Rolled back unless `commit` is still true on a normal exit. A second import
        is refused at once (`ImportInProgressError`), never queued (docs/adr/0052).
        """
        ...

    async def apply_rows[Row](
        self,
        rows: Sequence[Row],
        apply: Callable[[Row], Awaitable[None]],
        *,
        max_refused: int | None = None,
    ) -> list[RowRefusal]:
        """Apply each row in the transaction; a refused row is skipped, the others stay.

        `max_refused` is what this call may still refuse (the default is the whole budget of
        an import, `MAX_REFUSED_ROWS`): an import that calls this twice shares one budget.

        Rows run in batches of one `SAVEPOINT`, and a refusal replays the rows of its batch
        that precede it: `apply` must be re-runnable, deriving its state from what it does now.
        """
        ...

    async def known_elements(self) -> tuple[Element, ...]:
        """Every element of the catalogue — the map an import matches `(type, name)` against."""
        ...

    async def known_relationship_keys(self) -> frozenset[tuple[RelationshipType, UUID, UUID]]:
        """`(type, source, target)` of every link, to tell a new link from an existing one."""
        ...

    async def lock_package_tree(self) -> None:
        """Take the package tree's lock, before any element lock, as a package write does."""
        ...

    async def lock_elements(self, ids: Iterable[UUID]) -> None:
        """Take the row locks of these existing elements, in id order, without writing.

        Done before the first write so that an import and an ordinary write meet the
        elements in the same order (docs/adr/0052, the interlock limit).
        """
        ...
