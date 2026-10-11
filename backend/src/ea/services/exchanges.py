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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.errors import (
    DomainError,
    ExchangeFileTooLargeError,
    ImportInProgressError,
    UnknownScopeError,
)
from ea.domain.exchange import (
    CSV_FILES,
    EXPORT_PAGE_SIZE,
    EXPORT_TIMEOUT_SECONDS,
    MAX_EXCHANGE_BYTES,
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
from ea.services.exchange_admission import current_ticket
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
MAX_PARSES_QUEUED = 2


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
    ) -> None:
        self._max_new_folders = max_new_folders
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
        """
        caller = require_editor()
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
        self._log_import(caller.subject, content, built)
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
        if (ticket := current_ticket.get()) is not None:
            ticket.hold(work)  # the request's place outlives a client that hangs up

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
        require_caller()
        request = replace(request, scope=await self._resolve(request.scope))
        is_csv = request.format is ExchangeFormat.CSV
        extension = "zip" if is_csv else "xml"
        raw = is_csv and not request.safe_cells
        label = "-raw" if raw else ""
        wants_views = request.with_views and not is_csv
        return Export(
            filename=f"ea-{request.scope.label}-{self._now():%Y-%m-%d}{label}.{extension}",
            cells=("raw" if raw else "safe") if is_csv else None,
            media_type="application/zip" if is_csv else "application/xml",
            chunks=self._csv(request) if is_csv else self._xml(request),
            skipped_views=(
                await self._repository.count_skipped_views(request.scope) if wants_views else 0
            ),
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

    async def _xml(self, request: ExportRequest) -> AsyncIterator[bytes]:
        dropped: dict[str, int] = {}
        async with self._repository.snapshot(request.scope) as snapshot:
            streams = _Streams(snapshot, self._monotonic)
            streams.packages = await snapshot.packages()
            parts = render_model(
                "EA catalogue",
                streams.elements(),
                streams.relationships(),
                on_dropped=lambda identifier, count: dropped.__setitem__(identifier, count),
                views=streams.views(request.with_views),
                organizations=lambda: build_organization(streams.packages, streams.members),
            )
            async for chunk in buffered(parts):
                yield chunk
        _report(request, streams, dropped)

    async def _csv(self, request: ExportRequest) -> AsyncIterator[bytes]:
        # ponytail: three spooled files then one archive, written synchronously on the
        # loop: fine to the model's size today; stream a zip64 writer if exports outgrow it.
        spools = {name: tempfile.SpooledTemporaryFile(max_size=1 << 20) for name in CSV_FILES}  # noqa: SIM115
        archive = tempfile.SpooledTemporaryFile(max_size=1 << 20)  # noqa: SIM115
        try:
            async with self._repository.snapshot(request.scope) as snapshot:
                streams = _Streams(snapshot, self._monotonic)
                async for name, text in render_csv(
                    streams.elements(), streams.relationships(), safe_cells=request.safe_cells
                ):
                    spools[name].write(text.encode("utf-8"))
            with zipfile.ZipFile(archive, "w") as bundle:
                for name in CSV_FILES:  # the names are ours, never a cell's
                    # A fixed date: the archive is a pure function of the model.
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    spools[name].seek(0)
                    with bundle.open(info, "w") as entry:
                        shutil.copyfileobj(spools[name], entry)
            archive.seek(0)
            while chunk := archive.read(65_536):
                yield chunk
            _report(request, streams, {})
        finally:
            archive.close()
            for spool in spools.values():
                spool.close()


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
