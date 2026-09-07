"""Which relationships the ArchiMate 3.2 metamodel allows between which elements.

Appendix B of the specification publishes this as a 61x61 matrix. That matrix is
not hand-written by its authors: it is *derived* from the structural rules of the
metamodel. This module encodes those rules directly, which keeps the answer
explainable ("access runs from behavior to passive structure") instead of
reducing it to an opaque lookup, and keeps the 3721 cells from drifting.

Where a documented pair does not follow from the general rules — a Product
aggregating the Business Services it bundles, an Artifact realizing the
Application Component it packages — it is listed in `_EXTRA_ALLOWED`. That set is
the extension point: adding a pair is one line and one test.
"""

from __future__ import annotations

from ea.domain.archimate.relations import RelationshipType
from ea.domain.archimate.taxonomy import Aspect, ElementKind, ElementType, Layer
from ea.domain.errors import IllegalRelationshipError

_R = RelationshipType
_E = ElementType

#: How abstract a layer is. Realization climbs this ladder: an Artifact realizes
#: a Data Object, a Data Object realizes a Business Object, never the reverse.
_ABSTRACTION_RANK: dict[Layer, int] = {
    Layer.OTHER: 0,
    Layer.IMPLEMENTATION_MIGRATION: 0,
    Layer.PHYSICAL: 1,
    Layer.TECHNOLOGY: 1,
    Layer.APPLICATION: 2,
    Layer.BUSINESS: 3,
    Layer.STRATEGY: 4,
    Layer.MOTIVATION: 5,
}

#: Elements that contain other elements regardless of aspect.
_CONTAINERS = frozenset({_E.GROUPING, _E.LOCATION, _E.PLATEAU})

#: A relationship whose source contains its target must not be reflexive.
_IRREFLEXIVE = frozenset({_R.COMPOSITION, _R.AGGREGATION, _R.SPECIALIZATION, _R.REALIZATION})

_ACTIVE_OR_BEHAVIOR = frozenset({Aspect.ACTIVE_STRUCTURE, Aspect.BEHAVIOR})

#: Pairs the specification allows that the general rules below do not derive.
_EXTRA_ALLOWED: frozenset[tuple[RelationshipType, ElementType, ElementType]] = frozenset(
    {
        # A Business Actor is assigned to the Business Role it plays.
        (_R.ASSIGNMENT, _E.BUSINESS_ACTOR, _E.BUSINESS_ROLE),
        # A Product bundles the services and the contract that define it.
        (_R.AGGREGATION, _E.PRODUCT, _E.BUSINESS_SERVICE),
        (_R.AGGREGATION, _E.PRODUCT, _E.APPLICATION_SERVICE),
        (_R.AGGREGATION, _E.PRODUCT, _E.TECHNOLOGY_SERVICE),
        (_R.COMPOSITION, _E.PRODUCT, _E.BUSINESS_SERVICE),
        (_R.COMPOSITION, _E.PRODUCT, _E.APPLICATION_SERVICE),
        # An Artifact is the deployable form of what it packages.
        (_R.REALIZATION, _E.ARTIFACT, _E.APPLICATION_COMPONENT),
        (_R.REALIZATION, _E.ARTIFACT, _E.SYSTEM_SOFTWARE),
        (_R.REALIZATION, _E.ARTIFACT, _E.NODE),
        # Implementation & Migration produces the things it delivers.
        (_R.REALIZATION, _E.WORK_PACKAGE, _E.DELIVERABLE),
        # A Representation is how a Business Object is perceived.
        (_R.REALIZATION, _E.REPRESENTATION, _E.BUSINESS_OBJECT),
    }
)


def _permits_composition(source: ElementType, target: ElementType) -> bool:
    """Containment: a container holds anything, otherwise aspects must agree."""
    if source in _CONTAINERS:
        return not target.is_connector
    return source.aspect is target.aspect


def _permits_assignment(source: ElementType, target: ElementType) -> bool:
    """Allocation of responsibility: active structure onto what it performs."""
    if source is _E.LOCATION:
        return True
    if source.aspect is not Aspect.ACTIVE_STRUCTURE:
        return False
    return (
        target.aspect is Aspect.BEHAVIOR
        or target.kind in (ElementKind.INTERFACE, ElementKind.COLLABORATION)
        or target is _E.ARTIFACT
    )


def _permits_realization(source: ElementType, target: ElementType) -> bool:
    """Making something abstract concrete — the ladder runs upward only."""
    if target.aspect is Aspect.MOTIVATION:
        return True
    if source.layer is Layer.IMPLEMENTATION_MIGRATION:
        return True
    if target.kind is ElementKind.SERVICE and source.aspect in _ACTIVE_OR_BEHAVIOR:
        return source.kind is not ElementKind.SERVICE
    if target.layer is Layer.STRATEGY:
        return _ABSTRACTION_RANK[source.layer] < _ABSTRACTION_RANK[Layer.STRATEGY]
    return (
        source.aspect is target.aspect
        and _ABSTRACTION_RANK[source.layer] < _ABSTRACTION_RANK[target.layer]
    )


def _permits_serving(source: ElementType, target: ElementType) -> bool:
    """A provider makes its functionality available to a consumer."""
    return source.aspect in _ACTIVE_OR_BEHAVIOR and target.aspect in _ACTIVE_OR_BEHAVIOR


def _permits_access(source: ElementType, target: ElementType) -> bool:
    """Behavior touches data; a Data Object never reads a function back."""
    return source.aspect is Aspect.BEHAVIOR and target.aspect is Aspect.PASSIVE_STRUCTURE


def _permits_dynamic(source: ElementType, target: ElementType) -> bool:
    """Triggering and flow chain elements of the same aspect over time."""
    if source is _E.PLATEAU and target is _E.PLATEAU:
        return True
    return source.aspect is target.aspect and source.aspect in _ACTIVE_OR_BEHAVIOR


def permits(
    relationship: RelationshipType,
    source: ElementType,
    target: ElementType,
    *,
    same_element: bool = False,
) -> bool:
    """Answer whether `source -[relationship]-> target` is legal ArchiMate.

    `same_element` says the two endpoints are the *same* element rather than two
    elements of the same type, which is what makes `A composes A` illegal while
    `component composes component` stays legal.
    """
    if same_element and relationship in _IRREFLEXIVE:
        return False

    # A junction only wires relationships together, so it inherits the legality
    # of whatever it stands in for — except specialization, which needs a type.
    if source.is_connector or target.is_connector:
        return relationship is not _R.SPECIALIZATION

    if (relationship, source, target) in _EXTRA_ALLOWED:
        return True

    match relationship:
        case _R.ASSOCIATION:
            return True
        case _R.SPECIALIZATION:
            return source is target
        case _R.COMPOSITION | _R.AGGREGATION:
            return _permits_composition(source, target)
        case _R.ASSIGNMENT:
            return _permits_assignment(source, target)
        case _R.REALIZATION:
            return _permits_realization(source, target)
        case _R.SERVING:
            return _permits_serving(source, target)
        case _R.ACCESS:
            return _permits_access(source, target)
        case _R.INFLUENCE:
            return target.aspect is Aspect.MOTIVATION
        case _R.TRIGGERING | _R.FLOW:
            return _permits_dynamic(source, target)

    raise AssertionError(f"unhandled relationship type: {relationship}")  # pragma: no cover


def validate_relationship(
    relationship: RelationshipType,
    source: ElementType,
    target: ElementType,
    *,
    same_element: bool = False,
) -> None:
    """Raise `IllegalRelationshipError` unless the triple is legal ArchiMate."""
    if permits(relationship, source, target, same_element=same_element):
        return
    reason = (
        "an element cannot contain or specialise itself"
        if same_element
        else ("the ArchiMate 3.2 metamodel does not allow this relationship")
    )
    msg = f"{relationship.value}: {source.value} -> {target.value} is not permitted — {reason}"
    raise IllegalRelationshipError(msg)


def permitted_relationships(
    source: ElementType, target: ElementType
) -> tuple[RelationshipType, ...]:
    """Every relationship legal between two element types, strongest first.

    The frontend uses this to offer only the relationships that will be accepted.
    """
    return tuple(
        relationship
        for relationship in sorted(RelationshipType, key=lambda r: -r.strength)
        if permits(relationship, source, target)
    )


def permitted_targets(
    relationship: RelationshipType, source: ElementType
) -> tuple[ElementType, ...]:
    """Every element type `source` may point at with `relationship`."""
    return tuple(target for target in ElementType if permits(relationship, source, target))
