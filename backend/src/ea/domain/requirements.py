"""Requirements, and the traceability read from the graph (docs/adr/0067).

There is no requirement table. A requirement is a `requirement` or a
`constraint` element; its status, priority, source and readable identifier are
four keys of its `properties`, and a test case is a `deliverable` or an
`artifact` that carries a `test_result` and realizes the requirement it
demonstrates. Everything below is a *reading* of that graph, computed on
demand and never stored: a stored coverage would be a second truth, stale at
the next edit.

Pure: nothing here imports a framework, and time and randomness are not used.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from ea.domain.archimate import Aspect, ElementType, RelationshipType
from ea.domain.errors import InvalidRequirementError
from ea.domain.model import Element, Relationship

IDENTIFIER_PROPERTY: Final = "req_id"
STATUS_PROPERTY: Final = "req_status"
PRIORITY_PROPERTY: Final = "req_priority"
SOURCE_PROPERTY: Final = "req_source"
RESULT_PROPERTY: Final = "test_result"
LAST_RUN_PROPERTY: Final = "test_last_run"

REQUIREMENT_TYPES: Final = (ElementType.REQUIREMENT, ElementType.CONSTRAINT)
TEST_TYPES: Final = (ElementType.DELIVERABLE, ElementType.ARTIFACT)

#: Requirements and realizations a coverage will hold. Past either, the question is refused
#: (a 422), never cut: a rate over a page of the requirements would be wrong, silently.
MAX_REQUIREMENTS: Final = 5000
MAX_TRACE_LINKS: Final = 20000
#: How many junctions deep a realization is followed from a requirement.
MAX_JUNCTION_HOPS: Final = 10

#: What a missing or unreadable value is counted as.
UNSET: Final = "unset"


class Status(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    IMPLEMENTED = "implemented"
    VERIFIED = "verified"
    REJECTED = "rejected"


class Priority(StrEnum):
    MUST = "must"
    SHOULD = "should"
    COULD = "could"


class VerificationResult(StrEnum):
    NOT_RUN = "not_run"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class Verification(StrEnum):
    """What the tests of a requirement say, as a whole."""

    VERIFIED = "verified"  # at least one `passed`, and none `failed`
    FAILING = "failing"  # at least one `failed`
    UNVERIFIED = "unverified"  # no test, or only `not_run`, `blocked` or a value we do not know


class Gap(StrEnum):
    """What is missing around a requirement, if anything."""

    DESIGN = "design"
    VERIFICATION = "verification"
    NONE = "none"


_PRIORITY_RANK: Final = {
    Priority.MUST.value: 0,
    Priority.SHOULD.value: 1,
    Priority.COULD.value: 2,
    UNSET: 3,
}


def is_requirement(element: Element) -> bool:
    return element.element_type in REQUIREMENT_TYPES


def is_test(element: Element) -> bool:
    """A test case is a deliverable or an artifact that records a result.

    The key's *presence* is the marker, as `ip_address` is for IPAM: it needs
    no table of the profile mechanism to be read.
    """
    return element.element_type in TEST_TYPES and RESULT_PROPERTY in element.properties


def canonical(raw: str, vocabulary: type[StrEnum]) -> str | None:
    """THE reading of a vocabulary value, shared by the write and the coverage.

    Case and surrounding blanks are not a difference; anything else outside the vocabulary
    (`ok`, `success`, `PASS`) is `None` - for a result that means *not verified*, never verified.
    """
    value = raw.strip().casefold()
    return value if value in {member.value for member in vocabulary} else None


def _read(properties: Mapping[str, str], key: str, vocabulary: type[StrEnum]) -> str:
    return canonical(properties.get(key, ""), vocabulary) or UNSET


def validate_requirement_properties(
    element_type: ElementType, properties: Mapping[str, str] | None
) -> Mapping[str, str] | None:
    """Refuse a status, priority or test result outside its vocabulary; store it canonical.

    The same convention check as `validate_ipam_properties`, called wherever properties are
    written, so the write and the coverage cannot disagree about what a value means. A blank
    value is kept (it reads as unset / unverified). The message names the key, never the value.
    """
    if not properties:
        return properties
    checked: dict[str, tuple[type[StrEnum], tuple[ElementType, ...]]] = {
        STATUS_PROPERTY: (Status, REQUIREMENT_TYPES),
        PRIORITY_PROPERTY: (Priority, REQUIREMENT_TYPES),
        RESULT_PROPERTY: (VerificationResult, TEST_TYPES),
    }
    out = dict(properties)
    for key, (vocabulary, types) in checked.items():
        raw = properties.get(key)
        if raw is None or element_type not in types or not raw.strip():
            continue
        value = canonical(raw, vocabulary)
        if value is None:
            allowed = ", ".join(member.value for member in vocabulary)
            msg = f"{key!r} is one of: {allowed}"
            raise InvalidRequirementError(msg)
        out[key] = value
    return out if out != dict(properties) else properties


def _by_name(element: Element) -> tuple[str, str]:
    return (element.name.casefold(), str(element.id))


def _rate(part: int, whole: int) -> float | None:
    return None if whole == 0 else part / whole


@dataclass(frozen=True, slots=True)
class RequirementTrace:
    """One requirement, what realizes it, and what tests it."""

    requirement: Element
    status: str
    priority: str
    designed_by: tuple[Element, ...]
    tested_by: tuple[Element, ...]

    @property
    def identifier(self) -> str:
        return self.requirement.properties.get(IDENTIFIER_PROPERTY, "").strip()

    @property
    def verification(self) -> Verification:
        """Only a test that *passed* verifies: a red, unrun or blocked one does not."""
        results = [
            canonical(t.properties.get(RESULT_PROPERTY, ""), VerificationResult)
            for t in self.tested_by
        ]
        if VerificationResult.FAILED.value in results:
            return Verification.FAILING
        if VerificationResult.PASSED.value in results:
            return Verification.VERIFIED
        return Verification.UNVERIFIED

    @property
    def gap(self) -> Gap:
        if self.status == Status.REJECTED.value:
            return Gap.NONE
        if not self.designed_by:
            return Gap.DESIGN
        if self.verification is not Verification.VERIFIED:
            return Gap.VERIFICATION
        return Gap.NONE


@dataclass(frozen=True, slots=True)
class Coverage:
    """The traces of the requirements asked about, and the rates over them."""

    traces: tuple[RequirementTrace, ...]

    @property
    def total(self) -> int:
        return len(self.traces)

    @property
    def active_traces(self) -> tuple[RequirementTrace, ...]:
        """Rejected requirements need neither a design nor a test."""
        return tuple(t for t in self.traces if t.status != Status.REJECTED.value)

    @property
    def active(self) -> int:
        return len(self.active_traces)

    @property
    def designed(self) -> int:
        return sum(1 for t in self.active_traces if t.designed_by)

    def _count(self, state: Verification) -> int:
        return sum(1 for t in self.active_traces if t.verification is state)

    @property
    def verified(self) -> int:
        """Active requirements with a passing test and no failing one."""
        return self._count(Verification.VERIFIED)

    @property
    def failing(self) -> int:
        return self._count(Verification.FAILING)

    @property
    def unverified(self) -> int:
        return self._count(Verification.UNVERIFIED)

    @property
    def design_rate(self) -> float | None:
        return _rate(self.designed, self.active)

    @property
    def verification_rate(self) -> float | None:
        return _rate(self.verified, self.active)

    @property
    def by_status(self) -> dict[str, int]:
        counts = {status.value: 0 for status in Status} | {UNSET: 0}
        for trace in self.traces:
            counts[trace.status] += 1
        return counts

    @property
    def by_priority(self) -> dict[str, int]:
        counts = {priority.value: 0 for priority in Priority} | {UNSET: 0}
        for trace in self.traces:
            counts[trace.priority] += 1
        return counts


def _check_filter(value: str | None, vocabulary: type[StrEnum], name: str) -> None:
    allowed = [member.value for member in vocabulary] + [UNSET]
    if value is not None and value not in allowed:
        msg = f"unknown {name}: expected one of {', '.join(allowed)}"
        raise ValueError(msg)


def check_filters(status: str | None, priority: str | None) -> None:
    """Refuse a filter outside the vocabularies - before anything is read."""
    _check_filter(status, Status, "status")
    _check_filter(priority, Priority, "priority")


def _reach(
    start: UUID, incoming: Mapping[UUID, list[Element]]
) -> tuple[tuple[Element, ...], tuple[Element, ...]]:
    """The design and the tests behind a requirement, through chains of requirements.

    Breadth-first over incoming realizations with a set of what was already
    seen, so a loop (A realized by B realized by A) ends instead of spinning.
    A source that is a requirement is followed; a test is collected as a test;
    any other non-motivation element is design; the remaining motivation
    elements (a stakeholder, a driver) are neither.
    """
    designed: dict[UUID, Element] = {}
    tested: dict[UUID, Element] = {}
    seen = {start}
    queue = deque([start])
    while queue:
        for source in incoming.get(queue.popleft(), ()):
            if source.id in seen:
                continue
            seen.add(source.id)
            if is_requirement(source) or source.element_type is ElementType.JUNCTION:
                # A junction only wires links together (docs/adr/0065): cross it, never list it.
                queue.append(source.id)
            elif is_test(source):
                tested[source.id] = source
            elif source.element_type.aspect is not Aspect.MOTIVATION:
                designed[source.id] = source
    return (
        tuple(sorted(designed.values(), key=_by_name)),
        tuple(sorted(tested.values(), key=_by_name)),
    )


def compute_coverage(
    elements: Iterable[Element],
    relationships: Iterable[Relationship],
    *,
    status: str | None = None,
    priority: str | None = None,
) -> Coverage:
    """Who realizes and who tests each requirement, optionally narrowed.

    `status` and `priority` narrow which requirements are listed and counted;
    `UNSET` selects those with no usable value. Anything else outside the
    vocabularies is refused (a `ValueError`, a 422 at the door).
    """
    check_filters(status, priority)
    elements = tuple(elements)
    by_id = {element.id: element for element in elements}
    incoming: dict[UUID, list[Element]] = {}
    for link in relationships:
        if link.relationship_type is not RelationshipType.REALIZATION:
            continue
        source = by_id.get(link.source_id)
        if source is not None:
            incoming.setdefault(link.target_id, []).append(source)

    traces: list[RequirementTrace] = []
    for element in elements:
        if not is_requirement(element):
            continue
        trace_status = _read(element.properties, STATUS_PROPERTY, Status)
        trace_priority = _read(element.properties, PRIORITY_PROPERTY, Priority)
        if status is not None and trace_status != status:
            continue
        if priority is not None and trace_priority != priority:
            continue
        designed, tested = _reach(element.id, incoming)
        traces.append(RequirementTrace(element, trace_status, trace_priority, designed, tested))
    traces.sort(key=lambda t: (_PRIORITY_RANK[t.priority], *_by_name(t.requirement)))
    return Coverage(tuple(traces))
