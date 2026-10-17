"""Time in the model: when a thing is true in the world.

An element or a relationship may carry `valid_from` and `valid_until` in its
`properties`, as `YYYY-MM-DD`. `valid_until` is inclusive, and a missing bound is
an open one ("since forever", "no end planned"). That is the *validity* axis —
when the thing holds in the world; the moment the model learnt it (the
*transaction* axis, the audit trail and the baselines) is another subject. See
docs/adr/0059.

Pure: no framework, no clock. The convention is checked wherever properties are
written, exactly like `validate_ipam_properties`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import TYPE_CHECKING, Final

from ea.domain.archimate import ElementType, RelationshipType
from ea.domain.errors import InvalidLifespanError, InvalidTimelineError

if TYPE_CHECKING:
    from uuid import UUID

    from ea.domain.model import Element, Relationship

VALID_FROM: Final = "valid_from"
VALID_UNTIL: Final = "valid_until"

#: The longest spelling of a day we read: `YYYY-MM-DD` is 10, `YYYYMMDD` 8, `2027-W01-1` 10.
#: A longer value is refused before it is parsed, whatever it holds.
_MAX_DAY_LENGTH: Final = 16


def parse_day(value: object, *, key: str) -> date:
    """A day written `YYYY-MM-DD` — or refuse, naming the key and never echoing the value.

    `properties` is a jsonb map: whatever it holds, a value that is not a string is not a day.
    """
    raw = value.strip() if isinstance(value, str) else None
    if raw is not None and len(raw) <= _MAX_DAY_LENGTH:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    msg = f"{key} must be a day written YYYY-MM-DD"
    raise InvalidLifespanError(msg)


def _read(properties: Mapping[str, str], key: str) -> date | None:
    raw = properties.get(key, "")
    if isinstance(raw, str) and not raw.strip():
        return None
    return parse_day(raw, key=key)


@dataclass(frozen=True, slots=True)
class Lifespan:
    """The days a thing holds: both ends inclusive, `None` meaning open."""

    start: date | None = None
    end: date | None = None

    @classmethod
    def read(cls, properties: Mapping[str, str]) -> Lifespan:
        return cls(_read(properties, VALID_FROM), _read(properties, VALID_UNTIL))

    @classmethod
    def read_tolerant(cls, properties: Mapping[str, str]) -> Lifespan:
        """As `read`, for a read that must not fail: what is not a day counts as open."""
        return cls.read(readable_lifespan_properties(properties))

    @property
    def is_dated(self) -> bool:
        return self.start is not None or self.end is not None

    def covers(self, day: date) -> bool:
        return (self.start is None or self.start <= day) and (self.end is None or day <= self.end)

    def overlaps(self, other: Lifespan) -> bool:
        return (self.start is None or other.end is None or self.start <= other.end) and (
            other.start is None or self.end is None or other.start <= self.end
        )


#: How many `legacy_<key>` names a set-aside value may take before it is appended to the last.
LEGACY_SLOTS: Final = 9


def _set_aside(out: dict[str, str], key: str, value: str) -> None:
    """Move `value` under the first free `legacy_<key>`, `_2` … `_9` — never over one.

    All nine taken, it is appended (after a newline) to the last: nothing is lost. The same
    rule as revision 0014, which a baseline captured before it is read back through.
    """
    names = [f"legacy_{key}"] + [f"legacy_{key}_{n}" for n in range(2, LEGACY_SLOTS + 1)]
    free = next((name for name in names if name not in out), None)
    if free is None:
        out[names[-1]] = f"{out[names[-1]]}\n{value}"
    else:
        out[free] = value


def readable_lifespan_properties(properties: Mapping[str, str]) -> dict[str, str]:
    """Properties stored before the rule, made readable: never raises, never loses a value.

    For what is *read back* from a copy that predates revision 0014 (a baseline): a
    day is canonicalised, a blank removed, and whatever is not a day — or an end that
    precedes its start — is set aside under `legacy_<key>`, exactly as the migration does.
    """
    out = dict(properties)
    days: dict[str, date] = {}
    for key in (VALID_FROM, VALID_UNTIL):
        if key not in properties:
            continue
        raw: object = properties[key]
        if isinstance(raw, str) and not raw.strip():
            del out[key]
            continue
        try:
            days[key] = parse_day(raw, key=key)
        except InvalidLifespanError:
            out.pop(key, None)
            _set_aside(out, key, raw if isinstance(raw, str) else str(raw))
        else:
            out[key] = days[key].isoformat()
    if VALID_FROM in days and VALID_UNTIL in days and days[VALID_UNTIL] < days[VALID_FROM]:
        _set_aside(out, VALID_UNTIL, out.pop(VALID_UNTIL))
    return out


def validate_lifespan_properties(
    properties: Mapping[str, str] | None,
) -> Mapping[str, str] | None:
    """Refuse a validity date that is malformed or out of order; return what to store.

    Same contract as `validate_ipam_properties`: the properties come back with
    both dates in their one canonical spelling, because the database compares
    stored strings. A blank date is *removed*: `"" >= '2027-01-01'` is false, so
    a stored blank end would silently hide the element from every dated read.
    """
    if not properties:
        return properties
    canonical = dict(properties)
    days: dict[str, date] = {}
    for key in (VALID_FROM, VALID_UNTIL):
        if key not in properties:
            continue
        raw: object = properties[key]
        if isinstance(raw, str) and not raw.strip():
            del canonical[key]
            continue
        days[key] = parse_day(raw, key=key)
        canonical[key] = days[key].isoformat()
    if VALID_FROM in days and VALID_UNTIL in days and days[VALID_UNTIL] < days[VALID_FROM]:
        msg = f"{VALID_UNTIL} is before {VALID_FROM}"
        raise InvalidLifespanError(msg)
    return canonical


# --- Plateaus and the roadmap (docs/adr/0061) ---------------------------------------------

#: A roadmap window wider than this is a dump wearing a filter: 20 years, in days.
MAX_ROADMAP_DAYS: Final = 20 * 366
#: Plateaus are a handful; this bounds a runaway catalogue. Past it the question is refused.
MAX_PLATEAUS: Final = 500
#: Plateaus, work packages and deliverables a roadmap will draw - beyond it, give a window.
MAX_ROADMAP_ITEMS: Final = 1000
#: Links a roadmap reads among its items.
MAX_ROADMAP_LINKS: Final = 5000
#: Members of one plateau that a comparison will hold.
MAX_PLATEAU_MEMBERS: Final = 5000
#: Gaps already drawn between two plateaus that a comparison lists.
MAX_GAPS_LISTED: Final = 100

#: What a roadmap draws.
ROADMAP_TYPES: Final = (ElementType.PLATEAU, ElementType.WORK_PACKAGE, ElementType.DELIVERABLE)
#: A plateau contains what it composes or aggregates; computed, never stored.
MEMBERSHIP: Final = (RelationshipType.COMPOSITION, RelationshipType.AGGREGATION)
#: The links a roadmap draws between its items.
ROADMAP_EDGES: Final = (RelationshipType.TRIGGERING, RelationshipType.REALIZATION)


def _by_name(elements: Iterable[Element]) -> tuple[Element, ...]:
    """One of each (by id), in a stable order: name, then id."""
    unique = {element.id: element for element in elements}
    return tuple(sorted(unique.values(), key=lambda e: (e.name.lower(), str(e.id))))


@dataclass(frozen=True, slots=True)
class PlateauGap:
    """What changes between two plateaus: departures, arrivals, and what stays."""

    only_in_a: tuple[Element, ...]
    only_in_b: tuple[Element, ...]
    common: tuple[Element, ...]


def diff_plateaus(a: Iterable[Element], b: Iterable[Element]) -> PlateauGap:
    """Compare the members of two plateaus. Computed, never stored (docs/adr/0061)."""
    in_a = {element.id: element for element in a}
    in_b = {element.id: element for element in b}
    return PlateauGap(
        only_in_a=_by_name(e for i, e in in_a.items() if i not in in_b),
        only_in_b=_by_name(e for i, e in in_b.items() if i not in in_a),
        common=_by_name(e for i, e in in_a.items() if i in in_b),
    )


@dataclass(frozen=True, slots=True)
class PlateauSummary:
    element: Element
    lifespan: Lifespan
    member_count: int


def _chronological(lifespan: Lifespan, name: str, element_id: UUID) -> tuple[bool, date, str, str]:
    return (lifespan.start is None, lifespan.start or date.min, name.lower(), str(element_id))


def sort_plateaus(summaries: Iterable[PlateauSummary]) -> tuple[PlateauSummary, ...]:
    """Earliest first; a plateau with no start last; then by name."""
    return tuple(
        sorted(summaries, key=lambda s: _chronological(s.lifespan, s.element.name, s.element.id))
    )


@dataclass(frozen=True, slots=True)
class PlateauComparison:
    """Two plateaus, how they differ, and the gaps already drawn between them."""

    a: Element
    b: Element
    diff: PlateauGap
    gaps: tuple[Element, ...]


@dataclass(frozen=True, slots=True)
class RoadmapItem:
    element: Element
    lifespan: Lifespan
    #: The plateaus (among the roadmap's items) that contain it.
    plateau_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class RoadmapEdge:
    source_id: UUID
    target_id: UUID
    relationship_type: RelationshipType


@dataclass(frozen=True, slots=True)
class Roadmap:
    items: tuple[RoadmapItem, ...]
    edges: tuple[RoadmapEdge, ...]
    window_from: date | None
    window_to: date | None
    #: The bounds of the dated data, whatever window was drawn.
    data_from: date | None = None
    data_to: date | None = None
    #: The default window was cut to 20 years and left dated items out: say so, never silently.
    window_truncated: bool = False


def check_window(start: date | None, end: date | None) -> None:
    """Refuse a window before anything is read: two bounds, in order, at most 20 years apart."""
    if start is None or end is None:
        return
    if end < start:
        msg = "the roadmap window ends before it starts"
        raise InvalidTimelineError(msg)
    if (end - start).days > MAX_ROADMAP_DAYS:
        msg = "the roadmap window is wider than 20 years"
        raise InvalidTimelineError(msg)


def resolve_window(
    lifespans: Iterable[Lifespan], start: date | None, end: date | None
) -> tuple[date | None, date | None]:
    """The days a roadmap spans: the bounds asked for, else those of the data.

    Two explicit bounds are checked by `check_window`; a bound taken from the data is
    cut to `MAX_ROADMAP_DAYS` instead of refused, since the caller did not choose it.
    """
    check_window(start, end)
    days = [d for span in lifespans for d in (span.start, span.end) if d is not None]
    low = start or (min(days) if days else None)
    high = end or (max(days) if days else None)
    if low is None or high is None:
        return low, high
    if high < low:  # one explicit bound beyond the other end of the data
        low, high = (low, low) if end is None else (high, high)
    if (high - low).days > MAX_ROADMAP_DAYS:
        if end is None:
            high = low + timedelta(days=MAX_ROADMAP_DAYS)
        else:
            low = high - timedelta(days=MAX_ROADMAP_DAYS)
    return low, high


def in_window(lifespan: Lifespan, start: date | None, end: date | None) -> bool:
    """Undated things are always in (they are listed apart, never hidden)."""
    return not lifespan.is_dated or lifespan.overlaps(Lifespan(start, end))


def build_roadmap(
    elements: Sequence[Element],
    links: Sequence[Relationship],
    start: date | None,
    end: date | None,
) -> Roadmap:
    """The items of a roadmap with their dates, plateaus and edges - sorted, so deterministic.

    The window defaults to the bounds of the data given. Containment is computed from the
    links (a composition or aggregation from a plateau); a link to a thing the window left
    out is dropped.
    """
    spans = {element.id: Lifespan.read_tolerant(element.properties) for element in elements}
    low, high = resolve_window(spans.values(), start, end)
    kept = {e.id: e for e in elements if in_window(spans[e.id], low, high)}
    contained: dict[UUID, set[UUID]] = {}
    for link in links:
        if (
            link.relationship_type in MEMBERSHIP
            and link.source_type is ElementType.PLATEAU
            and link.source_id in kept
            and link.target_id in kept
        ):
            contained.setdefault(link.target_id, set()).add(link.source_id)
    items = tuple(
        RoadmapItem(e, spans[e.id], tuple(sorted(contained.get(e.id, ()), key=str)))
        for e in sorted(kept.values(), key=lambda e: _chronological(spans[e.id], e.name, e.id))
    )
    edges = tuple(
        sorted(
            (
                RoadmapEdge(link.source_id, link.target_id, link.relationship_type)
                for link in links
                if link.relationship_type in ROADMAP_EDGES
                and link.source_id in kept
                and link.target_id in kept
            ),
            key=lambda e: (str(e.source_id), str(e.target_id), e.relationship_type.value),
        )
    )
    days = [d for span in spans.values() for d in (span.start, span.end) if d is not None]
    data_from, data_to = (min(days), max(days)) if days else (None, None)
    truncated = (
        low is not None
        and high is not None
        and data_from is not None
        and data_to is not None
        and ((start is None and data_from < low) or (end is None and data_to > high))
    )
    return Roadmap(items, edges, low, high, data_from, data_to, truncated)
