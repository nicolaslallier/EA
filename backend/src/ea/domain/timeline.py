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

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Final

from ea.domain.errors import InvalidLifespanError

VALID_FROM: Final = "valid_from"
VALID_UNTIL: Final = "valid_until"

#: The longest spelling of a day we read: `YYYY-MM-DD` is 10, `YYYYMMDD` 8, `2027-W01-1` 10.
#: A longer value is refused before it is parsed, whatever it holds.
_MAX_DAY_LENGTH: Final = 16


def parse_day(value: str, *, key: str) -> date:
    """A day written `YYYY-MM-DD` — or refuse, naming the key and never echoing the value."""
    raw = value.strip()
    if len(raw) <= _MAX_DAY_LENGTH:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    msg = f"{key} must be a day written YYYY-MM-DD"
    raise InvalidLifespanError(msg)


def _read(properties: Mapping[str, str], key: str) -> date | None:
    raw = properties.get(key, "").strip()
    return parse_day(raw, key=key) if raw else None


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


def readable_lifespan_properties(properties: Mapping[str, str]) -> dict[str, str]:
    """Properties stored before the rule, made readable: never raises, never loses a value.

    For what is *read back* from a copy that predates revision 0014 (a baseline): a
    day is canonicalised, a blank removed, and whatever is not a day — or an end that
    precedes its start — is set aside under `legacy_<key>`, exactly as the migration does.
    """
    out = dict(properties)
    days: dict[str, date] = {}
    for key in (VALID_FROM, VALID_UNTIL):
        raw = properties.get(key)
        if raw is None:
            continue
        if not raw.strip():
            del out[key]
            continue
        try:
            days[key] = parse_day(raw, key=key)
        except InvalidLifespanError:
            out[f"legacy_{key}"] = out.pop(key)
        else:
            out[key] = days[key].isoformat()
    if VALID_FROM in days and VALID_UNTIL in days and days[VALID_UNTIL] < days[VALID_FROM]:
        out["legacy_" + VALID_UNTIL] = out.pop(VALID_UNTIL)
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
        raw = properties.get(key)
        if raw is None:
            continue
        if not raw.strip():
            del canonical[key]
            continue
        days[key] = parse_day(raw, key=key)
        canonical[key] = days[key].isoformat()
    if VALID_FROM in days and VALID_UNTIL in days and days[VALID_UNTIL] < days[VALID_FROM]:
        msg = f"{VALID_UNTIL} is before {VALID_FROM}"
        raise InvalidLifespanError(msg)
    return canonical
