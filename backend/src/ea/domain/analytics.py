"""Reading the model as numbers: aggregates, scales, heat.

`properties` stays a map of strings (docs/adr/0058): no schema, no
migration. What is added is a *catalogue of readings* — how the few keys the
dashboards care about are to be read, as a decimal number or as a position in
an ordered list — and a value that does not read is counted and reported,
never silently dropped. Any other key is a nominal label: it can be counted by,
not summed.

The colour of a heat-map cell is decided here, as a `level` from 0 to 4. The
SPA only maps a level to a swatch, so no threshold lives in TypeScript.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from ea.domain.errors import InvalidAnalysisError
from ea.domain.model import PROPERTY_KEY, Element

#: Declared here and imported by `api/schemas.py` (a service cannot import the API).
MAX_BUCKETS: Final = 200
#: A hard stop on the groups one SQL query may return before they are merged.
RAW_GROUP_CEILING: Final = 5000
#: The bucket of an element that has no value for a property dimension.
UNSET_KEY: Final = "__unset__"
#: The bucket of an element whose value is outside the property's enumeration.
INVALID_KEY: Final = "__invalid__"
LEVELS: Final = 5
DIMENSION_PATTERN: Final = r"^(element_type|layer|package|property:[A-Za-z][A-Za-z0-9_]{0,62})$"

_BLANKS: Final = " \u00a0\u202f"
#: A figure of up to 30 digits either side of the point: finite as a float, exact as a PostgreSQL
#: numeric. The same pattern is bound in SQL (`architecture_store`), so the two cannot disagree.
NUMBER_PATTERN: Final = r"^-?[0-9]{1,30}(\.[0-9]{1,30})?$"
#: Characters read of a raw value before it is judged: a longer one is unreadable, unread.
MAX_NUMBER_INPUT: Final = 64
_NUMBER = re.compile(NUMBER_PATTERN)


class ReadingKind(StrEnum):
    NUMBER = "number"
    ORDERED = "ordered"


@dataclass(frozen=True, slots=True)
class Reading:
    kind: ReadingKind
    #: For `ORDERED`, the values from least to most.
    order: tuple[str, ...] = ()


#: How the keys the dashboards know are read. The values are stored as typed;
#: comparing them ignores case and surrounding blanks.
ANALYTIC_PROPERTIES: Final[Mapping[str, Reading]] = MappingProxyType(
    {
        "lifecycle": Reading(
            ReadingKind.ORDERED,
            ("planifié", "en construction", "en production", "en fin de vie", "retiré"),
        ),
        "risk": Reading(ReadingKind.ORDERED, ("faible", "moyen", "élevé", "critique")),
        "cost": Reading(ReadingKind.NUMBER),
    }
)


def read_number(raw: str) -> float | None:
    """A decimal number, or `None`.

    Tolerated: a `.` or `,` decimal, and spaces, no-break spaces (U+00A0) and
    narrow no-break spaces (U+202F) as thousands separators. Everything else —
    a currency sign, `1.200,50`, an exponent — is unreadable: guessing a
    spelling would turn a typo into a wrong total.
    """
    if len(raw) > MAX_NUMBER_INPUT:
        return None
    text = raw.translate(str.maketrans("", "", _BLANKS)).replace(",", ".")
    return float(text) if _NUMBER.fullmatch(text) else None


def _fold(text: str) -> str:
    return text.strip().casefold()


def read_rank(key: str, raw: str) -> int | None:
    """Where `raw` sits in the ordered reading of `key`, or `None`."""
    reading = ANALYTIC_PROPERTIES.get(key)
    if reading is None or reading.kind is not ReadingKind.ORDERED:
        return None
    folded = _fold(raw)
    for rank, value in enumerate(reading.order):
        if _fold(value) == folded:
            return rank
    return None


class Metric(StrEnum):
    COUNT = "count"
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class DimensionKind(StrEnum):
    ELEMENT_TYPE = "element_type"
    LAYER = "layer"
    PACKAGE = "package"
    PROPERTY = "property"


@dataclass(frozen=True, slots=True)
class Dimension:
    """What elements are grouped by."""

    kind: DimensionKind
    key: str | None = None

    @classmethod
    def parse(cls, text: str) -> Dimension:
        if not re.fullmatch(DIMENSION_PATTERN, text):
            msg = f"{text!r} is not a dimension: element_type, layer, package or property:<key>"
            raise InvalidAnalysisError(msg)
        if text.startswith("property:"):
            return cls(DimensionKind.PROPERTY, text.split(":", 1)[1])
        return cls(DimensionKind(text))

    def __str__(self) -> str:
        return f"property:{self.key}" if self.kind is DimensionKind.PROPERTY else self.kind.value


def validate_question(metric: Metric, property_key: str | None) -> str | None:
    """The key numbers are read from — `None` for a count, which reads nothing."""
    if metric is Metric.COUNT:
        return None
    if not property_key:
        msg = f"the metric {metric.value} needs a property to read"
        raise InvalidAnalysisError(msg)
    if not PROPERTY_KEY.match(property_key):
        msg = f"{property_key!r} is not a plain property name"
        raise InvalidAnalysisError(msg)
    return property_key


def scale_for(values: Sequence[float | None]) -> tuple[int | None, ...]:
    """A level 0..4 per value, by quantile; equal values share a level.

    The thresholds are the values at 20, 40, 60 and 80 % of the sorted present
    values; a value's level is how many thresholds it is strictly above. A
    missing value has no level and does not move the others.
    """
    present = sorted(v for v in values if v is not None)
    if not present:
        return tuple(None for _ in values)
    thresholds = [
        present[max(0, math.ceil(step * len(present) / LEVELS) - 1)] for step in range(1, LEVELS)
    ]
    return tuple(None if v is None else sum(1 for t in thresholds if v > t) for v in values)


def rank_level(rank: int, size: int) -> int:
    """Spread `size` ordered values over the five levels, ends included."""
    if size <= 1:
        return 0
    return int(rank * (LEVELS - 1) / (size - 1) + 0.5)


# --- Grouping ------------------------------------------------------------
#
# The database groups by the *raw* spelling of each dimension and returns
# partial sums per group (`RawGroup`); folding spellings together, ordering
# and colouring are done once, here, in `summarise` — so the SQL path and the
# in-memory path (`group_elements`) cannot disagree about what "Élevé" means.


@dataclass(frozen=True, slots=True)
class RawGroup:
    #: The raw value of each dimension, in order; `""` for a property that is absent.
    keys: tuple[str, ...]
    count: int
    #: How many elements of the group carry a readable number, and their sum / extremes.
    valid: int = 0
    total: float = 0.0
    low: float | None = None
    high: float | None = None
    #: Elements whose property is present but not a number.
    invalid: int = 0


def _raw_of(dimension: Dimension, element: Element) -> str:
    match dimension.kind:
        case DimensionKind.ELEMENT_TYPE:
            return element.element_type.value
        case DimensionKind.LAYER:
            return element.layer
        case DimensionKind.PACKAGE:
            return str(element.package_id or "")
        case DimensionKind.PROPERTY:
            return element.properties.get(dimension.key or "", "")


def group_elements(
    elements: Iterable[Element],
    dimensions: Sequence[Dimension],
    metric_key: str | None = None,
) -> tuple[RawGroup, ...]:
    """The reference grouping, in memory: what the SQL query must agree with."""
    counts: dict[tuple[str, ...], int] = {}
    numbers: dict[tuple[str, ...], list[float]] = {}
    invalid: dict[tuple[str, ...], int] = {}
    for element in elements:
        keys = tuple(_raw_of(dimension, element) for dimension in dimensions)
        counts[keys] = counts.get(keys, 0) + 1
        if metric_key is None:
            continue
        raw = element.properties.get(metric_key, "")
        if not raw.strip(" "):
            continue
        number = read_number(raw)
        if number is None:
            invalid[keys] = invalid.get(keys, 0) + 1
        else:
            numbers.setdefault(keys, []).append(number)
    return tuple(
        RawGroup(
            keys=keys,
            count=count,
            valid=len(numbers.get(keys, ())),
            total=sum(numbers.get(keys, ())),
            low=min(numbers.get(keys, ()), default=None),
            high=max(numbers.get(keys, ()), default=None),
            invalid=invalid.get(keys, 0),
        )
        for keys, count in counts.items()
    )


def _ordered(dimension: Dimension) -> Reading | None:
    if dimension.kind is not DimensionKind.PROPERTY:
        return None
    reading = ANALYTIC_PROPERTIES.get(dimension.key or "")
    return reading if reading and reading.kind is ReadingKind.ORDERED else None


def classify(dimension: Dimension, raw: str) -> str:
    """The bucket a raw value belongs to: itself, its declared spelling, or a sentinel."""
    if dimension.kind not in (DimensionKind.PROPERTY, DimensionKind.PACKAGE):
        return raw
    text = raw.strip()
    if not text:
        return UNSET_KEY
    reading = _ordered(dimension)
    if reading is None:
        return text
    rank = read_rank(dimension.key or "", text)
    return reading.order[rank] if rank is not None else INVALID_KEY


def _sort_key(dimension: Dimension, key: str) -> tuple[int, int, str]:
    if key == UNSET_KEY:
        return (2, 0, "")
    if key == INVALID_KEY:
        return (1, 0, "")
    reading = _ordered(dimension)
    if reading is not None and key in reading.order:
        return (0, reading.order.index(key), "")
    return (0, 0, key.casefold())


@dataclass(frozen=True, slots=True)
class Bucket:
    keys: tuple[str, ...]
    count: int
    #: The metric over the bucket: the count itself for `COUNT`, else over readable values.
    value: float | None
    #: 0..4, or `None` for a bucket with nothing to colour.
    level: int | None


@dataclass(frozen=True, slots=True)
class Aggregate:
    dimensions: tuple[Dimension, ...]
    metric: Metric
    property_key: str | None
    buckets: tuple[Bucket, ...]
    #: Elements counted, before any cut.
    total: int
    #: Elements whose metric property is present but not a number.
    unreadable: int
    truncated: bool

    def axis_keys(self, position: int) -> tuple[str, ...]:
        """The distinct keys of one dimension, in the order that dimension reads."""
        dimension = self.dimensions[position]
        distinct = {bucket.keys[position] for bucket in self.buckets}
        return tuple(sorted(distinct, key=lambda key: _sort_key(dimension, key)))


@dataclass(slots=True)
class _Merged:
    count: int = 0
    valid: int = 0
    total: float = 0.0
    low: float | None = None
    high: float | None = None


def _smaller(one: float | None, other: float | None) -> float | None:
    return other if one is None else one if other is None else min(one, other)


def _larger(one: float | None, other: float | None) -> float | None:
    return other if one is None else one if other is None else max(one, other)


def _metric_of(metric: Metric, merged: _Merged) -> float | None:
    match metric:
        case Metric.COUNT:
            return float(merged.count)
        case Metric.SUM:
            return merged.total if merged.valid else None
        case Metric.AVG:
            return merged.total / merged.valid if merged.valid else None
        case Metric.MIN:
            return merged.low
        case Metric.MAX:
            return merged.high


def summarise(
    groups: Iterable[RawGroup],
    dimensions: Sequence[Dimension],
    metric: Metric,
    property_key: str | None = None,
) -> Aggregate:
    """Fold raw groups into buckets: classify, merge, order, cut and colour."""
    merged: dict[tuple[str, ...], _Merged] = {}
    unreadable = 0
    for group in groups:
        keys = tuple(
            classify(dimension, raw) for dimension, raw in zip(dimensions, group.keys, strict=True)
        )
        into = merged.setdefault(keys, _Merged())
        into.count += group.count
        into.valid += group.valid
        into.total += group.total
        into.low = _smaller(into.low, group.low)
        into.high = _larger(into.high, group.high)
        unreadable += group.invalid

    ordered = sorted(
        merged,
        key=lambda keys: tuple(
            _sort_key(dimension, key) for dimension, key in zip(dimensions, keys, strict=True)
        ),
    )
    total = sum(entry.count for entry in merged.values())
    kept = ordered[:MAX_BUCKETS]
    values = [_metric_of(metric, merged[keys]) for keys in kept]

    reading = _ordered(dimensions[0]) if len(dimensions) == 1 else None
    if reading is not None and metric is Metric.COUNT:
        levels: Sequence[int | None] = [
            rank_level(reading.order.index(keys[0]), len(reading.order))
            if keys[0] in reading.order
            else None
            for keys in kept
        ]
    else:
        levels = scale_for(values)

    return Aggregate(
        dimensions=tuple(dimensions),
        metric=metric,
        property_key=property_key,
        buckets=tuple(
            Bucket(keys=keys, count=merged[keys].count, value=value, level=level)
            for keys, value, level in zip(kept, values, levels, strict=True)
        ),
        total=total,
        unreadable=unreadable,
        truncated=len(ordered) > MAX_BUCKETS,
    )
