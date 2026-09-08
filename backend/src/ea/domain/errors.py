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


class DuplicateDocumentError(DomainError):
    """An element already carries a document under that file name."""


class DocumentNotFoundError(DomainError):
    """A document referenced by id is absent from the store."""


class SearchUnavailableError(DomainError):
    """Semantic search was asked for on a deployment that has no index.

    A deliberate configuration and not a bug — `EA_EMBEDDINGS_ENABLED` is off,
    so nothing was ever embedded — which is why it is a domain failure with a
    message rather than a crash: the caller, agent or human, is told what is
    missing instead of being handed "error executing tool".
    """
