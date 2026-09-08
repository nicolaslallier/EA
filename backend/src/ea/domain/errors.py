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


class NotAddressableError(DomainError):
    """An IP address was written onto something that cannot answer on one.

    A business process has no interface; an address on it is a modelling
    mistake no later query can undo — see `domain/ipam.py`.
    """


class NotASubnetError(DomainError):
    """A prefix was asked of an element that declares none, or written onto
    something that is not a `communication_network`."""


class DuplicateNetworkError(DomainError):
    """A prefix is already declared in that routing scope."""


class AddressAlreadyAssignedError(DomainError):
    """Another element already answers on that address in that scope."""


class AddressNotAssignedError(DomainError):
    """An address was released, or looked up, and nothing holds it."""


class AddressOutsideAnyNetworkError(DomainError):
    """No declared subnet of that scope holds the address.

    Refused rather than stored: an inventory that accepts addresses belonging
    to no subnet is one nobody can reconcile, and the usual cause is a typo or
    a subnet that was never declared.
    """


class NetworkExhaustedError(DomainError):
    """A subnet was asked for an address and has none left to give."""
