"""The eleven relationship types of ArchiMate 3.2 and their qualifiers."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class RelationshipCategory(StrEnum):
    """The four families the specification groups relationships into."""

    STRUCTURAL = "structural"
    DEPENDENCY = "dependency"
    DYNAMIC = "dynamic"
    OTHER = "other"


class RelationshipType(StrEnum):
    """Every relationship type ArchiMate 3.2 defines.

    Members are ordered from the strongest structural relationship to the
    weakest dependency, which is the order the specification uses when it
    derives an indirect relationship from a chain of direct ones.
    """

    COMPOSITION = "composition"
    AGGREGATION = "aggregation"
    ASSIGNMENT = "assignment"
    REALIZATION = "realization"
    SERVING = "serving"
    ACCESS = "access"
    INFLUENCE = "influence"
    ASSOCIATION = "association"
    TRIGGERING = "triggering"
    FLOW = "flow"
    SPECIALIZATION = "specialization"

    @property
    def category(self) -> RelationshipCategory:
        return _CATEGORY[self]

    @property
    def strength(self) -> int:
        """Higher binds tighter; used to pick the weakest link of a derived chain."""
        return _STRENGTH[self]

    @property
    def impact_follows_direction(self) -> bool:
        """Whether an outage at the source propagates *along* the arrow.

        ArchiMate arrows do not all point the same way with respect to
        dependency. A service *serves* a process, so losing the service breaks
        the process: impact runs along the arrow. A whole *composes* a part, so
        losing the part breaks the whole: impact runs against it. Impact
        analysis has to walk each hop in the right direction or it answers a
        different question than the one asked.
        """
        return self not in _IMPACT_RUNS_AGAINST_THE_ARROW

    @property
    def label(self) -> str:
        """The Neo4j relationship type, e.g. `SERVING`."""
        return self.value.upper()


class AccessType(StrEnum):
    """How an `ACCESS` relationship touches the passive element it points at."""

    ACCESS = "access"
    READ = "read"
    WRITE = "write"
    READ_WRITE = "read_write"


#: Relationships whose dependency runs from target to source: the part a whole
#: is made of, the data a function reads, the general type a specialisation
#: refines. Everything else carries dependency along its own direction.
_IMPACT_RUNS_AGAINST_THE_ARROW: frozenset[RelationshipType] = frozenset(
    {
        RelationshipType.COMPOSITION,
        RelationshipType.AGGREGATION,
        RelationshipType.ACCESS,
        RelationshipType.SPECIALIZATION,
    }
)


#: The family each relationship belongs to, per the specification.
_CATEGORY: Final[dict[RelationshipType, RelationshipCategory]] = {
    RelationshipType.COMPOSITION: RelationshipCategory.STRUCTURAL,
    RelationshipType.AGGREGATION: RelationshipCategory.STRUCTURAL,
    RelationshipType.ASSIGNMENT: RelationshipCategory.STRUCTURAL,
    RelationshipType.REALIZATION: RelationshipCategory.STRUCTURAL,
    RelationshipType.SERVING: RelationshipCategory.DEPENDENCY,
    RelationshipType.ACCESS: RelationshipCategory.DEPENDENCY,
    RelationshipType.INFLUENCE: RelationshipCategory.DEPENDENCY,
    RelationshipType.ASSOCIATION: RelationshipCategory.DEPENDENCY,
    RelationshipType.TRIGGERING: RelationshipCategory.DYNAMIC,
    RelationshipType.FLOW: RelationshipCategory.DYNAMIC,
    RelationshipType.SPECIALIZATION: RelationshipCategory.OTHER,
}

#: How tightly each relationship binds. The specification derives an indirect
#: relationship from a chain by keeping the weakest link, so the order matters.
_STRENGTH: Final[dict[RelationshipType, int]] = {
    RelationshipType.COMPOSITION: 10,
    RelationshipType.AGGREGATION: 9,
    RelationshipType.ASSIGNMENT: 8,
    RelationshipType.REALIZATION: 7,
    RelationshipType.SERVING: 6,
    RelationshipType.ACCESS: 5,
    RelationshipType.INFLUENCE: 4,
    RelationshipType.ASSOCIATION: 3,
    RelationshipType.TRIGGERING: 2,
    RelationshipType.FLOW: 1,
    RelationshipType.SPECIALIZATION: 0,
}
