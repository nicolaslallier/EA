"""The requirements coverage, as a reading of the architecture graph.

This service adds no store and writes nothing. A requirement is an element, a
test or a design realizes it, and the coverage is computed here on demand from
one repository read - see `docs/adr/0064`. Writing a requirement, its status or
its test result goes through `ArchitectureService`, so the ArchiMate rules and
the properties convention are enforced there and nowhere else.

**The read is bounded before it is made**: the repository is asked for one past
each ceiling, and the question is refused (`ScopeTooLargeError`, a 422) rather
than cut, since a rate over a page of the requirements would be wrong, silently.
The computation is folded off the event loop, under a place of the same
admission as the analyses, held until the thread has really ended.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager, nullcontext
from typing import TYPE_CHECKING

from ea.domain.errors import ScopeTooLargeError
from ea.domain.requirements import (
    MAX_REQUIREMENTS,
    MAX_TRACE_LINKS,
    REQUIREMENT_TYPES,
    Coverage,
    check_filters,
    compute_coverage,
)
from ea.services.analysis import off_the_loop
from ea.services.caller import require_caller
from ea.services.exchange_admission import Admission, Lease

if TYPE_CHECKING:
    from ea.domain.ports import RequirementRepository


class RequirementService:
    """The requirements use cases."""

    def __init__(
        self,
        repository: RequirementRepository,
        *,
        admission: Admission | None = None,
        max_requirements: int = MAX_REQUIREMENTS,
        max_links: int = MAX_TRACE_LINKS,
    ) -> None:
        self._repository = repository
        self._admission = admission
        self._max_requirements = max_requirements
        self._max_links = max_links

    def _place(self, key: str) -> AbstractAsyncContextManager[Lease | None]:
        return self._admission.lease(key) if self._admission is not None else nullcontext(None)

    async def coverage(self, *, status: str | None = None, priority: str | None = None) -> Coverage:
        """Which requirements are realized by a design, and tested.

        `status` and `priority` narrow the requirements listed and counted; an
        unknown value is a `ValueError`, answered with a 422.
        """
        caller = require_caller()
        check_filters(status, priority)  # before the first read
        async with self._place(caller.subject) as lease:
            view = await self._repository.traceability(
                requirement_limit=self._max_requirements + 1, link_limit=self._max_links + 1
            )
            held = sum(1 for e in view.elements if e.element_type in REQUIREMENT_TYPES)
            if held > self._max_requirements:
                msg = f"the catalogue holds more than {self._max_requirements} requirements"
                raise ScopeTooLargeError(msg)
            if len(view.relationships) > self._max_links:
                msg = f"the requirements have more than {self._max_links} realizations"
                raise ScopeTooLargeError(msg)
            return await off_the_loop(
                lease,
                lambda: compute_coverage(
                    view.elements, view.relationships, status=status, priority=priority
                ),
            )
