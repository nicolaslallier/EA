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
    CyclicContainmentError,
    DocumentNotFoundError,
    DomainError,
    DuplicateDocumentError,
    DuplicateElementError,
    ElementNotFoundError,
    IllegalRelationshipError,
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


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _handle_domain_error)
    app.add_exception_handler(ValueError, _handle_value_error)
