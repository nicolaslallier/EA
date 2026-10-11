"""Use cases of the import and export of exchange files (docs/adr/0048).

Export reads one consistent snapshot, page after page, and streams. It writes
nothing, so it has neither an audit event nor a review guard: a reader may run it.
Import (a later lot) goes through `ArchitectureService`, so every ArchiMate and
IPAM rule is enforced for a file exactly as for a person.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import time
import weakref
import zipfile
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ea.domain.errors import (
    DomainError,
    ExchangeFileTooLargeError,
    ExportInProgressError,
    ExportTooLargeError,
    ImportInProgressError,
    UnknownScopeError,
)
from ea.domain.exchange import (
    CSV_FILES,
    EXPORT_LEASE_SECONDS,
    EXPORT_PAGE_SIZE,
    EXPORT_TIMEOUT_SECONDS,
    MAX_EXCHANGE_BYTES,
    MAX_EXPORT_BYTES,
    MAX_EXPORTS_IN_FLIGHT,
    MAX_EXPORTS_PER_CALLER,
    MAX_IMPORTS_IN_FLIGHT,
    MAX_NEW_FOLDERS,
    Category,
    ExchangeBox,
    ExchangeElement,
    ExchangeFormat,
    ExchangeModel,
    ExchangeRelationship,
    ExchangeView,
    ExportRequest,
    ExportScope,
    IdentifierRegistry,
    ImportContent,
    ImportOptions,
    ImportReport,
    Issue,
    ReportBuilder,
    ScopeKind,
    Severity,
    build_organization,
    exchange_element_of,
    exchange_relationship_of,
    xml_identifier,
)
from ea.domain.packages import descendants_of
from ea.formats.archimate_xml import buffered, parse_model, render_model
from ea.formats.csv_files import parse_csv, render_csv
from ea.services.caller import names_in_logs, require_caller, require_editor
from ea.services.exchange_admission import Admission, current_lease
from ea.services.exchange_import import ImportRun

if TYPE_CHECKING:
    from ea.domain.exchange import PackageNode
    from ea.domain.ports import ExchangeRepository, ExportSnapshot
    from ea.domain.query import ElementQuery
    from ea.services.architecture import ArchitectureService
    from ea.services.diagrams import DiagramService
    from ea.services.packages import PackageService
    from ea.services.saved_queries import SavedQueryService

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
#: Parsings allowed in the process at once, the one running included: a 20 MB file is
#: hundreds of megabytes while it is read, so one reads and one waits, and a third is told
#: to come back (409), never queued without end.
MAX_PARSES_QUEUED = MAX_IMPORTS_IN_FLIGHT


@dataclass(slots=True)
class _Gate:
    slot: asyncio.Semaphore
    queued: int = 0


#: One gate per event loop (a semaphore belongs to the loop that first waits on it).
_gates: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Gate] = weakref.WeakKeyDictionary()


def _gate() -> _Gate:
    loop = asyncio.get_running_loop()
    if loop not in _gates:
        _gates[loop] = _Gate(asyncio.Semaphore(1))
    return _gates[loop]


#: How many object identifiers a warning names: enough to find the culprits, not a dump.
WARNED_IDENTIFIERS = 20


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Export:
    """A download: its name and type are fixed before the first byte is produced."""

    filename: str
    media_type: str
    chunks: AsyncIterator[bytes]
    skipped_views: int = 0
    #: CSV only: `safe` (formulas neutralised) or `raw`; None for the XML.
    cells: str | None = None
    #: Bytes of the file, known before the first is sent.
    size: int = 0


class ExchangeService:
    """Import and export of the model as ArchiMate exchange files."""

    def __init__(
        self,
        repository: ExchangeRepository,
        architecture: ArchitectureService,
        *,
        max_bytes: int = MAX_EXCHANGE_BYTES,
        clock: Clock = _utc_now,
        monotonic: Callable[[], float] = time.monotonic,
        parser: Callable[[bytes], ExchangeModel] = parse_model,
        diagrams: DiagramService | None = None,
        packages: PackageService | None = None,
        queries: SavedQueryService | None = None,
        max_new_folders: int = MAX_NEW_FOLDERS,
        max_export_bytes: int = MAX_EXPORT_BYTES,
        max_exports: int = MAX_EXPORTS_IN_FLIGHT,
        exports_per_caller: int = MAX_EXPORTS_PER_CALLER,
        export_lease_seconds: float = EXPORT_LEASE_SECONDS,
        imports: Admission | None = None,
    ) -> None:
        self._imports = imports
        self._max_new_folders = max_new_folders
        self._max_export_bytes = max_export_bytes
        # Exports prepared or being sent, apart from the imports: a download holds no
        # connection, but it holds a file and a place (docs/adr/0048).
        self._exports = Admission(
            max_exports,
            per_caller=exports_per_caller,
            lease_seconds=export_lease_seconds,
            refusal=lambda: ExportInProgressError(
                "other exports are being prepared or sent: try again in a moment"
            ),
            kind="export",
            clock=monotonic,
        )
        self._queries = queries
        self._parse = parser
        self._diagrams = diagrams
        self._packages = packages
        self._repository = repository
        self._architecture = architecture
        self._max_bytes = max_bytes
        self._now = clock
        self._monotonic = monotonic

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    async def import_exchange(self, content: ImportContent, options: ImportOptions) -> ImportReport:
        """Apply (or simulate) an exchange file through the architecture service.

        Editors only, *dry run included*: the simulation really performs the writes, then
        rolls them back — that is what makes it honest. The file is read before the
        transaction opens, so a database connection is never held while parsing.

        Through HTTP the place among the imports in flight was taken by the door, before the
        body was read. A caller that comes by another way (the MCP tool, a script) takes it
        here, from the same admission, and gives it back when the analysis thread has ended.
        """
        caller = require_editor()  # a reader takes no place
        async with AsyncExitStack() as stack:
            if self._imports is not None and current_lease.get() is None:
                lease = await stack.enter_async_context(self._imports.lease(caller.subject))
                marked = current_lease.set(lease)
                stack.callback(current_lease.reset, marked)
            return await self._import(caller.subject, content, options)

    async def _import(
        self, actor: str, content: ImportContent, options: ImportOptions
    ) -> ImportReport:
        started = self._monotonic()
        if content.size > self._max_bytes:
            msg = f"an exchange file is at most {self._max_bytes} bytes"
            raise ExchangeFileTooLargeError(msg)
        model = await self._parsed(lambda: self._read(content, options))
        report = ReportBuilder()
        for issue in model.issues:
            report.add(
                issue,
                Category.RELATIONSHIPS
                if (issue.path or "").startswith("rel")
                else Category.ELEMENTS,
            )
        async with self._repository.transaction(commit=not options.dry_run) as transaction:
            quiet = names_in_logs.set(False)  # the rows' log lines must not quote the file
            try:
                await ImportRun(
                    self._repository,
                    self._architecture,
                    options,
                    diagrams=self._diagrams,
                    packages=self._packages,
                    max_new_folders=self._max_new_folders,
                ).run(model, report)
            finally:
                names_in_logs.reset(quiet)
            if options.strict and report.has_rejections():
                transaction.commit = False
                report.add(
                    Issue(
                        Severity.ERROR,
                        "strict_refused",
                        "strict mode: at least one row was refused, so nothing was written",
                    ),
                    always=True,
                )
        built = report.build(
            dry_run=options.dry_run,
            applied=transaction.commit and not options.dry_run,
            format=content.format,
            sha256=content.sha256,
            duration_ms=round((self._monotonic() - started) * 1000),
        )
        self._log_import(actor, content, built)
        return built

    def _read(self, content: ImportContent, options: ImportOptions) -> ExchangeModel:
        """Turn the bytes into an exchange model, whatever the format. Runs in a thread."""
        if content.format is ExchangeFormat.CSV:
            return parse_csv(
                content.main, content.relations, content.properties, safe_cells=options.safe_cells
            )
        return self._parse(content.main)

    async def _parsed(self, read: Callable[[], ExchangeModel]) -> ExchangeModel:
        """Read in a thread (seconds of pure CPU), one file at a time, with a short queue."""
        gate = _gate()
        if gate.queued >= MAX_PARSES_QUEUED:
            msg = "other files are being read: try again in a moment"
            raise ImportInProgressError(msg)
        gate.queued += 1
        try:
            await gate.slot.acquire()
        except BaseException:  # cancelled while waiting: nothing was taken, give the place back
            gate.queued -= 1
            raise
        work = asyncio.ensure_future(asyncio.to_thread(read))
        if (lease := current_lease.get()) is not None:
            lease.hold(work)  # the request's place outlives a client that hangs up

        def finished(done: asyncio.Future[ExchangeModel]) -> None:
            # A thread cannot be stopped: the place is given back when it *ends*, even if
            # the request that started it was cancelled, or two big parsings would overlap.
            gate.slot.release()
            gate.queued -= 1
            if not done.cancelled():
                done.exception()  # seen, so an abandoned failure is not reported as unread

        work.add_done_callback(finished)
        return await asyncio.shield(work)

    @staticmethod
    def _log_import(actor: str, content: ImportContent, report: ImportReport) -> None:
        # `rows_*` and not `created`: that is a `LogRecord` attribute (its timestamp),
        # and `extra=` refuses a key that collides with one. Counts only, never the content.
        totals = {
            f"rows_{outcome}": sum(getattr(counts, outcome) for counts in report.counts.values())
            for outcome in ("created", "reused", "rejected", "ignored")
        }
        logger.info(
            "exchange file imported",
            extra={
                "action": "imported",
                "dry_run": report.dry_run,
                "applied": report.applied,
                "format": report.format.value,
                "size_bytes": content.size,
                "sha256": report.sha256,
                "duration_ms": report.duration_ms,
                "actor": actor,
                **totals,
            },
        )

    async def export(self, request: ExportRequest) -> Export:
        """Prepare a download (see `_prepare`)."""
        require_caller()
        return await self._prepare(request, text=False, ceiling=self._max_export_bytes)

    async def export_text(self, request: ExportRequest, *, ceiling: int) -> Export:
        """The export as text an agent can read: the XML, or the three CSV files one after the
        other under `--- elements.csv ---` and so on. Refused past `ceiling` bytes while it is
        rendered, not after: a model too big for the answer is not rendered whole to find out."""
        require_caller()
        return await self._prepare(request, text=True, ceiling=min(ceiling, self._max_export_bytes))

    async def _prepare(self, request: ExportRequest, *, text: bool, ceiling: int) -> Export:
        """Prepare a download: the whole file is rendered into a temporary file, the snapshot is
        given back, and only the file is left to send.

        A client that reads slowly then holds a descriptor and a place (`MAX_EXPORTS_IN_FLIGHT`),
        never a connection of the pool: ten slow downloads would otherwise stop every other
        request of the API. The place is taken before the snapshot is opened and given back with
        the file, whatever ends the download — or the preparation.
        """
        caller = require_caller()
        # Before any connection: a refusal opens nothing. The lease is entered here and left by
        # the download's `aclose` (or below if the preparation fails): one context manager.
        places = AsyncExitStack()
        await places.enter_async_context(self._exports.lease(caller.subject))
        files: list[Any] = []
        stack = AsyncExitStack()
        try:
            request = replace(request, scope=await self._resolve(request.scope))
            is_csv = request.format is ExchangeFormat.CSV
            wants_views = request.with_views and not is_csv
            # The snapshot is opened here, not when the first byte is asked for: the header that
            # counts the views left out and the file that leaves them out are one reading of
            # the database, and a saved query that cannot run fails before any header is sent.
            snapshot = await stack.enter_async_context(self._repository.snapshot(request.scope))
            if request.scope.query is not None:
                await snapshot.check_scope()
            skipped = await snapshot.skipped_views() if wants_views else 0
            streams = _Streams(snapshot, self._monotonic)
            out = tempfile.SpooledTemporaryFile(max_size=1 << 20)  # noqa: SIM115
            files.append(out)
            dropped: dict[str, int] = {}
            parts: dict[str, Any] = {}
            if is_csv:
                parts = {n: tempfile.SpooledTemporaryFile(max_size=1 << 20) for n in CSV_FILES}  # noqa: SIM115
                files.extend(parts.values())
                await self._render_csv(request, streams, parts, ceiling)
            else:
                await self._render_xml(request, streams, snapshot, out, dropped, ceiling)
            await stack.aclose()  # the connection goes back before a byte goes out
            if is_csv:
                await asyncio.to_thread(_text_into if text else _zip_into, parts, out)
            size = out.tell()
            out.seek(0)
            for spare in files[1:]:
                spare.close()
            _report(request, streams, dropped)
        except BaseException:
            await stack.aclose()
            for opened in files:
                opened.close()
            await places.aclose()
            raise
        raw = is_csv and not request.safe_cells
        label = "-raw" if raw else ""
        extension = ("txt" if text else "zip") if is_csv else "xml"
        media = ("text/plain" if text else "application/zip") if is_csv else "application/xml"
        return Export(
            filename=f"ea-{request.scope.label}-{self._now():%Y-%m-%d}{label}.{extension}",
            cells=("raw" if raw else "safe") if is_csv else None,
            media_type=media,
            chunks=_FileChunks(out, size, places),
            skipped_views=skipped,
            size=size,
        )

    async def _resolve(self, scope: ExportScope) -> ExportScope:
        """Turn the ids a client sent into what the snapshot filters on.

        The client names a package or a saved query; it never supplies the filter itself.
        Whatever cannot be resolved (an unknown package, a saved query that is gone, was
        written by another version or names a package that vanished) is the same typed
        refusal: a 422 `unknown_scope`, with no word of what was stored.
        """
        has_package, has_query = scope.package_id is not None, scope.query_id is not None
        if (scope.kind is ScopeKind.MODEL and (has_package or has_query)) or (
            scope.kind is ScopeKind.PACKAGE and (not has_package or has_query)
        ):
            msg = "this scope takes its own id: `package_id` for a package, `query_id` for a query"
            raise UnknownScopeError(msg)
        if scope.kind is ScopeKind.QUERY and (not has_query or has_package):
            msg = "this scope takes its own id: `package_id` for a package, `query_id` for a query"
            raise UnknownScopeError(msg)
        if scope.kind is ScopeKind.PACKAGE and scope.package_id is not None:
            return replace(scope, package_ids=await self._subtree(scope))
        if scope.kind is ScopeKind.QUERY and scope.query_id is not None:
            return replace(scope, query=await self._saved_query(scope.query_id))
        return scope

    async def _subtree(self, scope: ExportScope) -> frozenset[UUID]:
        if self._packages is None or scope.package_id is None:
            msg = "packages are not available on this deployment"
            raise UnknownScopeError(msg)
        tree = {s.package.id: s.package for s in await self._packages.list_packages()}
        if scope.package_id not in tree:
            msg = "no such package"
            raise UnknownScopeError(msg)
        below = descendants_of(scope.package_id, tree) if scope.include_subpackages else frozenset()
        return frozenset({scope.package_id, *below})

    async def _saved_query(self, query_id: UUID) -> ElementQuery:
        if self._queries is None:
            msg = "saved queries are not available on this deployment"
            raise UnknownScopeError(msg)
        try:
            query = (await self._queries.get(query_id)).query
            tree = await self._packages.list_packages() if self._packages is not None else ()
        except (DomainError, ValueError, KeyError, TypeError):
            # The stored text is a person's and the reason is not for the caller.
            msg = "no such saved query, or it cannot be read"
            raise UnknownScopeError(msg) from None
        if query.package_id is not None and query.package_id not in {s.package.id for s in tree}:
            msg = "the saved query names a package that no longer exists"
            raise UnknownScopeError(msg)
        return query

    async def _render_xml(
        self,
        request: ExportRequest,
        streams: _Streams,
        snapshot: ExportSnapshot,
        out: Any,
        dropped: dict[str, int],
        ceiling: int,
    ) -> None:
        streams.packages = await snapshot.packages()
        parts = render_model(
            "EA catalogue",
            streams.elements(),
            streams.relationships(),
            on_dropped=lambda identifier, count: dropped.__setitem__(identifier, count),
            views=streams.views(request.with_views),
            organizations=lambda: build_organization(streams.packages, streams.members),
        )
        written = 0
        async for chunk in buffered(parts):
            written += len(chunk)
            self._within_the_ceiling(written, ceiling)
            out.write(chunk)

    async def _render_csv(
        self, request: ExportRequest, streams: _Streams, parts: dict[str, Any], ceiling: int
    ) -> None:
        written = 0
        async for name, text in render_csv(
            streams.elements(), streams.relationships(), safe_cells=request.safe_cells
        ):
            data = text.encode("utf-8")
            written += len(data)
            self._within_the_ceiling(written, ceiling)
            parts[name].write(data)

    @staticmethod
    def _within_the_ceiling(written: int, ceiling: int) -> None:
        if written > ceiling:
            msg = (
                f"this export is past {ceiling} bytes: ask for a narrower scope "
                "(a package, or a saved query)"
            )
            raise ExportTooLargeError(msg)


def _zip_into(parts: dict[str, Any], out: Any) -> None:
    """The three files as one archive, fixed in date and order: a pure function of the model.
    Synchronous work on files: it runs in a thread, never on the event loop."""
    with zipfile.ZipFile(out, "w") as bundle:
        for name in CSV_FILES:  # the names are ours, never a cell's
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            parts[name].seek(0)
            with bundle.open(info, "w") as entry:
                shutil.copyfileobj(parts[name], entry)


def _text_into(parts: dict[str, Any], out: Any) -> None:
    """The three files one after the other, each under its name: what an agent reads."""
    for name in CSV_FILES:
        out.write(f"--- {name} ---\n".encode())
        parts[name].seek(0)
        shutil.copyfileobj(parts[name], out)


_SEND_CHUNK = 65_536


class _FileChunks:
    """The body of a download: a temporary file read 64 KB at a time.

    It gives the file and the place back however the download ends — read to the end, left by
    the client, cancelled, or never started — because both are released by `aclose`, which the
    response always calls, and not by a generator's `finally` (which a generator never started
    does not have).
    """

    def __init__(self, source: Any, size: int, places: AsyncExitStack) -> None:
        self._source = source
        self.size = size
        self._places = places
        self.closed = False

    def __aiter__(self) -> _FileChunks:
        return self

    async def __anext__(self) -> bytes:
        if self.closed:
            raise StopAsyncIteration
        data = self._source.read(_SEND_CHUNK)
        if not data:
            await self.aclose()
            raise StopAsyncIteration
        return bytes(data)

    async def aclose(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                self._source.close()
            finally:
                await self._places.aclose()


class _Streams:
    """The two paged iterators of one export. The second needs what the first learns:
    a link is written with the identifiers of its ends, so elements are read first.
    One registry for both, so that no two objects share an identifier in the file."""

    def __init__(self, snapshot: ExportSnapshot, monotonic: Callable[[], float]) -> None:
        self._snapshot = snapshot
        self._monotonic = monotonic
        self._deadline = monotonic() + EXPORT_TIMEOUT_SECONDS
        self.registry = IdentifierRegistry()
        self.identifiers: dict[UUID, str] = {}
        #: The identifiers of the elements by package (`None`: unfiled), for `organizations`.
        self.members: dict[UUID | None, list[str]] = {}
        self.packages: tuple[PackageNode, ...] = ()
        self.counts = {"elements": 0, "relationships": 0, "views": 0}
        self.skipped_views = 0

    def _within_budget(self) -> None:
        if self._monotonic() > self._deadline:
            msg = f"the export ran past its {EXPORT_TIMEOUT_SECONDS:.0f} s budget"
            raise TimeoutError(msg)

    async def elements(self) -> AsyncIterator[ExchangeElement]:
        offset = 0
        while page := await self._snapshot.elements_page(offset, EXPORT_PAGE_SIZE):
            self._within_budget()
            for element in page:
                item = exchange_element_of(element, self.registry)
                self.identifiers[element.id] = item.identifier
                self.members.setdefault(element.package_id, []).append(item.identifier)
                self.counts["elements"] += 1
                yield item
            offset += len(page)

    async def relationships(self) -> AsyncIterator[ExchangeRelationship]:
        offset = 0
        while page := await self._snapshot.relationships_page(offset, EXPORT_PAGE_SIZE):
            self._within_budget()
            for link in page:
                # A link with an end outside the scope is not exported (spec §11).
                if link.source_id in self.identifiers and link.target_id in self.identifiers:
                    self.counts["relationships"] += 1
                    yield exchange_relationship_of(
                        link,
                        source=self.identifiers[link.source_id],
                        target=self.identifiers[link.target_id],
                        registry=self.registry,
                    )
            offset += len(page)

    async def views(self, with_views: bool) -> AsyncIterator[ExchangeView]:
        if not with_views:
            return
        offset = 0
        while page := await self._snapshot.views_page(offset, EXPORT_PAGE_SIZE):
            self._within_budget()
            for view in page:
                # A view whose boxes are not all in the export would be a different picture.
                if any(box.element_id not in self.identifiers for box in view.boxes):
                    self.skipped_views += 1
                    continue
                self.counts["views"] += 1
                yield ExchangeView(
                    identifier=self.registry.claim(xml_identifier(f"view-{view.name}")),
                    name=view.name,
                    description=view.description,
                    boxes=tuple(
                        ExchangeBox(self.identifiers[b.element_id], b.x, b.y, b.width, b.height)
                        for b in view.boxes
                    ),
                )
            offset += len(page)


def _report(request: ExportRequest, streams: _Streams, dropped: dict[str, int]) -> None:
    registry = streams.registry
    logger.info(
        "model exported",
        extra={"action": "exported", "format": request.format.value, **streams.counts},
    )
    if registry.replaced or dropped:
        # Never the text itself: only counts and the identifiers to look for.
        logger.warning(
            "export adjusted: some content could not be written as is",
            extra={
                "action": "export_adjusted",
                "identifiers_replaced": len(registry.replaced),
                "replaced_ids": [str(u) for u in registry.replaced[:WARNED_IDENTIFIERS]],
                "characters_dropped": sum(dropped.values()),
                "objects_cleaned": list(dropped)[:WARNED_IDENTIFIERS],
            },
        )
