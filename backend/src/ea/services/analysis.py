"""Analysing the model: the matrix, the quality checks, the dashboards.

Nothing here writes and nothing here is stored — an analysis is computed on
demand from `elements` and `relationships`, because a stored result would be a
second truth that every edit made stale (docs/adr/0056). Writing a link
from a matrix cell is `ArchitectureService.connect`, called by the caller.

Every public method asks who is calling first (`require_caller`): all of them
are reads.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from time import perf_counter
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.analytics import Aggregate, Dimension, Metric, summarise, validate_question
from ea.domain.errors import (
    AnalysisBusyError,
    ElementNotFoundError,
    InvalidAnalysisError,
    MatrixAxisTooLargeError,
    ScopeTooLargeError,
)
from ea.domain.matrix import (
    MAX_MATRIX_AXIS,
    MAX_MATRIX_LINKS,
    AxisSelection,
    Matrix,
    MatrixAxis,
    MatrixCell,
    allowed_between,
    build_matrix,
)
from ea.domain.ports import ElementFilter
from ea.domain.quality import (
    QualityPage,
    QualityParams,
    RuleCode,
    Severity,
    page_of,
    run_checks,
)
from ea.services.caller import require_caller
from ea.services.exchange_admission import Admission, Lease

if TYPE_CHECKING:
    from ea.domain.ports import AnalysisRepository
    from ea.services.architecture import ArchitectureService


logger = logging.getLogger(__name__)

#: The element ids of a package (sub-packages included) or of a saved query. It is the one
#: seam with the packages and the saved queries: the service knows neither (docs/adr/0056).
ScopeResolver = Callable[[UUID | None, UUID | None], Awaitable[tuple[UUID, ...]]]

#: How many analyses may read the whole model at once, and how many one caller may hold.
#: A run reads up to `MAX_QUALITY_ELEMENTS` rows and their links, so it is admitted, not queued.
MAX_ANALYSES_IN_FLIGHT = 4
MAX_ANALYSES_PER_CALLER = 2
ANALYSIS_LEASE_SECONDS = 120.0


def analysis_admission(
    *, limit: int = MAX_ANALYSES_IN_FLIGHT, clock: Callable[[], float] = time.monotonic
) -> Admission:
    """The places for the analyses that read the whole model (docs/adr/0057)."""
    return Admission(
        limit,
        per_caller=min(MAX_ANALYSES_PER_CALLER, limit),
        lease_seconds=ANALYSIS_LEASE_SECONDS,
        refusal=lambda: AnalysisBusyError("other analyses are running: try again in a moment"),
        kind="analysis",
        clock=clock,
    )


async def _off_the_loop[**P, R](
    lease: Lease | None, function: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> R:
    """Run a CPU-bound function in a thread; the lease is held until the thread ends."""
    work = asyncio.ensure_future(asyncio.to_thread(function, *args, **kwargs))
    if lease is not None:
        lease.hold(work)
    return await asyncio.shield(work)


class AnalysisService:
    """The analysis use cases, over the architecture service and three extra reads."""

    def __init__(
        self,
        architecture: ArchitectureService,
        repository: AnalysisRepository,
        *,
        params: QualityParams | None = None,
        admission: Admission | None = None,
        scope_ids: ScopeResolver | None = None,
    ) -> None:
        self._architecture = architecture
        self._repository = repository
        self._params = params or QualityParams()
        self._admission = admission
        self._scope_ids = scope_ids

    # --- The relationship matrix -------------------------------------------

    async def matrix(self, rows: AxisSelection, cols: AxisSelection) -> Matrix:
        """The grid of two axes with the links between them."""
        require_caller()
        row_axis = await self._axis(rows)
        col_axis = await self._axis(cols)
        links = await self._repository.links_between(
            [element.id for element in row_axis.elements],
            [element.id for element in col_axis.elements],
            limit=MAX_MATRIX_LINKS + 1,
        )
        if len(links) > MAX_MATRIX_LINKS:
            msg = f"the grid holds more than {MAX_MATRIX_LINKS} links: narrow the axes"
            raise ScopeTooLargeError(msg)
        return build_matrix(row_axis, col_axis, links)

    async def cell(self, source_id: UUID, target_id: UUID) -> MatrixCell:
        """One ordered pair: its links, and what ArchiMate would still let us add."""
        require_caller()
        source = await self._architecture.get_element(source_id)
        target = await self._architecture.get_element(target_id)
        links = await self._repository.links_between([source_id], [target_id])
        return MatrixCell(
            source_id=source_id,
            target_id=target_id,
            links=tuple(sorted(links, key=lambda link: (link.created_at, link.id))),
            allowed=allowed_between(source, target),
        )

    # --- Quality checks -----------------------------------------------------

    async def check(
        self,
        *,
        severity: Severity | None = None,
        code: RuleCode | None = None,
        limit: int = 100,
        cursor: int = 0,
        package_id: UUID | None = None,
        query_id: UUID | None = None,
    ) -> QualityPage:
        """Run every rule over the catalogue and return one page of findings.

        The counts describe the whole run; `severity` and `code` narrow only the
        findings listed. A catalogue over the ceiling is refused rather than
        silently checked in part: a partial report reads as a clean one.
        """
        caller = require_caller()
        async with self._place(caller.subject) as lease:
            scope = await self._scope(package_id, query_id)
            return await self._check(lease, scope, severity, code, limit, cursor)

    async def _check(
        self,
        lease: Lease | None,
        scope: tuple[UUID, ...] | None,
        severity: Severity | None,
        code: RuleCode | None,
        limit: int,
        cursor: int,
    ) -> QualityPage:
        started = perf_counter()
        ceiling = self._params.max_elements
        if scope is None:
            elements = await self._architecture.list_elements(ElementFilter(limit=ceiling + 1))
        else:
            elements = await self._repository.elements_by_ids(
                scope[: ceiling + 1], limit=ceiling + 1
            )
        if len(elements) > ceiling:
            msg = f"the catalogue holds more than {ceiling} elements; a run takes at most {ceiling}"
            raise ScopeTooLargeError(msg)
        # One past the ceiling is read, never the whole table: a refusal must not cost the read.
        links = await self._repository.links_touching(
            [element.id for element in elements], limit=self._params.max_links + 1
        )
        if len(links) > self._params.max_links:
            msg = f"the scope holds more than {self._params.max_links} links; a run takes fewer"
            raise ScopeTooLargeError(msg)
        # CPU-bound (pairwise name comparison): off the event loop, and the place is held until
        # the thread has really ended, even if the client hangs up or the lease expires.
        report = await _off_the_loop(lease, run_checks, elements, links, self._params)
        logger.info(
            "quality run: %d findings over %d elements",
            len(report.findings),
            len(elements),
            extra={
                "action": "quality_run",
                "duration_ms": round((perf_counter() - started) * 1000, 1),
                "findings": len(report.findings),
            },
        )
        return page_of(report, severity=severity, code=code, limit=limit, cursor=cursor)

    # --- Aggregates and heat maps -------------------------------------------

    async def aggregate(
        self,
        by: Dimension,
        metric: Metric,
        property_key: str | None = None,
        *,
        package_id: UUID | None = None,
        query_id: UUID | None = None,
    ) -> Aggregate:
        """Elements grouped by one dimension, with a metric and a colour level per bucket."""
        caller = require_caller()
        return await self._summary(
            caller.subject, (by,), metric, property_key, package_id, query_id
        )

    async def heatmap(
        self,
        rows: Dimension,
        cols: Dimension,
        metric: Metric,
        property_key: str | None = None,
        *,
        package_id: UUID | None = None,
        query_id: UUID | None = None,
    ) -> Aggregate:
        """The same, grouped by two dimensions — the cells of a heat map."""
        caller = require_caller()
        return await self._summary(
            caller.subject, (rows, cols), metric, property_key, package_id, query_id
        )

    async def _summary(
        self,
        subject: str,
        dimensions: Sequence[Dimension],
        metric: Metric,
        property_key: str | None,
        package_id: UUID | None,
        query_id: UUID | None,
    ) -> Aggregate:
        key = validate_question(metric, property_key)
        async with self._place(subject) as lease:
            scope = await self._scope(package_id, query_id)
            groups = await self._repository.grouped(dimensions, key, scope=scope)
            # Folding up to RAW_GROUP_CEILING groups is CPU: off the loop, place held to its end.
            return await _off_the_loop(lease, summarise, groups, dimensions, metric, key)

    async def _scope(
        self, package_id: UUID | None, query_id: UUID | None
    ) -> tuple[UUID, ...] | None:
        """The element ids of a package or a saved query; `None` means the whole catalogue."""
        if package_id is None and query_id is None:
            return None
        if package_id is not None and query_id is not None:
            msg = "a scope is a package or a saved query, not both"
            raise InvalidAnalysisError(msg)
        if self._scope_ids is None:
            msg = "this deployment has no packages or saved queries to scope by"
            raise InvalidAnalysisError(msg)
        ids = await self._scope_ids(package_id, query_id)
        # The one point of control: the resolver reads one past the ceiling, so a scope that
        # does not fit is refused here for every path, never silently cut to its first rows.
        if len(ids) > self._params.max_elements:
            msg = (
                f"the scope holds more than {self._params.max_elements} elements; "
                f"an analysis takes at most that"
            )
            raise ScopeTooLargeError(msg)
        return ids

    def _place(self, key: str) -> AbstractAsyncContextManager[Lease | None]:
        """A place among the analyses reading the whole model, or no limit where none is set."""
        return self._admission.lease(key) if self._admission is not None else nullcontext(None)

    async def _axis(self, selection: AxisSelection) -> MatrixAxis:
        scope = await self._scope(selection.package_id, selection.query_id)
        if scope is not None:
            # Cut at the first 50 by name, `total` says how many the scope held.
            found = await self._repository.elements_by_ids(scope, limit=MAX_MATRIX_AXIS)
            return MatrixAxis.of(found, total=len(scope))
        if selection.element_ids:
            ids = tuple(dict.fromkeys(selection.element_ids))
            if len(ids) > MAX_MATRIX_AXIS:
                msg = f"an axis names {len(ids)} elements; the grid shows at most {MAX_MATRIX_AXIS}"
                raise MatrixAxisTooLargeError(msg)
            found = await self._repository.elements_by_ids(ids, limit=MAX_MATRIX_AXIS)
            missing = sorted(str(i) for i in set(ids) - {element.id for element in found})
            if missing:
                msg = f"no element with id {missing[0]}"
                raise ElementNotFoundError(msg)
            return MatrixAxis.of(found)
        criteria = ElementFilter(
            element_types=selection.element_types,
            layers=selection.layers,
            search=selection.search,
            limit=MAX_MATRIX_AXIS + 1,
        )
        elements = await self._architecture.list_elements(criteria)
        total = (
            len(elements)
            if len(elements) <= MAX_MATRIX_AXIS
            else await self._architecture.count_elements(criteria)
        )
        return MatrixAxis.of(elements, total)
