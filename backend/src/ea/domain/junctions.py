"""A junction is an element: the AND/OR that several links meet at (docs/adr/0062).

It carries one fact beside its name, `junction_type` (`and` or `or`), kept in
`properties` like the IPAM's address. Exchange writes the same fact in the
element's type (`AndJunction`, `OrJunction`); `ea.domain.exchange` translates.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from ea.domain.archimate import ElementType
from ea.domain.errors import InvalidJunctionError

JUNCTION_TYPE_PROPERTY: Final = "junction_type"
JUNCTION_KINDS: Final = ("and", "or")
DEFAULT_JUNCTION_KIND: Final = "and"


def validate_junction_properties(
    element_type: ElementType, properties: Mapping[str, str] | None
) -> Mapping[str, str] | None:
    """A junction is `and` unless it says `or`; anything else is refused. Others pass untouched.

    Same contract as `validate_ipam_properties`: what comes back is what is stored.
    The message names the key, never the value it was given.
    """
    if element_type is not ElementType.JUNCTION:
        return properties
    canonical = dict(properties or {})
    kind = canonical.get(JUNCTION_TYPE_PROPERTY, "").strip().lower() or DEFAULT_JUNCTION_KIND
    if kind not in JUNCTION_KINDS:
        msg = f"{JUNCTION_TYPE_PROPERTY!r} is `and` or `or`"
        raise InvalidJunctionError(msg)
    canonical[JUNCTION_TYPE_PROPERTY] = kind
    return canonical
