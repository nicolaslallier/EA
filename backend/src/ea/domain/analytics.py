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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from ea.domain.errors import InvalidAnalysisError
from ea.domain.model import PROPERTY_KEY

#: Declared here and imported by `api/schemas.py` (a service cannot import the API).
MAX_BUCKETS: Final = 200
#: A hard stop on the groups one SQL query may return before they are merged.
RAW_GROUP_CEILING: Final = 5000
#: The bucket of an element that has no value for a property dimension.
UNSET_KEY: Final = "__unset__"
#: The bucket of an element whose value is outside the property's enumeration.
INVALID_KEY: Final = "__invalid__"
LEVELS: Final = 5
DIMENSION_PATTERN: Final = r"^(element_type|layer|property:[A-Za-z][A-Za-z0-9_]{0,62})$"

_BLANKS: Final = " \u00a0\u202f"
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")


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
    PROPERTY = "property"


@dataclass(frozen=True, slots=True)
class Dimension:
    """What elements are grouped by."""

    kind: DimensionKind
    key: str | None = None

    @classmethod
    def parse(cls, text: str) -> Dimension:
        if not re.fullmatch(DIMENSION_PATTERN, text):
            msg = f"{text!r} is not a dimension: element_type, layer or property:<key>"
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
