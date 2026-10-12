"""Each report refusal reaches a client as its own typed status."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from ea.api.errors import register_error_handlers
from ea.domain.errors import (
    ReportBusyError,
    ReportFormatUnavailableError,
    ReportTooLargeError,
    UnknownReportTemplateError,
    UnsupportedReportSubjectError,
)

pytestmark = pytest.mark.asyncio

CASES = {
    "too-large": (ReportTooLargeError, 422, "report_too_large"),
    "no-format": (ReportFormatUnavailableError, 501, "format_unavailable"),
    "busy": (ReportBusyError, 429, "report_busy"),
    "no-template": (UnknownReportTemplateError, 404, "not_found"),
    "no-subject": (UnsupportedReportSubjectError, 422, "unsupported_subject"),
}


@pytest.mark.parametrize("name", CASES)
async def test_a_report_refusal_has_its_own_status_and_code(name: str) -> None:
    error, expected_status, expected_code = CASES[name]
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise error("the ceiling is 500 elements")

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/boom")

    assert response.status_code == expected_status
    assert response.json()["error"] == expected_code
    assert "500 elements" in response.json()["detail"]
