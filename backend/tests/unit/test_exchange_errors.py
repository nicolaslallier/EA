"""Each exchange failure has its own status and its own stable code."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ea.api.errors import register_error_handlers
from ea.domain.errors import (
    ExchangeFileTooLargeError,
    ImportInProgressError,
    InvalidExchangeFileError,
    RowRefusedError,
    UnknownScopeError,
)


def answer(error: Exception) -> tuple[int, dict[str, str]]:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise error

    response = TestClient(app, raise_server_exceptions=False).get("/boom")
    return response.status_code, response.json()


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (InvalidExchangeFileError("not XML"), 422, "invalid_exchange_file"),
        (ExchangeFileTooLargeError("too big"), 413, "file_too_large"),
        (ImportInProgressError("busy"), 409, "import_in_progress"),
        (UnknownScopeError("no such scope"), 422, "unknown_scope"),
        (RowRefusedError("blank_name", "blank"), 422, "invalid_row"),
    ],
)
def test_each_failure_has_its_status_and_code(error: Exception, status: int, code: str) -> None:
    http_status, body = answer(error)

    assert http_status == status
    assert body == {"error": code, "detail": str(error)}


def test_a_refused_row_carries_its_code() -> None:
    refusal = RowRefusedError("blank_name", "an element name cannot be blank")

    assert refusal.code == "blank_name"
    assert str(refusal) == "an element name cannot be blank"
