"""The ArchiMate 3.2 metamodel: what an architecture element may be, and how
elements may be connected.

Framework-free by construction — see `CLAUDE.md`, "Dependency direction".
"""

from ea.domain.archimate.relations import (
    AccessType,
    RelationshipCategory,
    RelationshipType,
)
from ea.domain.archimate.rules import (
    permits,
    permitted_relationships,
    permitted_targets,
    validate_relationship,
)
from ea.domain.archimate.taxonomy import Aspect, ElementKind, ElementType, Layer

__all__ = [
    "AccessType",
    "Aspect",
    "ElementKind",
    "ElementType",
    "Layer",
    "RelationshipCategory",
    "RelationshipType",
    "permits",
    "permitted_relationships",
    "permitted_targets",
    "validate_relationship",
]
