"""Domain errors.

They carry no HTTP status and no framework type: `api/` maps them onto the
error envelope, `services/` raises them.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base class for every rule this domain enforces."""


class IllegalRelationshipError(DomainError):
    """A relationship the ArchiMate metamodel does not allow between two types."""


class CyclicContainmentError(DomainError):
    """A composition or aggregation that would close a containment loop."""


class ElementNotFoundError(DomainError):
    """An element referenced by id is absent from the repository."""


class DuplicateElementError(DomainError):
    """An element name is already taken inside its type."""
