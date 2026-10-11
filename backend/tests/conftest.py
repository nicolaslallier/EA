"""Fixtures shared by every suite."""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import math
import os
import re
import socket
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Collection,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest

from ea.db.unit_of_work import BATCH_SIZE, MAX_REFUSED_ROWS
from ea.domain.approval import WriteKind, guard_all, guard_element_write, is_frozen
from ea.domain.archimate import RelationshipType as R
from ea.domain.audit import (
    AuditEvent,
    AuditFilter,
    AuditOf,
    AuditPage,
    decode_cursor,
    encode_cursor,
    events_for,
    saved_query_view,
)
from ea.domain.auth import APPROVER_ROLE, EDITOR_ROLE, Caller
from ea.domain.baselines import Baseline, BoxSnapshot, ModelSnapshot, RestorePlan, plan_restore
from ea.domain.diagrams import Diagram, DiagramNode
from ea.domain.documents import Document, DocumentSummary
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    BaselineNotFoundError,
    DiagramNotFoundError,
    DocumentNotFoundError,
    DomainError,
    DuplicateBaselineError,
    DuplicateDiagramError,
    DuplicateDocumentError,
    DuplicatePackageError,
    DuplicateSavedQueryError,
    ElementNotFoundError,
    ImportInProgressError,
    InvalidQueryError,
    NotAuthenticatedError,
    NotAuthorisedError,
    PackageNotEmptyError,
    PackageNotFoundError,
    RestoreBlockedError,
    RestoreConflictError,
    SavedQueryNotFoundError,
    StoredFileNotFoundError,
    TooManyRefusedRowsError,
    WriteConflictError,
)
from ea.domain.exchange import SOURCE_ID, ExportScope, identifier_of
from ea.domain.files import FileDetails, FileListing, FileMetadata, StoredFile
from ea.domain.ipam import ADDRESS_PROPERTY, PREFIX_PROPERTY, read_vrf
from ea.domain.model import Element, Relationship
from ea.domain.packages import Package, PackageCounts
from ea.domain.ports import (
    ElementChange,
    ElementFilter,
    ElementGuard,
    ElementRemoval,
    EndsReview,
    GraphView,
    Held,
    PackageRemoval,
    RowRefusal,
    Transaction,
)
from ea.domain.saved_queries import SavedQuery, SavedQuerySummary
from ea.domain.search import (
    DEFAULT_SEARCH_LIMIT,
    EMBEDDING_DIMENSIONS,
    EmbeddedChunk,
    Passage,
)
from ea.repositories.architecture_store import author_of
from ea.services.architecture import ArchitectureService
from ea.services.baselines import BaselineService
from ea.services.caller import acting_as, current_caller
from ea.services.diagrams import DiagramService
from ea.services.documents import DocumentService
from ea.services.exchanges import ExchangeService
from ea.services.files import FileService
from ea.services.indexing import DocumentIndexer
from ea.services.ipam import IpamService
from ea.services.packages import PackageService
from ea.services.saved_queries import SavedQueryService

#: Every suite that needs a timestamp uses this one, so nothing depends on
#: when the tests happen to run.
FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def an_editor(**overrides: object) -> Caller:
    fields: dict[str, object] = {
        "subject": "editor-1",
        "username": "editor",
        "roles": frozenset({EDITOR_ROLE}),
    }
    return Caller(**{**fields, **overrides})  # type: ignore[arg-type]


def an_approver(**overrides: object) -> Caller:
    """An approver who is also an editor: the roles accumulate (docs/adr/0046)."""
    return an_editor(
        **{
            "subject": "approver-1",
            "username": "approver",
            "roles": frozenset({EDITOR_ROLE, APPROVER_ROLE}),
            **overrides,
        }
    )


def a_reader(**overrides: object) -> Caller:
    return an_editor(
        **{"subject": "reader-1", "username": "reader", "roles": frozenset(), **overrides}
    )


class StaticVerifier:
    """Tokens known in advance — the `AccessTokenVerifier` of every API test."""

    def __init__(self, tokens: dict[str, Caller]) -> None:
        self.tokens = tokens

    async def verify(self, token: str) -> Caller:
        try:
            return self.tokens[token]
        except KeyError:
            raise NotAuthenticatedError("the bearer token is not valid") from None


@pytest.fixture(autouse=True)
def _an_editor_is_calling() -> Iterator[Caller]:
    """The services refuse a call nobody makes (docs/adr/0032).

    A suite about what a use case *does* is not about who may run it, so an
    editor calls unless a test says otherwise — `nobody_calling`, `acting_as`.
    """
    with acting_as(an_editor()) as caller:
        yield caller


@pytest.fixture
def nobody_calling() -> Iterator[None]:
    token = current_caller.set(None)
    try:
        yield
    finally:
        current_caller.reset(token)


class NetworkAccessInTestError(RuntimeError):
    """A DB-free test tried to reach another machine.

    A `RuntimeError` and deliberately not an `OSError`: the drivers and the
    embedding client translate an `OSError` into "store unreachable", which is
    exactly the misleading sentence this guard exists to replace.
    """


def _is_loopback(host: object) -> bool:
    """`localhost`, `127.0.0.0/8`, `::1` and their IPv4-mapped spelling."""
    if host is None:
        return True  # a passive lookup (`getaddrinfo(None, port)`) goes nowhere
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() == "localhost":
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    return bool(address.is_loopback or (mapped is not None and mapped.is_loopback))


@pytest.fixture(autouse=True)
def _no_network_beyond_this_machine(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse, at once, any connection a test opens to another machine.

    The settings defaults point PostgreSQL and the embedding service at
    the cluster — the useful default for a developer, and a trap for a test: a
    lifespan entered with them goes looking for the real stores. On the LAN
    that test quietly talks to the shared databases; off it, it waits for a
    timeout and fails as "PostgreSQL unreachable", which reads like an outage
    rather than a test that forgot to inject its double. Both are the same bug,
    and this turns it into one immediate failure naming the address.

    Loopback stays open, and that is all `tests/integration` needs: its stores
    are throwaway containers on 127.0.0.1 (docs/adr/0024), and its fixtures
    already refuse any other host. The guard applies there too, so a test that
    slips past those fixtures toward the shared cluster fails here instead of
    emptying it.

    The patch sits on `socket.socket` itself, below every client in this
    process: asyncio, and therefore asyncpg and httpx, all
    end in `sock.connect`. `getaddrinfo` is guarded too, because a lookup with
    no network can hang for the resolver's own timeout before any connect.
    In-process transports — `httpx.ASGITransport`, `httpx.MockTransport` —
    open no socket and are untouched.
    """
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def refuse(what: str) -> NetworkAccessInTestError:
        return NetworkAccessInTestError(
            f"{request.node.nodeid} tried to reach {what}. No test may leave "
            "this machine: inject a double "
            "(`architecture_service=`, `documents=`, `indexer=`) or turn the store "
            "off in the Settings you build (`postgres_enabled=False`, "
            "`embeddings_enabled=False`)."
        )

    def _target(address: object) -> tuple[bool, str]:
        if isinstance(address, str | bytes):  # AF_UNIX: a path on this machine
            return True, repr(address)
        if isinstance(address, tuple) and address:
            return _is_loopback(address[0]), ":".join(str(part) for part in address[:2])
        return False, repr(address)

    def connect(self: socket.socket, address: Any) -> None:
        local, shown = _target(address)
        if not local:
            raise refuse(shown)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        local, shown = _target(address)
        if not local:
            raise refuse(shown)
        return real_connect_ex(self, address)

    def getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
        if not _is_loopback(host):
            raise refuse(f"{host!r} (name lookup)")
        return real_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


@pytest.fixture(autouse=True)
def _database_credentials_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stand in for the deployment that provides the database credentials.

    `Settings` refuses an empty PostgreSQL password outside debug since
    `postgres_enabled` defaults to on (docs/adr/0017) — so a suite that builds
    settings must look like a configured process. Tests
    that are *about* the credentials pass their own values, which take
    precedence over this.

    A password already in the environment wins: that is the integration run,
    which needs the credentials of the database it is about to talk to.
    """
    for variable in ("EA_POSTGRES_PASSWORD",):
        if not os.environ.get(variable):
            monkeypatch.setenv(variable, "test-password")


def _live_loggers() -> list[logging.Logger]:
    return [logging.getLogger()] + [
        logger
        for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]


@pytest.fixture(autouse=True)
def _logging_is_put_back_exactly_as_it_was() -> Iterator[None]:
    """No test may leave the logging of the process reconfigured.

    Three of them apply a real `dictConfig`: the one that checks the
    configuration is accepted, and the two that drive `python -m ea.reindex`
    and `python -m ea.files_reconcile` through their entry points.
    `dictConfig` sets a level on `ea` and on nine other loggers and takes
    `propagate` off two of them — and a `propagate` left off is a `caplog` in
    another module that silently captures nothing.

    **A logger the test created is reset, not just the ones that already
    existed.** `dictConfig` *creates* `ea.requests` the first time it runs, so
    it was in no snapshot and nothing put it back: the first module to call an
    entry point left `ea.requests` pinned at INFO for the rest of the session,
    and `test_request_logging.py` — which asserts `/health` logs one line at
    DEBUG — then captured nothing, in whichever order put it second.
    """
    before = {
        logger: (logger.level, list(logger.handlers), logger.propagate, list(logger.filters))
        for logger in _live_loggers()
    }
    yield
    for logger in _live_loggers():
        level, handlers, propagate, filters = before.get(logger, (logging.NOTSET, [], True, []))
        logger.setLevel(level)
        logger.handlers[:] = handlers
        logger.propagate = propagate
        logger.filters[:] = filters


class InMemoryAuditLog:
    """The `audit_events` table: append-only, and written only by a write that succeeded.

    The three repository doubles share one of these (the `audit_log` fixture), so
    a service test asserts on what a real transaction would have committed: a
    refused write appends nothing, because the double raises before it does.
    """

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, events: Sequence[AuditEvent]) -> None:
        self.events.extend(events)

    async def page(self, criteria: AuditFilter) -> AuditPage:
        def matches(event: AuditEvent) -> bool:
            return all(
                [
                    criteria.entity_kind is None or event.entity_kind == criteria.entity_kind,
                    criteria.entity_id is None or event.entity_id == criteria.entity_id,
                    criteria.actor is None or criteria.actor in (event.actor_sub, event.actor_name),
                    criteria.action is None or event.action == criteria.action,
                    criteria.since is None or event.at >= criteria.since,
                    criteria.until is None or event.at < criteria.until,
                    criteria.request_id is None or event.request_id == criteria.request_id,
                ]
            )

        ordered = sorted(
            (event for event in self.events if matches(event)),
            key=lambda event: (event.at, event.id.int),
            reverse=True,
        )
        if criteria.cursor is not None:
            at, event_id = decode_cursor(criteria.cursor)
            ordered = [e for e in ordered if (e.at, e.id.int) < (at, event_id.int)]
        shown = ordered[: criteria.limit]
        more = len(ordered) > criteria.limit
        return AuditPage(
            events=tuple(shown),
            next_cursor=encode_cursor(shown[-1].at, shown[-1].id) if more else None,
        )


class InMemoryRepository:
    """A dictionary pretending to be a graph. Enough for the service's rules.

    Deleting an element here does not cascade to the document and diagram
    doubles: in PostgreSQL the foreign keys of revision 0006 do that, so a unit
    test must not rely on it — `tests/integration/test_element_cascade.py` does.
    """

    def __init__(self, audit_log: InMemoryAuditLog | None = None) -> None:
        self.audit_log = audit_log if audit_log is not None else InMemoryAuditLog()
        self.elements: dict[UUID, Element] = {}
        self.relationships: dict[UUID, Relationship] = {}

    async def add_element(self, element: Element, *, audit: AuditOf[Element]) -> Element:
        events = events_for(audit, element)
        element = replace(element, last_modified_by_sub=author_of(events))
        self._refuse_a_taken_address(element)
        self.elements[element.id] = element
        self.audit_log.append(events)
        return element

    def touch(self, element_ids: Iterable[UUID], events: Sequence[AuditEvent]) -> None:
        """What the real repositories do to a changed element: a new revision and an author."""
        author = author_of(events)
        for element_id in element_ids:
            element = self.elements[element_id]
            self.elements[element_id] = replace(
                element, revision=element.revision + 1, last_modified_by_sub=author
            )

    async def get_element(self, element_id: UUID) -> Element | None:
        return self.elements.get(element_id)

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        return tuple(self.elements.values())[criteria.offset : criteria.offset + criteria.limit]

    async def count_elements(self, criteria: ElementFilter) -> int:
        return len(self.elements)

    async def update_element(
        self,
        element_id: UUID,
        change: Callable[[Element], Element],
        *,
        audit: AuditOf[ElementChange],
    ) -> Element:
        if element_id not in self.elements:
            raise ElementNotFoundError(str(element_id))
        before = self.elements[element_id]
        after = change(before)
        if replace(after, updated_at=before.updated_at) == before:
            return before  # nothing observable changed: no write, no event
        self._refuse_a_taken_address(after)
        events = events_for(audit, ElementChange(before, after))
        after = replace(
            after,
            revision=before.revision + 1,
            last_modified_by_sub=author_of(events),
        )
        self.elements[element_id] = after
        self.audit_log.append(events)
        return after

    async def transition_element(
        self,
        element_id: UUID,
        change: Callable[[Element], Element],
        *,
        audit: AuditOf[ElementChange],
    ) -> Element:
        if element_id not in self.elements:
            raise ElementNotFoundError(str(element_id))
        before = self.elements[element_id]
        after = change(before)
        events = events_for(audit, ElementChange(before, after))
        self.elements[element_id] = after
        self.audit_log.append(events)
        return after

    def _refuse_a_taken_address(self, element: Element) -> None:
        """Stand in for the `(vrf, ip_address)` partial unique index.

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

    def _review_ends(
        self, end_ids: Sequence[UUID], ends: EndsReview, events: Sequence[AuditEvent]
    ) -> list[ElementChange]:
        """What the repository does to the elements at the ends of a link, in its transaction."""
        missing = [i for i in end_ids if i not in self.elements]
        if missing:
            raise ElementNotFoundError(str(missing[0]))
        author = author_of(events)
        changes = [
            ElementChange(c.before, replace(c.after, last_modified_by_sub=author))
            for c in ends.apply(self.elements[i] for i in set(end_ids))
        ]
        for change in changes:
            self.elements[change.after.id] = change.after
        return changes

    async def delete_element(
        self,
        element_id: UUID,
        *,
        audit: AuditOf[ElementRemoval],
        guard: ElementGuard | None = None,
        ends: EndsReview | None = None,
    ) -> bool:
        element = self.elements.get(element_id)
        if element is None:
            return False
        if guard is not None:
            guard(element)
        links = tuple(
            r for r in self.relationships.values() if element_id in (r.source_id, r.target_id)
        )
        events = events_for(
            audit,
            ElementRemoval(
                element=element, relationship_count=len(links), relationships=links[:100]
            ),
        )
        neighbours = {end for r in links for end in (r.source_id, r.target_id)} - {element_id}
        reviewed = self._review_ends(tuple(neighbours), ends, events) if ends is not None else []
        del self.elements[element_id]
        for link in links:
            del self.relationships[link.id]
        self.audit_log.append(events)
        for end in reviewed:
            self.audit_log.append(ends.audit(end))  # type: ignore[union-attr]
        return True

    async def add_relationship(
        self,
        relationship: Relationship,
        *,
        audit: AuditOf[Relationship],
        ends: EndsReview | None = None,
    ) -> Relationship:
        events = events_for(audit, relationship)
        reviewed = (
            self._review_ends((relationship.source_id, relationship.target_id), ends, events)
            if ends is not None
            else []
        )
        self.relationships[relationship.id] = relationship
        self.audit_log.append(events)
        for end in reviewed:
            self.audit_log.append(ends.audit(end))  # type: ignore[union-attr]
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

    async def delete_relationship(
        self,
        relationship_id: UUID,
        *,
        audit: AuditOf[Relationship],
        ends: EndsReview | None = None,
    ) -> bool:
        stored = self.relationships.get(relationship_id)
        if stored is None:
            return False
        events = events_for(audit, stored)
        reviewed = (
            self._review_ends((stored.source_id, stored.target_id), ends, events)
            if ends is not None
            else []
        )
        del self.relationships[relationship_id]
        self.audit_log.append(events)
        for end in reviewed:
            self.audit_log.append(ends.audit(end))  # type: ignore[union-attr]
        return True

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

    async def view_of(self, element_ids: Sequence[UUID]) -> GraphView:
        """The elements that exist among these ids, and the links between them."""
        scope = set(element_ids)
        return GraphView(
            elements=tuple(
                self.elements[element_id] for element_id in scope & self.elements.keys()
            ),
            relationships=tuple(
                link
                for link in self.relationships.values()
                if link.source_id in scope and link.target_id in scope
            ),
        )

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

    def __init__(
        self,
        audit_log: InMemoryAuditLog | None = None,
        graph: InMemoryRepository | None = None,
    ) -> None:
        self.audit_log = audit_log if audit_log is not None else InMemoryAuditLog()
        #: The graph double the documents hang from: the freeze of an element is read there.
        self.graph = graph
        self.documents: dict[UUID, Document] = {}
        self.chunks: dict[UUID, tuple[EmbeddedChunk, ...]] = {}

    def _refuse_a_frozen_element(self, element_id: UUID) -> None:
        """Stand in for the repository reading the element under its lock (docs/adr/0046)."""
        element = self.graph.elements.get(element_id) if self.graph is not None else None
        if element is not None:
            guard_element_write(element, WriteKind.DOCUMENT)

    def _touch(self, element_id: UUID, events: Sequence[AuditEvent]) -> None:
        if self.graph is not None and element_id in self.graph.elements:
            self.graph.touch([element_id], events)

    async def add(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: AuditOf[Document],
    ) -> Document:
        self._refuse_a_frozen_element(document.element_id)
        events = events_for(audit, document)
        taken = any(
            stored.element_id == document.element_id and stored.filename == document.filename
            for stored in self.documents.values()
        )
        if taken:
            msg = f"{document.filename!r} is already attached to this element"
            raise DuplicateDocumentError(msg)
        self.documents[document.id] = document
        self.chunks[document.id] = tuple(chunks)
        self.audit_log.append(events)
        self._touch(document.element_id, events)
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

    async def replace(
        self,
        document: Document,
        chunks: Sequence[EmbeddedChunk] = (),
        *,
        audit: AuditOf[Document],
    ) -> Document:
        if document.id not in self.documents:
            raise DocumentNotFoundError(str(document.id))
        changing = self.documents[document.id].content != document.content
        if changing:
            self._refuse_a_frozen_element(document.element_id)
        events = events_for(audit, self.documents[document.id])
        self.documents[document.id] = document
        self.chunks[document.id] = tuple(chunks)
        self.audit_log.append(events)
        if changing:
            self._touch(document.element_id, events)
        return document

    async def delete(self, document_id: UUID, *, audit: AuditOf[Document]) -> bool:
        """The passages go with the document — here by hand, in PostgreSQL by
        the foreign key from `document_chunks`."""
        stored = self.documents.get(document_id)
        if stored is None:
            return False
        self._refuse_a_frozen_element(stored.element_id)
        events = events_for(audit, stored)
        self.chunks.pop(document_id, None)
        del self.documents[document_id]
        self.audit_log.append(events)
        self._touch(stored.element_id, events)
        return True

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


class InMemoryDiagrams:
    """A dictionary pretending to be the `diagrams` and `diagram_nodes` tables.

    It reproduces the unique name, for the same reason the document double
    reproduces its own constraint: a service test must see the refusal the
    real table would give.
    """

    def __init__(self, audit_log: InMemoryAuditLog | None = None) -> None:
        self.audit_log = audit_log if audit_log is not None else InMemoryAuditLog()
        self.diagrams: dict[UUID, Diagram] = {}
        self.nodes: dict[UUID, tuple[DiagramNode, ...]] = {}

    def _counted(self, diagram: Diagram) -> Diagram:
        return diagram.with_node_count(len(self.nodes.get(diagram.id, ())))

    def _refuse_a_taken_name(self, diagram: Diagram) -> None:
        if any(d.name == diagram.name and d.id != diagram.id for d in self.diagrams.values()):
            msg = f"a diagram is already named {diagram.name!r}"
            raise DuplicateDiagramError(msg)

    async def list_all(
        self, *, package_ids: Collection[UUID] | None = None, root_only: bool = False
    ) -> tuple[Diagram, ...]:
        wanted = [
            d
            for d in self.diagrams.values()
            if (not root_only or d.package_id is None)
            and (package_ids is None or d.package_id in package_ids)
        ]
        return tuple(self._counted(d) for d in sorted(wanted, key=lambda d: d.name))

    async def add(self, diagram: Diagram, *, audit: AuditOf[Diagram]) -> Diagram:
        self._refuse_a_taken_name(diagram)
        self.diagrams[diagram.id] = diagram
        self.audit_log.append(events_for(audit, diagram))
        return diagram

    async def get(self, diagram_id: UUID) -> Diagram | None:
        stored = self.diagrams.get(diagram_id)
        return self._counted(stored) if stored is not None else None

    async def nodes_of(self, diagram_id: UUID) -> tuple[DiagramNode, ...]:
        return self.nodes.get(diagram_id, ())

    async def save(self, diagram: Diagram, *, audit: AuditOf[Diagram]) -> Diagram:
        if diagram.id not in self.diagrams:
            raise DiagramNotFoundError(str(diagram.id))
        self._refuse_a_taken_name(diagram)
        events = events_for(audit, self.diagrams[diagram.id])
        self.diagrams[diagram.id] = diagram
        self.audit_log.append(events)
        return diagram

    async def delete(self, diagram_id: UUID, *, audit: AuditOf[Diagram]) -> bool:
        stored = self.diagrams.get(diagram_id)
        if stored is None:
            return False
        events = events_for(audit, stored)
        self.nodes.pop(diagram_id, None)
        del self.diagrams[diagram_id]
        self.audit_log.append(events)
        return True

    async def replace_layout(
        self,
        diagram_id: UUID,
        nodes: Sequence[DiagramNode],
        *,
        now: datetime,
        audit: AuditOf[tuple[DiagramNode, ...]],
    ) -> bool:
        stored = self.diagrams.get(diagram_id)
        if stored is None:
            return False
        events = events_for(audit, self.nodes.get(diagram_id, ()))
        self.nodes[diagram_id] = tuple(nodes)
        self.diagrams[diagram_id] = stored.touched(now)
        self.audit_log.append(events)
        return True


class InMemoryBaselines:
    """The baseline tables over the model doubles they copy.

    Capture copies the dicts. `apply_restore` plans again on the double's own
    state, refuses a plan that is not the one expected or that touches a
    protected element (`protected`, a set a test fills, plus every approved or locked
    one, as the real repository reads them), mutates and swaps in
    only if every step held — the all-or-nothing the real transaction gives,
    uniqueness of (type, name) and of addresses included.
    """

    def __init__(
        self,
        graph: InMemoryRepository,
        diagrams: InMemoryDiagrams,
        documents: InMemoryDocuments | None = None,
        packages: InMemoryPackages | None = None,
    ) -> None:
        self.graph = graph
        self.diagrams = diagrams
        self.documents = documents
        self.packages = packages
        self.audit_log = graph.audit_log
        self.protected: set[UUID] = set()
        self.baselines: dict[UUID, Baseline] = {}
        self.copies: dict[UUID, ModelSnapshot] = {}

    def _counted(self, baseline: Baseline) -> Baseline:
        copy = self.copies.get(baseline.id, ModelSnapshot((), ()))
        return baseline.with_counts(len(copy.elements), len(copy.relationships), len(copy.boxes))

    async def list_all(self) -> tuple[Baseline, ...]:
        return tuple(
            self._counted(b) for b in sorted(self.baselines.values(), key=lambda b: b.name)
        )

    async def get(self, baseline_id: UUID) -> Baseline | None:
        stored = self.baselines.get(baseline_id)
        return self._counted(stored) if stored is not None else None

    async def create(self, baseline: Baseline, *, audit: AuditOf[Baseline]) -> Baseline:
        if any(b.name == baseline.name for b in self.baselines.values()):
            raise DuplicateBaselineError(f"a baseline is already named {baseline.name!r}")
        self.baselines[baseline.id] = baseline
        self.copies[baseline.id] = await self.current_snapshot()
        stored = self._counted(baseline)
        self.audit_log.append(events_for(audit, stored))
        return stored

    async def delete(self, baseline_id: UUID, *, audit: AuditOf[Baseline]) -> bool:
        stored = self.baselines.get(baseline_id)
        if stored is None:
            return False
        events = events_for(audit, self._counted(stored))
        del self.baselines[baseline_id]
        self.copies.pop(baseline_id, None)
        self.audit_log.append(events)
        return True

    async def snapshot_of(self, baseline_id: UUID) -> ModelSnapshot:
        return self.copies.get(baseline_id, ModelSnapshot((), ()))

    async def current_snapshot(self) -> ModelSnapshot:
        tree = self.packages.packages if self.packages is not None else {}

        def path(package_id: UUID) -> str:
            package = tree[package_id]
            above = path(package.parent_id) + " / " if package.parent_id else ""
            return above + package.name

        return ModelSnapshot(
            elements=tuple(self.graph.elements.values()),
            relationships=tuple(self.graph.relationships.values()),
            boxes=tuple(
                BoxSnapshot(diagram_id, n.element_id, n.x, n.y, n.width, n.height)
                for diagram_id, nodes in self.diagrams.nodes.items()
                for n in nodes
            ),
            package_paths={package_id: path(package_id) for package_id in tree},
        )

    async def _plan(self, baseline_id: UUID, keep_new: bool) -> RestorePlan:
        if baseline_id not in self.baselines:
            raise BaselineNotFoundError(f"no baseline with id {baseline_id}")
        plan = plan_restore(
            self.copies[baseline_id],
            await self.current_snapshot(),
            keep_new=keep_new,
            packages_present=frozenset(self.packages.packages if self.packages else ()),
            protected_ids=frozenset(self.protected)
            | frozenset(e.id for e in self.graph.elements.values() if is_frozen(e)),
            diagrams_present=frozenset(self.diagrams.diagrams),
        )
        held: dict[UUID, list[UUID]] = {}
        for d in sorted(
            self.documents.documents.values() if self.documents else (), key=lambda d: str(d.id)
        ):
            held.setdefault(d.element_id, []).append(d.id)
        return replace(
            plan,
            documents_lost=sum(len(held.get(e.id, ())) for e in plan.elements_to_delete),
            cascades=tuple(
                replace(
                    c,
                    document_count=len(held.get(c.element_id, ())),
                    document_ids=tuple(held.get(c.element_id, ())[:100]),
                )
                for c in plan.cascades
            ),
        )

    async def restore_plan(self, baseline_id: UUID, *, keep_new: bool) -> RestorePlan:
        return await self._plan(baseline_id, keep_new)

    async def apply_restore(
        self,
        baseline_id: UUID,
        *,
        keep_new: bool,
        expected: RestorePlan,
        now: datetime,
        audit: AuditOf[RestorePlan],
    ) -> None:
        plan = await self._plan(baseline_id, keep_new)
        if plan.is_blocked:
            raise RestoreBlockedError(
                "protected elements: " + ", ".join(e.name for e in plan.blocked)
            )
        if plan != expected:
            raise RestoreConflictError("the model changed since the plan was read")
        events = events_for(audit, plan)
        saved = (
            dict(self.graph.elements),
            dict(self.graph.relationships),
            dict(self.diagrams.nodes),
            dict(self.documents.documents) if self.documents else {},
        )
        try:
            for link in plan.relationships_to_delete:
                self.graph.relationships.pop(link.id, None)
            for element in plan.elements_to_delete:
                self.graph.elements.pop(element.id, None)
                for link_id in [
                    i
                    for i, r in self.graph.relationships.items()
                    if element.id in (r.source_id, r.target_id)
                ]:
                    del self.graph.relationships[link_id]
                if self.documents is not None:
                    for doc_id in [
                        i for i, d in self.documents.documents.items() if d.element_id == element.id
                    ]:
                        del self.documents.documents[doc_id]
                for diagram_id, nodes in list(self.diagrams.nodes.items()):
                    self.diagrams.nodes[diagram_id] = tuple(
                        n for n in nodes if n.element_id != element.id
                    )
            for update in plan.elements_to_update:
                restored = replace(update.restored, updated_at=now)
                self.graph._refuse_a_taken_address(restored)
                self.graph.elements[restored.id] = restored
            for element in plan.elements_to_create:
                clash = any(
                    e.name == element.name and e.element_type == element.element_type
                    for e in self.graph.elements.values()
                )
                if clash:
                    raise RestoreConflictError(f"restoring {element.name!r} conflicts: name taken")
                try:
                    self.graph._refuse_a_taken_address(element)
                except AddressAlreadyAssignedError as refusal:
                    raise RestoreConflictError(
                        f"restoring {element.name!r} conflicts: {refusal}"
                    ) from refusal
                self.graph.elements[element.id] = element
            for link in plan.relationships_to_create:
                self.graph.relationships[link.id] = link
            for box in plan.boxes_to_restore:
                kept = [
                    n
                    for n in self.diagrams.nodes.get(box.diagram_id, ())
                    if n.element_id != box.element_id
                ]
                kept.append(DiagramNode(box.element_id, box.x, box.y, box.width, box.height))
                self.diagrams.nodes[box.diagram_id] = tuple(kept)
        except Exception:
            self.graph.elements, self.graph.relationships = saved[0], saved[1]
            self.diagrams.nodes = saved[2]
            if self.documents is not None:
                self.documents.documents = saved[3]
            raise
        self.audit_log.append(events)


def _held_ids(ids: Iterable[UUID]) -> Held:
    found = tuple(sorted(ids))
    return Held(len(found), found[:100])


class InMemoryPackages:
    """A dictionary pretending to be `packages`: the sibling-name rule of its two indexes,
    and the counts and moves that SQL does across tables, reaching into the element and
    diagram doubles it is built over."""

    def __init__(self, graph: InMemoryRepository, diagrams: InMemoryDiagrams) -> None:
        self.audit_log = graph.audit_log
        self.packages: dict[UUID, Package] = {}
        self._graph = graph
        self._diagrams = diagrams

    def _refuse_a_taken_name(self, package: Package) -> None:
        if any(
            p.id != package.id
            and p.parent_id == package.parent_id
            and p.name.lower() == package.name.lower()
            for p in self.packages.values()
        ):
            msg = f"a package is already named {package.name!r} in that place"
            raise DuplicatePackageError(msg)

    def _require_parent(self, package: Package) -> None:
        if package.parent_id is not None and package.parent_id not in self.packages:
            raise PackageNotFoundError(str(package.parent_id))

    async def list_all(self) -> tuple[Package, ...]:
        return tuple(sorted(self.packages.values(), key=lambda p: (p.name.lower(), p.id)))

    async def counts(self) -> dict[UUID, PackageCounts]:
        return {
            p.id: PackageCounts(
                elements=sum(1 for e in self._graph.elements.values() if e.package_id == p.id),
                diagrams=sum(1 for d in self._diagrams.diagrams.values() if d.package_id == p.id),
                children=sum(1 for c in self.packages.values() if c.parent_id == p.id),
            )
            for p in self.packages.values()
        }

    async def get(self, package_id: UUID) -> Package | None:
        return self.packages.get(package_id)

    async def add(self, package: Package, *, audit: AuditOf[Package]) -> Package:
        self._require_parent(package)
        self._refuse_a_taken_name(package)
        self.packages[package.id] = package
        self.audit_log.append(events_for(audit, package))
        return package

    async def save(self, package: Package, *, audit: AuditOf[Package]) -> Package:
        if package.id not in self.packages:
            raise PackageNotFoundError(str(package.id))
        self._require_parent(package)
        self._refuse_a_taken_name(package)
        events = events_for(audit, self.packages[package.id])
        self.packages[package.id] = package
        self.audit_log.append(events)
        return package

    async def delete(
        self,
        package_id: UUID,
        *,
        into: UUID | None,
        audit: AuditOf[PackageRemoval],
        require_empty: bool = False,
    ) -> bool:
        if package_id not in self.packages:
            return False
        if require_empty:
            held = (await self.counts()).get(package_id, PackageCounts())
            if held.elements or held.diagrams or held.children:
                msg = "the package still holds sub-packages, elements or diagrams"
                raise PackageNotEmptyError(msg)
        if not require_empty:
            guard_all(
                [e for e in self._graph.elements.values() if e.package_id == package_id],
                WriteKind.FILING,
            )
        children = [p for p in self.packages.values() if p.parent_id == package_id]
        for child in children:  # checked before anything moves: all or none
            self._refuse_a_taken_name(replace(child, parent_id=into))
        events = events_for(
            audit,
            PackageRemoval(
                package=self.packages[package_id],
                into=into,
                sub_packages=Held(len(children), tuple(c.id for c in children)),
                elements=_held_ids(
                    e.id for e in self._graph.elements.values() if e.package_id == package_id
                ),
                diagrams=_held_ids(
                    d.id for d in self._diagrams.diagrams.values() if d.package_id == package_id
                ),
            ),
        )
        for child in children:
            self.packages[child.id] = replace(child, parent_id=into)
        self._graph.touch(
            [e.id for e in self._graph.elements.values() if e.package_id == package_id], events
        )
        for element_id, element in list(self._graph.elements.items()):
            if element.package_id == package_id:
                self._graph.elements[element_id] = replace(element, package_id=into)
        for diagram_id, diagram in list(self._diagrams.diagrams.items()):
            if diagram.package_id == package_id:
                self._diagrams.diagrams[diagram_id] = replace(diagram, package_id=into)
        del self.packages[package_id]
        self.audit_log.append(events)
        return True

    async def move_elements(
        self,
        element_ids: Sequence[UUID],
        package_id: UUID | None,
        *,
        audit: AuditOf[Mapping[UUID, UUID | None]],
    ) -> None:
        missing = [str(i) for i in element_ids if i not in self._graph.elements]
        if missing:
            raise ElementNotFoundError(f"no element with id {', '.join(missing)}")
        if package_id is not None and package_id not in self.packages:
            raise PackageNotFoundError(str(package_id))
        guard_all([self._graph.elements[i] for i in dict.fromkeys(element_ids)], WriteKind.FILING)
        events = events_for(
            audit, {i: self._graph.elements[i].package_id for i in dict.fromkeys(element_ids)}
        )
        self.audit_log.append(events)
        self._graph.touch(
            [
                i
                for i in dict.fromkeys(element_ids)
                if self._graph.elements[i].package_id != package_id
            ],
            events,
        )
        for element_id in element_ids:
            self._graph.elements[element_id] = replace(
                self._graph.elements[element_id], package_id=package_id
            )


class InMemorySavedQueries:
    """A dictionary pretending to be `saved_queries`, with its unique name.

    `corrupt` holds ids whose stored JSON can no longer be read, so a test can
    prove a listing survives a row `get` refuses — as the real table does.
    """

    def __init__(self, audit_log: InMemoryAuditLog | None = None) -> None:
        self.audit_log = audit_log if audit_log is not None else InMemoryAuditLog()
        self.queries: dict[UUID, SavedQuery] = {}
        self.corrupt: set[UUID] = set()

    def _refuse_a_taken_name(self, saved: SavedQuery) -> None:
        if any(s.name == saved.name and s.id != saved.id for s in self.queries.values()):
            msg = f"a saved query is already named {saved.name!r}"
            raise DuplicateSavedQueryError(msg)

    async def list_all(self) -> tuple[SavedQuerySummary, ...]:
        ordered = sorted(self.queries.values(), key=lambda s: (s.name.lower(), s.id))
        return tuple(s.summary for s in ordered)

    async def get(self, query_id: UUID) -> SavedQuery | None:
        if query_id in self.corrupt:
            msg = "the stored query cannot be read: unsupported query version: 2"
            raise InvalidQueryError(msg)
        return self.queries.get(query_id)

    async def add(self, saved: SavedQuery, *, audit: AuditOf[SavedQuery]) -> SavedQuery:
        self._refuse_a_taken_name(saved)
        self.queries[saved.id] = saved
        self.audit_log.append(events_for(audit, saved))
        return saved

    async def save(self, saved: SavedQuery, *, audit: AuditOf[Mapping[str, Any]]) -> SavedQuery:
        if saved.id not in self.queries:
            raise SavedQueryNotFoundError(str(saved.id))
        self._refuse_a_taken_name(saved)
        events = events_for(audit, saved_query_view(self.queries[saved.id]))
        self.queries[saved.id] = saved
        self.audit_log.append(events)
        return saved

    async def delete(self, query_id: UUID, *, audit: AuditOf[Mapping[str, Any]]) -> bool:
        stored = self.queries.get(query_id)
        if stored is None:
            return False
        events = events_for(audit, saved_query_view(stored))
        self.corrupt.discard(query_id)
        del self.queries[query_id]
        self.audit_log.append(events)
        return True


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    product = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return product / norms if norms else 0.0


class _MemorySnapshot:
    """The pages the PostgreSQL snapshot would give, sorted the way its SQL sorts."""

    def __init__(self, repository: InMemoryRepository) -> None:
        self._repository = repository

    @staticmethod
    def _identifier(item: Element | Relationship) -> str:
        return identifier_of(item.properties.get(SOURCE_ID), item.id)

    async def elements_page(self, offset: int, limit: int) -> tuple[Element, ...]:
        ordered = sorted(
            self._repository.elements.values(),
            key=lambda e: (e.element_type.value, e.name, self._identifier(e)),
        )
        return tuple(ordered[offset : offset + limit])

    async def relationships_page(self, offset: int, limit: int) -> tuple[Relationship, ...]:
        elements = self._repository.elements

        def key(link: Relationship) -> tuple[str, ...]:
            source, target = elements[link.source_id], elements[link.target_id]
            return (
                source.element_type.value,
                source.name,
                target.element_type.value,
                target.name,
                link.relationship_type.value,
                link.name,
                self._identifier(link),
                str(link.id),
            )

        ordered = sorted(self._repository.relationships.values(), key=key)
        return tuple(ordered[offset : offset + limit])


class InMemoryExchange:
    """The `ExchangeRepository` over the in-memory graph.

    `apply_rows` mirrors `db.unit_of_work.apply_rows` step for step — a batch is one
    savepoint, a refusal rolls the batch back and *replays* the rows before it — because
    that replay is exactly what an import's callback must survive.
    """

    #: What is not the row's fault (kept in step with `db.unit_of_work._NOT_THE_ROWS`).
    NOT_THE_ROWS = (
        WriteConflictError,
        ImportInProgressError,
        NotAuthenticatedError,
        NotAuthorisedError,
        TooManyRefusedRowsError,
    )

    def __init__(self, repository: InMemoryRepository) -> None:
        self.repository = repository
        self.lock_held = False
        self.batch_size = BATCH_SIZE
        #: Rows handed to `apply`, replays included, and the replays alone.
        self.applied = 0
        self.replayed = 0
        self.locked: list[UUID] = []

    @asynccontextmanager
    async def snapshot(self, scope: ExportScope) -> AsyncIterator[_MemorySnapshot]:
        yield _MemorySnapshot(self.repository)

    def _copy(self) -> tuple[dict[UUID, Element], dict[UUID, Relationship], list[AuditEvent]]:
        return (
            dict(self.repository.elements),
            dict(self.repository.relationships),
            list(self.repository.audit_log.events),
        )

    def _restore(
        self, saved: tuple[dict[UUID, Element], dict[UUID, Relationship], list[AuditEvent]]
    ) -> None:
        self.repository.elements = dict(saved[0])
        self.repository.relationships = dict(saved[1])
        self.repository.audit_log.events = list(saved[2])

    @asynccontextmanager
    async def transaction(self, *, commit: bool) -> AsyncIterator[Transaction]:
        """All-or-nothing over the in-memory graph, like the real shared transaction."""
        if self.lock_held:
            msg = "another import is running: wait for it to finish"
            raise ImportInProgressError(msg)
        before = self._copy()
        handle = Transaction(commit=commit)
        try:
            yield handle
        except BaseException:
            self._restore(before)
            raise
        if not handle.commit:
            self._restore(before)

    async def apply_rows[Row](
        self, rows: Sequence[Row], apply: Callable[[Row], Awaitable[None]]
    ) -> list[RowRefusal]:
        refused: list[RowRefusal] = []
        start = 0
        while start < len(rows):
            end = min(start + self.batch_size, len(rows))
            position = start
            saved = self._copy()
            try:
                for position in range(start, end):
                    self.applied += 1
                    await apply(rows[position])
            except self.NOT_THE_ROWS:
                raise
            except DomainError as error:
                self._restore(saved)  # ROLLBACK TO SAVEPOINT
                refused.append(RowRefusal(position, error))
                if len(refused) > MAX_REFUSED_ROWS:
                    msg = f"more than {MAX_REFUSED_ROWS} rows were refused"
                    raise TooManyRefusedRowsError(msg) from error
                # The replay, without a savepoint. Like the real `apply_rows`, a row refused the
                # second time condemns the import. Database errors (IntegrityError, DataError)
                # are not raised by this double: `tests/integration/test_exchange_import.py`
                # and `test_unit_of_work.py` cover them against PostgreSQL.
                for index in range(start, position):
                    self.applied += 1
                    self.replayed += 1
                    try:
                        await apply(rows[index])
                    except self.NOT_THE_ROWS:
                        raise
                    except DomainError as again:
                        msg = "rows applied once were refused the second time"
                        raise WriteConflictError(msg) from again
                start = position + 1
            else:
                start = end
        return refused

    async def known_elements(self) -> tuple[Element, ...]:
        return tuple(self.repository.elements.values())

    async def known_relationship_keys(self) -> frozenset[tuple[R, UUID, UUID]]:
        return frozenset(
            (link.relationship_type, link.source_id, link.target_id)
            for link in self.repository.relationships.values()
        )

    async def lock_elements(self, ids: Iterable[UUID]) -> None:
        self.locked.extend(sorted(set(ids)))


class InMemoryObjectStore:
    """The `ObjectStore` port over a dict — folders computed from keys, like S3."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def _stored(self, key: str) -> StoredFile:
        data, content_type = self.objects[key]
        return StoredFile(
            key=key,
            size=len(data),
            last_modified=FIXED_NOW,
            content_type=content_type,
            # Hashed rather than counted, so that replacing a file changes the
            # etag exactly when its bytes changed — which is what the real store
            # reports and what `note_seen` reads. MinIO's own etag is an MD5;
            # what matters to the double is that it moves with the bytes.
            etag=hashlib.sha256(data).hexdigest()[:32],
        )

    async def list_folder(self, prefix: str, *, limit: int) -> FileListing:
        folders: dict[str, None] = {}
        files: list[StoredFile] = []
        for key in sorted(self.objects):
            if not key.startswith(prefix):
                continue
            rest = key[len(prefix) :]
            if "/" in rest:
                folders[f"{prefix}{rest.split('/', 1)[0]}/"] = None
            else:
                files.append(self._stored(key))
        kept_folders = tuple(folders)[:limit]
        kept_files = tuple(files)[: limit - len(kept_folders)]
        truncated = len(folders) + len(files) > limit
        return FileListing(prefix, kept_folders, kept_files, truncated)

    async def stat(self, key: str) -> StoredFile | None:
        return self._stored(key) if key in self.objects else None

    async def put(self, key: str, data: bytes, *, content_type: str) -> StoredFile:
        self.objects[key] = (data, content_type)
        return self._stored(key)

    async def open(self, key: str) -> tuple[StoredFile, AsyncIterator[bytes]]:
        if key not in self.objects:
            msg = f"no file at {key!r}"
            raise StoredFileNotFoundError(msg)
        data = self.objects[key][0]

        async def chunks() -> AsyncIterator[bytes]:
            yield data

        return self._stored(key), chunks()

    async def walk(self, prefix: str = "") -> AsyncIterator[StoredFile]:
        for key in sorted(self.objects):
            if key.startswith(prefix):
                yield self._stored(key)

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class InMemoryFileMetadata:
    """The `FileMetadataRepository` port over a dict, keyed by path like the table.

    It copies the two upserts of `repositories/file_metadata_store.py` — what
    each one keeps is the behaviour under test, so a double that kept more
    would prove the service right about a repository that does not exist.
    """

    def __init__(self) -> None:
        self.rows: dict[str, FileMetadata] = {}

    async def record(self, metadata: FileMetadata) -> FileMetadata:
        existing = self.rows.get(metadata.key)
        stored = (
            metadata
            if existing is None
            else replace(
                metadata,
                id=existing.id,
                details=existing.details,
                created_at=existing.created_at,
            )
        )
        self.rows[metadata.key] = stored
        return stored

    async def note_seen(self, stored: StoredFile, *, now: datetime) -> FileMetadata:
        existing = self.rows.get(stored.key)
        if existing is None:
            seen = FileMetadata(
                id=uuid4(),
                key=stored.key,
                size=stored.size,
                content_type=stored.content_type,
                etag=stored.etag,
                sha256=None,
                last_modified=stored.last_modified,
                created_at=now,
                updated_at=now,
            )
        else:
            unchanged = bool(existing.etag) and existing.etag == stored.etag
            seen = replace(
                existing,
                size=stored.size,
                content_type=stored.content_type,
                etag=stored.etag,
                sha256=existing.sha256 if unchanged else None,
                last_modified=stored.last_modified,
                updated_at=now,
            )
        self.rows[stored.key] = seen
        return seen

    async def get(self, key: str) -> FileMetadata | None:
        return self.rows.get(key)

    async def for_keys(self, keys: Sequence[str]) -> dict[str, FileMetadata]:
        return {key: self.rows[key] for key in keys if key in self.rows}

    async def describe(
        self, key: str, details: FileDetails, *, now: datetime
    ) -> FileMetadata | None:
        existing = self.rows.get(key)
        if existing is None:
            return None
        described = replace(existing, details=details, updated_at=now)
        self.rows[key] = described
        return described

    async def forget(self, key: str) -> bool:
        return self.rows.pop(key, None) is not None

    async def all_keys(self) -> tuple[str, ...]:
        return tuple(sorted(self.rows))

    async def with_digest(self, sha256: str) -> tuple[FileMetadata, ...]:
        return tuple(self.rows[key] for key in sorted(self.rows) if self.rows[key].sha256 == sha256)


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
def audit_log() -> InMemoryAuditLog:
    return InMemoryAuditLog()


@pytest.fixture
def repository(audit_log: InMemoryAuditLog) -> InMemoryRepository:
    return InMemoryRepository(audit_log)


@pytest.fixture
def documents(audit_log: InMemoryAuditLog, repository: InMemoryRepository) -> InMemoryDocuments:
    return InMemoryDocuments(audit_log, repository)


@pytest.fixture
def diagrams(audit_log: InMemoryAuditLog) -> InMemoryDiagrams:
    return InMemoryDiagrams(audit_log)


@pytest.fixture
def service(repository: InMemoryRepository) -> ArchitectureService:
    """The service wired to the in-memory graph and to a clock that never moves."""
    return ArchitectureService(repository, clock=lambda: FIXED_NOW)


@pytest.fixture
def packages(repository: InMemoryRepository, diagrams: InMemoryDiagrams) -> InMemoryPackages:
    return InMemoryPackages(repository, diagrams)


@pytest.fixture
def package_service(packages: InMemoryPackages) -> PackageService:
    return PackageService(packages, clock=lambda: FIXED_NOW)


@pytest.fixture
def baseline_store(
    repository: InMemoryRepository,
    diagrams: InMemoryDiagrams,
    documents: InMemoryDocuments,
    packages: InMemoryPackages,
) -> InMemoryBaselines:
    return InMemoryBaselines(repository, diagrams, documents, packages)


@pytest.fixture
def baseline_service(baseline_store: InMemoryBaselines) -> BaselineService:
    return BaselineService(baseline_store, clock=lambda: FIXED_NOW)


@pytest.fixture
def saved_queries(audit_log: InMemoryAuditLog) -> InMemorySavedQueries:
    return InMemorySavedQueries(audit_log)


@pytest.fixture
def saved_query_service(
    saved_queries: InMemorySavedQueries,
    packages: InMemoryPackages,
    service: ArchitectureService,
) -> SavedQueryService:
    return SavedQueryService(saved_queries, packages, service, clock=lambda: FIXED_NOW)


@pytest.fixture
def exchange_store(repository: InMemoryRepository) -> InMemoryExchange:
    return InMemoryExchange(repository)


@pytest.fixture
def exchange_service(
    exchange_store: InMemoryExchange, service: ArchitectureService
) -> ExchangeService:
    return ExchangeService(exchange_store, service, clock=lambda: FIXED_NOW)


@pytest.fixture
def diagram_service(
    diagrams: InMemoryDiagrams, service: ArchitectureService, packages: InMemoryPackages
) -> DiagramService:
    return DiagramService(diagrams, service, packages=packages, clock=lambda: FIXED_NOW)


@pytest.fixture
def ipam(repository: InMemoryRepository, service: ArchitectureService) -> IpamService:
    """The IP use cases over the same graph double, which answers both ports.

    `InMemoryRepository` satisfies `IpamRepository` as well as
    `ArchitectureRepository`, exactly as the PostgreSQL class does — an address is
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


@pytest.fixture
def files_store() -> InMemoryObjectStore:
    return InMemoryObjectStore()


@pytest.fixture
def files_metadata() -> InMemoryFileMetadata:
    return InMemoryFileMetadata()


@pytest.fixture
def file_service(
    files_store: InMemoryObjectStore, files_metadata: InMemoryFileMetadata
) -> FileService:
    """The bucket and the catalogue beside it, on one frozen clock."""
    return FileService(files_store, metadata=files_metadata, clock=lambda: FIXED_NOW)
