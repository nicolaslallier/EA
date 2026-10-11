"""Use cases of the import and export of exchange files (docs/adr/0048).

Export reads one consistent snapshot, page after page, and streams. It writes
nothing, so it has neither an audit event nor a review guard: a reader may run it.
Import (a later lot) goes through `ArchitectureService`, so every ArchiMate and
IPAM rule is enforced for a file exactly as for a person.
"""

from __future__ import annotations

import asyncio
import logging
import time
import weakref
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.errors import ExchangeFileTooLargeError, ImportInProgressError, UnknownScopeError
from ea.domain.exchange import (
    EXPORT_PAGE_SIZE,
    EXPORT_TIMEOUT_SECONDS,
    MAX_EXCHANGE_BYTES,
    Category,
    ExchangeElement,
    ExchangeModel,
    ExchangeRelationship,
    ExportRequest,
    IdentifierRegistry,
    ImportContent,
    ImportOptions,
    ImportReport,
    Issue,
    ReportBuilder,
    Severity,
    exchange_element_of,
    exchange_relationship_of,
)
from ea.formats.archimate_xml import buffered, parse_model, render_model
from ea.services.caller import names_in_logs, require_caller, require_editor
from ea.services.exchange_import import ImportRun

if TYPE_CHECKING:
    from ea.domain.ports import ExchangeRepository, ExportSnapshot
    from ea.services.architecture import ArchitectureService

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
    ) -> None:
        self._parse = parser
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
        model = await self._parsed(content.main)
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
                await ImportRun(self._repository, self._architecture, options).run(model, report)
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

    async def _parsed(self, data: bytes) -> ExchangeModel:
        """Parse in a thread (seconds of pure CPU), one file at a time, with a short queue."""
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
        work = asyncio.ensure_future(asyncio.to_thread(self._parse, data))

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
        if request.scope.package_id is not None or request.scope.query_id is not None:
            msg = "this scope is not available: only the whole model can be exported"
            raise UnknownScopeError(msg)
        return Export(
            filename=f"ea-{request.scope.label}-{self._now():%Y-%m-%d}.xml",
            media_type="application/xml",
            chunks=self._xml(request),
        )

    async def _xml(self, request: ExportRequest) -> AsyncIterator[bytes]:
        deadline = self._monotonic() + EXPORT_TIMEOUT_SECONDS
        registry = IdentifierRegistry()
        identifiers: dict[UUID, str] = {}
        counts = {"elements": 0, "relationships": 0}
        dropped: dict[str, int] = {}

        def within_budget() -> None:
            if self._monotonic() > deadline:
                msg = f"the export ran past its {EXPORT_TIMEOUT_SECONDS:.0f} s budget"
                raise TimeoutError(msg)

        async with self._repository.snapshot(request.scope) as snapshot:

            async def elements() -> AsyncIterator[ExchangeElement]:
                offset = 0
                while page := await snapshot.elements_page(offset, EXPORT_PAGE_SIZE):
                    within_budget()
                    for element in page:
                        item = exchange_element_of(element, registry)
                        identifiers[element.id] = item.identifier
                        counts["elements"] += 1
                        yield item
                    offset += len(page)

            async def relationships() -> AsyncIterator[ExchangeRelationship]:
                async for item in _relationships(snapshot, identifiers, registry, within_budget):
                    counts["relationships"] += 1
                    yield item

            parts = render_model(
                "EA catalogue",
                elements(),
                relationships(),
                on_dropped=lambda identifier, count: dropped.__setitem__(identifier, count),
            )
            async for chunk in buffered(parts):
                yield chunk
        _report(request, counts, registry, dropped)


def _report(
    request: ExportRequest,
    counts: dict[str, int],
    registry: IdentifierRegistry,
    dropped: dict[str, int],
) -> None:
    logger.info(
        "model exported as ArchiMate XML",
        extra={"action": "exported", "format": request.format.value, **counts},
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


async def _relationships(
    snapshot: ExportSnapshot,
    identifiers: dict[UUID, str],
    registry: IdentifierRegistry,
    within_budget: Callable[[], None],
) -> AsyncIterator[ExchangeRelationship]:
    offset = 0
    while page := await snapshot.relationships_page(offset, EXPORT_PAGE_SIZE):
        within_budget()
        for link in page:
            # A link with an end outside the scope is not exported (spec §11).
            if link.source_id in identifiers and link.target_id in identifiers:
                yield exchange_relationship_of(
                    link,
                    source=identifiers[link.source_id],
                    target=identifiers[link.target_id],
                    registry=registry,
                )
        offset += len(page)
