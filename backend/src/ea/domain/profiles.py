"""Profiles and stereotypes: reference data that extends nothing in ArchiMate.

A stereotype is a **label laid on an element of one base type**, never a new
type: the relationship rules stay those of the base type (docs/adr/0005) and
the metamodel does not move. What a stereotype adds is the properties people
expect on such an element, declared here and stored where every other
user-defined attribute lives — in `elements.properties`, as strings, without a
prefix, like the IP addressing (docs/adr/0020).

`required` never blocks a write (an element is created, then completed);
`missing_required` is what a quality rule reads. A value that is present *is*
checked, as `validate_ipam_properties` checks an address.

Nothing here imports a framework or a driver, per `CLAUDE.md`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Final
from uuid import UUID, uuid4

from ea.domain.archimate import ElementType
from ea.domain.ipam import IPAM_PROPERTIES
from ea.domain.model import PROPERTY_KEY, StereotypeRef

PROFILE_FORMAT: Final = 1
MAX_NAME_LENGTH: Final = 100
MAX_LABEL_LENGTH: Final = 100
MAX_HELP_LENGTH: Final = 300
MAX_PROPERTY_VALUE_LENGTH: Final = 500
MAX_PATTERN_LENGTH: Final = 200
MAX_ALLOWED_VALUES: Final = 50
MAX_PROPERTIES_PER_STEREOTYPE: Final = 30
MAX_STEREOTYPES_PER_PROFILE: Final = 100

#: Keys other conventions already read in `properties`: the IP addressing (docs/adr/0020)
#: and the validity dates (docs/adr/0059). A stereotype cannot redefine their format.
RESERVED_KEYS: Final = IPAM_PROPERTIES | frozenset({"valid_from", "valid_until"})
#: Values checked by a pattern are shorter still: the cost of a match grows with the
#: number of unbounded repetitions in the pattern and with the length of the value.
MAX_PATTERNED_VALUE_LENGTH: Final = 200
MAX_UNBOUNDED_REPETITIONS: Final = 3

_HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


class IconKey(StrEnum):
    """The closed set of icons the SPA ships — an icon is picked, never uploaded."""

    NONE = "none"
    DATABASE = "database"
    SERVER = "server"
    CLOUD = "cloud"
    USER = "user"
    LOCK = "lock"
    DOCUMENT = "document"
    GEAR = "gear"
    QUEUE = "queue"
    API = "api"


class PropertyKind(StrEnum):
    FREE = "free"
    ENUM = "enum"
    PATTERN = "pattern"


def _named(value: str, what: str, limit: int) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > limit:
        msg = f"{what} is between 1 and {limit} characters"
        raise ValueError(msg)
    return cleaned


def _refuse_catastrophic(pattern: str) -> None:
    """Refuse the shapes whose backtracking can explode: a quantified group that holds
    a quantifier or an alternation, and back-references.

    Deliberately stricter than the regex language: `(jpg|png)+` is refused too.
    A subset that is certainly linear beats a dependency (`re2`) for a field
    whose values are short.
    """
    holds: list[bool] = []  # per open group: a quantifier or a `|` is inside it
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            following = pattern[index + 1 : index + 2]
            if following.isdigit() and following != "0":
                msg = "a pattern cannot use back-references"
                raise ValueError(msg)
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char == "(":
            holds.append(False)
        elif char == ")":
            risky = holds.pop() if holds else False
            repeated = pattern[index + 1 : index + 2] in {"+", "*", "{"}
            if repeated and risky:
                msg = "a pattern cannot repeat a group that itself repeats or alternates"
                raise ValueError(msg)
            if holds and (risky or repeated):
                holds[-1] = True
        elif char in {"+", "*", "{", "|"} and holds:
            holds[-1] = True
        index += 1


def _refuse_stacked_repetitions(pattern: str) -> None:
    """Refuse more than a few unbounded repetitions: `a*a*a*a*b` backtracks in polynomial
    time of that degree on `aaaa...`, which is slow enough on 200 characters."""
    count = 0
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_class:
            in_class = char != "]"
        elif char == "[":
            in_class = True
        elif char in {"*", "+"}:
            count += 1
        elif char == "{":
            close = pattern.find("}", index)
            body = pattern[index + 1 : close] if close > index else ""
            if "," in body:
                low, _, high = body.partition(",")
                if low != high or not high:
                    count += 1
        index += 1
    if count > MAX_UNBOUNDED_REPETITIONS:
        msg = f"a pattern has at most {MAX_UNBOUNDED_REPETITIONS} repetitions"
        raise ValueError(msg)


def check_pattern(pattern: str) -> None:
    """Accept only a pattern that is short, compiles, and cannot backtrack catastrophically."""
    if not pattern or len(pattern) > MAX_PATTERN_LENGTH:
        msg = f"a pattern is at most {MAX_PATTERN_LENGTH} characters"
        raise ValueError(msg)
    _refuse_catastrophic(pattern)
    _refuse_stacked_repetitions(pattern)
    try:
        re.compile(pattern)
    except re.error as error:
        msg = f"not a valid pattern: {error}"
        raise ValueError(msg) from error


@dataclass(frozen=True, slots=True)
class StereotypeProperty:
    """One property a stereotype expects: a key in `elements.properties`, and how to read it."""

    key: str
    label: str
    required: bool = False
    kind: PropertyKind = PropertyKind.FREE
    allowed_values: tuple[str, ...] = ()
    pattern: str | None = None
    help: str = ""

    def __post_init__(self) -> None:
        if not PROPERTY_KEY.match(self.key):
            msg = f"property name {self.key!r} is not a plain identifier"
            raise ValueError(msg)
        if self.key in RESERVED_KEYS:
            msg = f"property name {self.key!r} is reserved by a convention of the model"
            raise ValueError(msg)
        _named(self.label, "a property label", MAX_LABEL_LENGTH)
        if len(self.help) > MAX_HELP_LENGTH:
            msg = f"a property help is at most {MAX_HELP_LENGTH} characters"
            raise ValueError(msg)
        if self.kind is PropertyKind.ENUM:
            values = self.allowed_values
            if not values or len(values) > MAX_ALLOWED_VALUES or len(set(values)) != len(values):
                msg = f"an enumeration has 1 to {MAX_ALLOWED_VALUES} distinct allowed values"
                raise ValueError(msg)
            if any(not v or len(v) > MAX_PROPERTY_VALUE_LENGTH for v in values):
                msg = "an allowed value is between 1 and 500 characters"
                raise ValueError(msg)
            if self.pattern is not None:
                msg = "an enumeration has no pattern"
                raise ValueError(msg)
        elif self.kind is PropertyKind.PATTERN:
            if self.pattern is None:
                msg = "a pattern property needs its pattern"
                raise ValueError(msg)
            check_pattern(self.pattern)
            if self.allowed_values:
                msg = "a pattern property has no allowed values"
                raise ValueError(msg)
        elif self.allowed_values or self.pattern is not None:
            msg = "a free property has neither allowed values nor a pattern"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Stereotype:
    id: UUID
    profile_id: UUID
    name: str
    base_type: ElementType
    colour: str | None
    icon_key: IconKey
    properties: tuple[StereotypeProperty, ...] = ()

    def __post_init__(self) -> None:
        _named(self.name, "a stereotype name", MAX_NAME_LENGTH)
        if self.base_type.is_connector:
            msg = "a stereotype cannot extend a connector (a junction is not a modelled element)"
            raise ValueError(msg)
        if self.colour is not None and not _HEX_COLOUR.match(self.colour):
            msg = "a stereotype colour is #rrggbb"
            raise ValueError(msg)
        if len(self.properties) > MAX_PROPERTIES_PER_STEREOTYPE:
            msg = f"a stereotype has at most {MAX_PROPERTIES_PER_STEREOTYPE} properties"
            raise ValueError(msg)
        keys = [p.key for p in self.properties]
        if len(set(keys)) != len(keys):
            msg = "a property key is declared twice in one stereotype"
            raise ValueError(msg)

    @property
    def ref(self) -> StereotypeRef:
        return StereotypeRef(
            id=self.id, name=self.name, colour=self.colour, icon_key=self.icon_key.value
        )


@dataclass(frozen=True, slots=True)
class Profile:
    id: UUID
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    stereotypes: tuple[Stereotype, ...] = ()

    def __post_init__(self) -> None:
        _named(self.name, "a profile name", MAX_NAME_LENGTH)
        names = [s.name for s in self.stereotypes]
        if len(set(names)) != len(names):
            msg = "a stereotype name is used twice in one profile"
            raise ValueError(msg)
        if len(names) > MAX_STEREOTYPES_PER_PROFILE:
            msg = f"a profile has at most {MAX_STEREOTYPES_PER_PROFILE} stereotypes"
            raise ValueError(msg)


def check_value(prop: StereotypeProperty, value: str) -> None:
    """The format of a value that is there. Blank means "not set" and is never checked."""
    if len(value) > MAX_PROPERTY_VALUE_LENGTH:
        msg = f"{prop.key}: a value is at most {MAX_PROPERTY_VALUE_LENGTH} characters"
        raise ValueError(msg)
    if prop.kind is PropertyKind.PATTERN and len(value) > MAX_PATTERNED_VALUE_LENGTH:
        msg = f"{prop.key}: a patterned value is at most {MAX_PATTERNED_VALUE_LENGTH} characters"
        raise ValueError(msg)
    if prop.kind is PropertyKind.ENUM and value not in prop.allowed_values:
        msg = f"{prop.key}: {value!r} is not one of {', '.join(prop.allowed_values)}"
        raise ValueError(msg)
    if prop.kind is PropertyKind.PATTERN and not re.fullmatch(prop.pattern or "", value):
        msg = f"{prop.key}: {value!r} does not match the expected form ({prop.pattern})"
        raise ValueError(msg)


def _declared(stereotypes: Sequence[Stereotype]) -> dict[str, StereotypeProperty]:
    declared: dict[str, StereotypeProperty] = {}
    owner: dict[str, str] = {}
    for stereotype in stereotypes:
        for prop in stereotype.properties:
            if prop.key in declared:
                msg = (
                    f"property {prop.key!r} is declared by both {owner[prop.key]!r} "
                    f"and {stereotype.name!r}: an element cannot carry both"
                )
                raise ValueError(msg)
            declared[prop.key] = prop
            owner[prop.key] = stereotype.name
    return declared


def validate_stereotype_properties(
    properties: Mapping[str, str], stereotypes: Sequence[Stereotype]
) -> dict[str, str]:
    """The properties to store, once the format of each declared value is checked.

    A key nobody declared stays free: an element keeps its own attributes.
    `required` is not enforced here — see `missing_required`.
    """
    declared = _declared(stereotypes)
    for key, prop in declared.items():
        value = properties.get(key)
        if value is not None and value.strip():
            check_value(prop, value)
    return dict(properties)


def missing_required(
    properties: Mapping[str, str], stereotypes: Sequence[Stereotype]
) -> tuple[str, ...]:
    """The required keys that are absent or blank — what a quality rule reports."""
    declared = _declared(stereotypes)
    return tuple(
        sorted(
            key
            for key, prop in declared.items()
            if prop.required and not properties.get(key, "").strip()
        )
    )


def profile_to_document(profile: Profile) -> dict[str, Any]:
    """A profile as a portable, versioned document — ids and dates are not part of it."""
    return {
        "profile_format": PROFILE_FORMAT,
        "name": profile.name,
        "description": profile.description,
        "stereotypes": [
            {
                "name": s.name,
                "base_type": s.base_type.value,
                "colour": s.colour,
                "icon_key": s.icon_key.value,
                "properties": [
                    {
                        "key": p.key,
                        "label": p.label,
                        "required": p.required,
                        "kind": p.kind.value,
                        "allowed_values": list(p.allowed_values),
                        "pattern": p.pattern,
                        "help": p.help,
                    }
                    for p in s.properties
                ],
            }
            for s in profile.stereotypes
        ],
    }


def profile_from_document(document: Mapping[str, Any], *, now: datetime) -> Profile:
    """A new profile from a document, through the very constructors a manual write uses."""
    if document.get("profile_format") != PROFILE_FORMAT:
        msg = f"profile_format must be {PROFILE_FORMAT}"
        raise ValueError(msg)
    profile_id = uuid4()
    try:
        stereotypes = tuple(
            Stereotype(
                id=uuid4(),
                profile_id=profile_id,
                name=raw["name"],
                base_type=ElementType(raw["base_type"]),
                colour=raw["colour"],
                icon_key=IconKey(raw["icon_key"]),
                properties=tuple(
                    StereotypeProperty(
                        key=p["key"],
                        label=p["label"],
                        required=p["required"],
                        kind=PropertyKind(p["kind"]),
                        allowed_values=tuple(p["allowed_values"]),
                        pattern=p["pattern"],
                        help=p["help"],
                    )
                    for p in raw["properties"]
                ),
            )
            for raw in document["stereotypes"]
        )
        return Profile(
            id=profile_id,
            name=document["name"],
            description=document["description"].strip(),
            created_at=now,
            updated_at=now,
            stereotypes=stereotypes,
        )
    except KeyError as error:
        msg = f"the profile document lacks {error}"
        raise ValueError(msg) from error
