"""Time in the model: the plateaus, the gap between two of them, the roadmap.

Reads compute on demand from `elements` and `relationships` and store nothing: a
plateau's members are what it composes or aggregates (computed, never stored), a gap
is an ordinary `gap` element, and the roadmap is a reading (docs/adr/0061).

**Every read is bounded before it is made.** A window wider than 20 years is refused
before the first query; every set is read one past its ceiling in SQL and refused
(`ScopeTooLargeError`, a 422) rather than cut, since a partial roadmap reads as a
complete one. The comparison and the roadmap are folded off the event loop, under a
place of the same admission as the analyses, held until the thread has really ended.

`create_gap` writes three things - the gap and two associations - in **one**
transaction, through `ArchitectureService`, so every ArchiMate, review, audit and
IPAM rule applies and a failure leaves nothing behind.
"""

from __future__ import annotations

import logging
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.errors import DuplicateGapError, InvalidTimelineError, ScopeTooLargeError
from ea.domain.timeline import (
    MAX_GAPS_LISTED,
    MAX_PLATEAU_MEMBERS,
    MAX_PLATEAUS,
    MAX_ROADMAP_ITEMS,
    MAX_ROADMAP_LINKS,
    ROADMAP_TYPES,
    Lifespan,
    PlateauComparison,
    PlateauSummary,
    Roadmap,
    build_roadmap,
    check_window,
    diff_plateaus,
    sort_plateaus,
)
from ea.services.analysis import off_the_loop
from ea.services.caller import require_caller, require_editor
from ea.services.exchange_admission import Admission, Lease

if TYPE_CHECKING:
    from ea.domain.model import Element
    from ea.domain.ports import TimelineRepository
    from ea.services.architecture import ArchitectureService

logger = logging.getLogger(__name__)

#: The longest name an element may have (`api/schemas.py: Name`); a default is cut to it.
_NAME_LIMIT = 200


class TimelineService:
    """The plateau, gap and roadmap use cases, over the architecture service and a few reads."""

    def __init__(
        self,
        architecture: ArchitectureService,
        repository: TimelineRepository,
        *,
        admission: Admission | None = None,
        max_plateaus: int = MAX_PLATEAUS,
        max_members: int = MAX_PLATEAU_MEMBERS,
        max_items: int = MAX_ROADMAP_ITEMS,
        max_links: int = MAX_ROADMAP_LINKS,
    ) -> None:
        self._architecture = architecture
        self._repository = repository
        self._admission = admission
        self._max_plateaus = max_plateaus
        self._max_members = max_members
        self._max_items = max_items
        self._max_links = max_links

    def _place(self, key: str) -> AbstractAsyncContextManager[Lease | None]:
        return self._admission.lease(key) if self._admission is not None else nullcontext(None)

    # --- Plateaus -----------------------------------------------------------

    async def list_plateaus(self) -> tuple[PlateauSummary, ...]:
        """Every plateau, earliest first, with what it contains."""
        require_caller()
        found = await self._repository.plateaus(limit=self._max_plateaus + 1)
        if len(found) > self._max_plateaus:
            msg = f"the catalogue holds more than {self._max_plateaus} plateaus"
            raise ScopeTooLargeError(msg)
        return sort_plateaus(
            PlateauSummary(element, Lifespan.read_tolerant(element.properties), count)
            for element, count in found
        )

    async def _two_plateaus(self, a_id: UUID, b_id: UUID) -> tuple[Element, Element]:
        if a_id == b_id:
            msg = "a plateau cannot be compared with itself"
            raise InvalidTimelineError(msg)
        a = await self._architecture.get_element(a_id)
        b = await self._architecture.get_element(b_id)
        if a.element_type is not ElementType.PLATEAU or b.element_type is not ElementType.PLATEAU:
            msg = "both ends of a comparison must be plateaus"
            raise InvalidTimelineError(msg)
        return a, b

    async def plateau_gap(self, a_id: UUID, b_id: UUID) -> PlateauComparison:
        """What leaves, arrives and stays between plateau `a` and plateau `b`."""
        caller = require_caller()
        a, b = await self._two_plateaus(a_id, b_id)
        async with self._place(caller.subject) as lease:
            members_a = await self._repository.members_of(a.id, limit=self._max_members + 1)
            members_b = await self._repository.members_of(b.id, limit=self._max_members + 1)
            if max(len(members_a), len(members_b)) > self._max_members:
                msg = f"a plateau holds more than {self._max_members} elements: too many to compare"
                raise ScopeTooLargeError(msg)
            gaps = await self._repository.gaps_between(a.id, b.id, limit=MAX_GAPS_LISTED)
            diff = await off_the_loop(lease, diff_plateaus, members_a, members_b)
        return PlateauComparison(a=a, b=b, diff=diff, gaps=gaps)

    async def create_gap(self, a_id: UUID, b_id: UUID, name: str | None = None) -> Element:
        """Draw the gap between two plateaus: a `gap` element associated to both.

        One transaction, serialised per pair: two editors drawing the same gap at the same
        instant get one gap and a `DuplicateGapError`, never two.
        """
        require_editor()
        a, b = await self._two_plateaus(a_id, b_id)
        title = name if name else f"Écart {a.name} → {b.name}"[:_NAME_LIMIT]
        async with self._repository.transaction(commit=True):
            await self._repository.lock_gap_pair(a.id, b.id)
            if await self._repository.gaps_between(a.id, b.id, limit=1):
                msg = "a gap is already drawn between these two plateaus"
                raise DuplicateGapError(msg)
            gap = await self._architecture.create_element(element_type=ElementType.GAP, name=title)
            for plateau in (a, b):
                await self._architecture.connect(
                    relationship_type=RelationshipType.ASSOCIATION,
                    source_id=gap.id,
                    target_id=plateau.id,
                )
        logger.info(
            "gap drawn between two plateaus",
            extra={"action": "gap_created", "element_id": str(gap.id)},
        )
        return gap

    # --- The roadmap --------------------------------------------------------

    async def roadmap(
        self, window_from: date | None = None, window_to: date | None = None
    ) -> Roadmap:
        """Plateaus, work packages and deliverables with their dates, and the links between."""
        caller = require_caller()
        check_window(window_from, window_to)  # before the first read
        async with self._place(caller.subject) as lease:
            items = await self._repository.roadmap_elements(
                ROADMAP_TYPES, window_from, window_to, limit=self._max_items + 1
            )
            if len(items) > self._max_items:
                msg = f"the roadmap holds more than {self._max_items} items: give it a window"
                raise ScopeTooLargeError(msg)
            links = await self._repository.roadmap_links(
                [item.id for item in items], limit=self._max_links + 1
            )
            if len(links) > self._max_links:
                msg = f"the roadmap holds more than {self._max_links} links: give it a window"
                raise ScopeTooLargeError(msg)
            return await off_the_loop(lease, build_roadmap, items, links, window_from, window_to)
