"""Use cases of the import and export of exchange files (docs/adr/0048).

Export reads one consistent snapshot, page after page, and streams. It writes
nothing, so it has neither an audit event nor a review guard: a reader may run it.
Import (a later lot) goes through `ArchitectureService`, so every ArchiMate and
IPAM rule is enforced for a file exactly as for a person.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.errors import UnknownScopeError
from ea.domain.exchange import (
    EXPORT_PAGE_SIZE,
    EXPORT_TIMEOUT_SECONDS,
    MAX_EXCHANGE_BYTES,
    ExchangeElement,
    ExchangeRelationship,
    ExportRequest,
    IdentifierRegistry,
    exchange_element_of,
    exchange_relationship_of,
)
from ea.formats.archimate_xml import buffered, render_model
from ea.services.caller import require_caller

if TYPE_CHECKING:
    from ea.domain.ports import ExchangeRepository, ExportSnapshot
    from ea.services.architecture import ArchitectureService

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]
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
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._max_bytes = max_bytes
        self._now = clock
        self._monotonic = monotonic

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
