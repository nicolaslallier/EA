"""Turning domain errors into the API's single error envelope.

Which failure maps to which status is a decision, so it lives in one table
rather than being spread over `raise HTTPException` calls in the routers. The
message of a `DomainError` is written for a human and names no internals; a
failure with any other cause never reaches the client at all.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from ea.domain.errors import (
    AddressAlreadyAssignedError,
    AddressNotAssignedError,
    AddressOutsideAnyNetworkError,
    CyclicContainmentError,
    DocumentNotFoundError,
    DomainError,
    DuplicateDocumentError,
    DuplicateElementError,
    DuplicateNetworkError,
    ElementNotFoundError,
    IllegalRelationshipError,
    NetworkExhaustedError,
    NotAddressableError,
    NotASubnetError,
    SearchUnavailableError,
)

logger = logging.getLogger(__name__)

#: Domain failure -> (HTTP status, stable machine-readable code).
_STATUS: Final[dict[type[Exception], tuple[int, str]]] = {
    ElementNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DocumentNotFoundError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateElementError: (status.HTTP_409_CONFLICT, "duplicate"),
    DuplicateDocumentError: (status.HTTP_409_CONFLICT, "duplicate"),
    CyclicContainmentError: (status.HTTP_409_CONFLICT, "cyclic_containment"),
    # A deployment with `EA_EMBEDDINGS_ENABLED` off, which is a configuration
    # and not a request that was wrong — hence 503 and not 4xx.
    SearchUnavailableError: (status.HTTP_503_SERVICE_UNAVAILABLE, "search_unavailable"),
    IllegalRelationshipError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "illegal_relationship",
    ),
    # --- IP address management (docs/adr/0020) ---
    # Each refusal gets its own code rather than a shared 409, because these
    # are the ones a caller is expected to *act* on: an address already taken
    # means pick another, a subnet exhausted means declare a bigger one, and an
    # address outside every subnet means declare the subnet first. An agent
    # told only "conflict" would retry the call that just failed.
    AddressNotAssignedError: (status.HTTP_404_NOT_FOUND, "not_found"),
    DuplicateNetworkError: (status.HTTP_409_CONFLICT, "duplicate"),
    AddressAlreadyAssignedError: (status.HTTP_409_CONFLICT, "address_taken"),
    NetworkExhaustedError: (status.HTTP_409_CONFLICT, "network_exhausted"),
    NotAddressableError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "not_addressable"),
    NotASubnetError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "not_a_subnet"),
    AddressOutsideAnyNetworkError: (
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        "address_outside_any_network",
    ),
}

_FALLBACK: Final = (status.HTTP_400_BAD_REQUEST, "invalid_request")


async def _handle_domain_error(_: Request, error: Exception) -> JSONResponse:
    http_status, code = _STATUS.get(type(error), _FALLBACK)
    return JSONResponse(status_code=http_status, content={"error": code, "detail": str(error)})


async def _handle_value_error(_: Request, error: Exception) -> JSONResponse:
    """A `ValueError` from the domain is a rejected input, not a server fault."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"error": "invalid_input", "detail": str(error)},
    )


async def _handle_unexpected_error(_: Request, error: Exception) -> JSONResponse:
    """Anything nobody anticipated: the envelope, and not one word of the cause.

    Registered for `Exception`, which Starlette installs in its outermost
    middleware. That middleware re-raises once this answer is sent, so the
    traceback still reaches the ASGI server's log; the client, which may be any
    machine on the LAN (docs/adr/0016), gets a sentence that names nothing.
    """
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "internal_error", "detail": "The server failed to answer this request."},
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(ValueError, _handle_value_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
