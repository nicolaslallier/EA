"""Use cases over reports: read them (any caller) or deposit them (an editor).

A report is a photograph: `build` reads the catalogue once, builds the neutral
`ReportModel` with a pure function, and serialises it. Nothing is cached, so
nothing can leak between two callers' rights; and a report holds nothing a direct
read by the same caller would not have shown, because every read goes through the
same services. See docs/adr/0062.

**Every ceiling is checked before the read it bounds** — counts from the package
summaries, `node_count` from the diagram header, a `LIMIT` one past the cap on the
links — and a scope that passes one is a typed 422, never a truncation. The heavy
part (assembling the model, drawing, serialising) runs off the event loop under a
place in a global `Admission` (one report per caller): a thread cannot be killed, so
the place is kept until the thread has really ended (`Lease.hold`), exactly as an
analysis does (docs/adr/0057).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from ea.domain.errors import (
    DocumentNotFoundError,
    ElementNotFoundError,
    FileStorageUnavailableError,
    PackageNotFoundError,
    ReportBusyError,
    ReportFormatUnavailableError,
    ReportTooLargeError,
    UnsupportedReportSubjectError,
)
from ea.domain.files import FileDetails
from ea.domain.packages import descendants_of
from ea.domain.ports import ElementFilter
from ea.domain.report_html import to_html
from ea.domain.reports import (
    BUILT_IN_TEMPLATES,
    CONTENT_TYPES,
    MAX_REPORT_BOXES,
    MAX_REPORT_DIAGRAMS,
    MAX_REPORT_DOCUMENT_BYTES,
    MAX_REPORT_ELEMENTS,
    MAX_REPORT_RELATIONSHIPS,
    REPORT_FORMATS,
    REPORT_TIMEOUT_SECONDS,
    RenderedReport,
    Renderer,
    ReportFormat,
    ReportModel,
    ReportRequest,
    ReportScope,
    ReportSubject,
    ReportTemplate,
    ScopedDocument,
    build_model,
    report_filename,
    report_key,
    template_by_id,
)
from ea.services.analysis import off_the_loop
from ea.services.caller import require_caller, require_editor
from ea.services.exchange_admission import Admission, Lease

if TYPE_CHECKING:
    from ea.domain.diagrams import DiagramDetail
    from ea.domain.files import CatalogedFile
    from ea.services.architecture import ArchitectureService
    from ea.services.diagrams import DiagramService
    from ea.services.documents import DocumentService
    from ea.services.files import FileService
    from ea.services.packages import PackageService

Clock = Callable[[], datetime]
Monotonic = Callable[[], float]

logger = logging.getLogger(__name__)

#: How many reports may be built at once in this process, and how many one caller may hold.
MAX_REPORTS_IN_FLIGHT = 3
MAX_REPORTS_PER_CALLER = 1


def report_admission(
    *, limit: int = MAX_REPORTS_IN_FLIGHT, clock: Callable[[], float] = time.monotonic
) -> Admission:
    """The places for the reports being built (docs/adr/0062)."""
    return Admission(
        limit,
        per_caller=min(MAX_REPORTS_PER_CALLER, limit),
        lease_seconds=REPORT_TIMEOUT_SECONDS * 2,
        refusal=lambda: ReportBusyError("other reports are being built: try again in a moment"),
        kind="report",
        clock=clock,
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex[:8]


def _html_bytes(model: ReportModel) -> bytes:
    return to_html(model).encode()


def _assemble(
    renderer: Renderer,
    template: ReportTemplate,
    scope: ReportScope,
    documents: Sequence[ScopedDocument],
    now: datetime,
) -> bytes:
    """The CPU-bound part, in one function so one thread does all of it."""
    return renderer(build_model(template, scope, documents, now=now))


class ReportService:
    """The single entry point `api/` and `mcp/` use to produce reports.

    `documents` is `None` only on the test seam without a relational store; the
    documents section then reports none. `extra_renderers` holds the formats
    that need native libraries (PDF, DOCX): a format absent from it is a typed
    501, never a crash.
    """

    def __init__(
        self,
        diagrams: DiagramService,
        architecture: ArchitectureService,
        documents: DocumentService | None,
        files: FileService,
        *,
        packages: PackageService | None = None,
        admission: Admission | None = None,
        extra_renderers: Mapping[ReportFormat, Renderer] | None = None,
        clock: Clock = _utc_now,
        monotonic: Monotonic = time.monotonic,
        new_id: Callable[[], str] = _new_id,
        timeout_seconds: float = REPORT_TIMEOUT_SECONDS,
        max_relationships: int = MAX_REPORT_RELATIONSHIPS,
        app_version: str = "0.1.0",
    ) -> None:
        self._diagrams = diagrams
        self._architecture = architecture
        self._documents = documents
        self._files = files
        self._packages = packages
        self._admission = admission
        self._renderers: dict[ReportFormat, Renderer] = {"html": _html_bytes}
        self._renderers.update(extra_renderers or {})
        self._now = clock
        self._monotonic = monotonic
        self._new_id = new_id
        self._timeout = timeout_seconds
        self._max_relationships = max_relationships
        self._version = app_version

    @property
    def formats(self) -> tuple[ReportFormat, ...]:
        return tuple(fmt for fmt in REPORT_FORMATS if fmt in self._renderers)

    async def list_templates(self) -> tuple[ReportTemplate, ...]:
        require_caller()
        return tuple(BUILT_IN_TEMPLATES.values())

    async def build(self, request: ReportRequest) -> RenderedReport:
        """The report, as bytes. Reads only: nothing is written to the bucket."""
        caller = require_caller()
        rendered = await self._produce(request, caller.subject)
        self._log(request, rendered, deposited=False)
        return rendered

    async def deposit(self, request: ReportRequest) -> CatalogedFile:
        """Build the report, then store it under `reports/` with its catalogue record.

        One more file each time, never a replacement: the key holds the day and eight
        random hex digits, so regenerating a report keeps the true date of the first.
        The write goes through `FileService.upload` and nothing else (docs/adr/0036).
        """
        caller = require_editor()
        if not self._files.available:
            # Before the build: a report nobody can store is not worth its minutes.
            msg = "file storage is not configured on this deployment (EA_S3_ENABLED is off)"
            raise FileStorageUnavailableError(msg)
        rendered = await self._produce(request, caller.subject)
        now = self._now()
        template = template_by_id(request.template_id)
        name = report_filename(
            now=now,
            subject_name=rendered.subject_name,
            template_id=template.id,
            fmt=request.format,
            unique=self._new_id(),
        )
        title = f"Rapport {template.title} — {rendered.subject_name}"[:200]
        stored = await self._files.upload(
            report_key(name, now),
            rendered.content,
            content_type=rendered.content_type,
            details=FileDetails(
                title=title,
                description=(
                    f"{request.subject.kind}:{request.subject.id}; "
                    f"modèle {template.id} v{template.version}; application {self._version}"
                ),
                tags=("rapport", template.id),
            ),
        )
        self._log(request, rendered, deposited=True)
        return stored

    # --- internals ----------------------------------------------------------

    def _place(self, key: str) -> AbstractAsyncContextManager[Lease | None]:
        return self._admission.lease(key) if self._admission is not None else nullcontext(None)

    async def _produce(self, request: ReportRequest, subject_sub: str) -> RenderedReport:
        template = template_by_id(request.template_id)
        if request.subject.kind not in template.subjects:
            msg = (
                f"the template {template.id!r} takes {sorted(template.subjects)}, "
                f"not {request.subject.kind!r}"
            )
            raise UnsupportedReportSubjectError(msg)
        renderer = self._renderers.get(request.format)
        if renderer is None:
            msg = f"the {request.format} format is not available on this deployment; html is"
            raise ReportFormatUnavailableError(msg)
        started = self._monotonic()
        async with self._place(subject_sub) as lease:
            scope = await self._scope(request.subject)
            self._within_time(started)
            documents = await self._documents_of(scope) if template.needs_documents else ()
            self._within_time(started)
            now = self._now()
            content = await self._render(lease, started, renderer, template, scope, documents, now)
        return RenderedReport(
            content=content,
            content_type=CONTENT_TYPES[request.format],
            filename=report_filename(
                now=now, subject_name=scope.name, template_id=template.id, fmt=request.format
            ),
            duration_ms=int((self._monotonic() - started) * 1000),
            subject_name=scope.name,
        )

    async def _scope(self, subject: ReportSubject) -> ReportScope:
        if subject.kind == "diagram":
            return await self._diagram_scope(subject.id)
        if subject.kind == "package":
            return await self._package_scope(subject.id)
        msg = f"reports on a {subject.kind!r} are not available on this deployment"
        raise UnsupportedReportSubjectError(msg)

    def _too_many_links(self) -> ReportTooLargeError:
        msg = (
            f"a report covers at most {self._max_relationships} relationships; "
            "report on a smaller scope"
        )
        return ReportTooLargeError(msg)

    async def _diagram_scope(self, diagram_id: UUID) -> ReportScope:
        header = await self._diagrams.get(diagram_id)  # 404 before anything else is read
        if header.node_count > MAX_REPORT_ELEMENTS:
            msg = (
                f"a report covers at most {MAX_REPORT_ELEMENTS} elements and this diagram "
                f"has {header.node_count}; split the diagram or report on a smaller one"
            )
            raise ReportTooLargeError(msg)
        # One read of the diagram and its graph: the report cannot mix two states.
        detail = await self._diagrams.open(diagram_id, link_limit=self._max_relationships + 1)
        if len(detail.graph.relationships) > self._max_relationships:
            raise self._too_many_links()
        return ReportScope(
            kind="diagram",
            name=detail.diagram.name,
            description=detail.diagram.description,
            diagrams=(detail,),
            elements=detail.graph.elements,
            relationships=detail.graph.relationships,
        )

    async def _package_scope(self, package_id: UUID) -> ReportScope:
        if self._packages is None:
            msg = "reports on a package are not available on this deployment"
            raise UnsupportedReportSubjectError(msg)
        summaries = {s.package.id: s for s in await self._packages.list_packages()}
        if package_id not in summaries:
            msg = f"no package with id {package_id}"
            raise PackageNotFoundError(msg)
        tree = {pid: s.package for pid, s in summaries.items()}
        wanted = {package_id} | descendants_of(package_id, tree)
        element_count = sum(summaries[pid].counts.elements for pid in wanted)
        diagram_count = sum(summaries[pid].counts.diagrams for pid in wanted)
        if element_count > MAX_REPORT_ELEMENTS:
            msg = (
                f"a report covers at most {MAX_REPORT_ELEMENTS} elements and this package "
                f"has {element_count}; report on a sub-package"
            )
            raise ReportTooLargeError(msg)
        if diagram_count > MAX_REPORT_DIAGRAMS:
            msg = (
                f"a report draws at most {MAX_REPORT_DIAGRAMS} diagrams and this package "
                f"has {diagram_count}; report on a sub-package"
            )
            raise ReportTooLargeError(msg)
        headers = await self._diagrams.list_diagrams(package_id=package_id)
        if sum(h.node_count for h in headers) > MAX_REPORT_BOXES:
            msg = (
                f"the diagrams of this package hold more than {MAX_REPORT_BOXES} boxes in all; "
                "report on a sub-package"
            )
            raise ReportTooLargeError(msg)
        elements = await self._architecture.list_elements(
            ElementFilter(
                package_id=package_id,
                include_subpackages=True,
                limit=MAX_REPORT_ELEMENTS + 1,
            )
        )
        if len(elements) > MAX_REPORT_ELEMENTS:  # the package grew between the count and the read
            msg = f"a report covers at most {MAX_REPORT_ELEMENTS} elements; report on a sub-package"
            raise ReportTooLargeError(msg)
        graph = await self._architecture.view_of(
            [e.id for e in elements], link_limit=self._max_relationships + 1
        )
        if len(graph.relationships) > self._max_relationships:
            raise self._too_many_links()
        details: list[DiagramDetail] = []
        for header in headers:
            details.append(
                await self._diagrams.open(header.id, link_limit=self._max_relationships + 1)
            )
        return ReportScope(
            kind="package",
            name=summaries[package_id].package.name,
            description=summaries[package_id].package.description,
            diagrams=tuple(details),
            elements=graph.elements,
            relationships=graph.relationships,
        )

    async def _documents_of(self, scope: ReportScope) -> tuple[ScopedDocument, ...]:
        if self._documents is None:
            return ()
        # ponytail: one listing per element (at most MAX_REPORT_ELEMENTS); a
        # batched query is the upgrade if a measured report ever needs it.
        found: list[tuple[str, UUID]] = []
        total = 0
        for element in scope.elements:
            try:
                summaries = await self._documents.list_for_element(element.id)
            except ElementNotFoundError:
                continue  # deleted while the report was being built
            for summary in summaries:
                total += summary.byte_size
                if total > MAX_REPORT_DOCUMENT_BYTES:
                    msg = (
                        "the attached documents total more than 2 MiB, the most a report "
                        "carries; report on fewer elements"
                    )
                    raise ReportTooLargeError(msg)
                found.append((element.name, summary.id))
        documents = []
        for element_name, document_id in found:
            try:
                document = await self._documents.get(document_id)
            except DocumentNotFoundError:
                continue  # deleted while the report was being built
            documents.append(ScopedDocument(element_name, document.filename, document.content))
        return tuple(documents)

    def _within_time(self, started: float) -> None:
        if self._monotonic() - started > self._timeout:
            raise self._too_slow()

    def _too_slow(self) -> ReportTooLargeError:
        msg = (
            f"a report is built in at most {self._timeout:g} seconds; narrow the scope "
            "or ask for the html format"
        )
        return ReportTooLargeError(msg)

    async def _render(
        self,
        lease: Lease | None,
        started: float,
        renderer: Renderer,
        template: ReportTemplate,
        scope: ReportScope,
        documents: Sequence[ScopedDocument],
        now: datetime,
    ) -> bytes:
        remaining = self._timeout - (self._monotonic() - started)
        if remaining <= 0:
            raise self._too_slow()
        # `wait_for` stops *waiting*; the thread already inside the serialiser runs on until it
        # returns, and `off_the_loop` keeps this caller's place until then (Lease.hold), so a
        # client that retries cannot pile up threads past the ceilings.
        try:
            return await asyncio.wait_for(
                off_the_loop(lease, _assemble, renderer, template, scope, documents, now),
                remaining,
            )
        except TimeoutError:
            raise self._too_slow() from None

    @staticmethod
    def _log(request: ReportRequest, rendered: RenderedReport, *, deposited: bool) -> None:
        # Ids and counts only: the name of the subject is a person's text, not a log field.
        logger.info(
            "report generated",
            extra={
                "action": "report_generated",
                "template": request.template_id,
                "subject": f"{request.subject.kind}:{request.subject.id}",
                "report_format": request.format,
                "bytes": len(rendered.content),
                "duration_ms": rendered.duration_ms,
                "deposited": deposited,
            },
        )
