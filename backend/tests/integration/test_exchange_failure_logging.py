"""A database refusal that nobody catches reaches the last handler without the file's values.

Real PostgreSQL, real driver messages: a unique violation (`Key (name)=(<value>)`), a CHECK
violation (`Failing row contains (<the row>)`) and a value too long for its column, raised in the
middle of an import by a statement the import does not expect to fail (so no row catches it and
nothing is a report line). The handler is the real one; no log record may carry the marker.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text

from ea.api.errors import AnsweringUnexpectedFailures
from ea.db.unit_of_work import ambient_session
from ea.services.caller import acting_as
from tests.conftest import an_editor
from tests.exchange_files import el, exchange_xml
from tests.integration.test_exchange_roundtrip import REAL, content, whole  # noqa: F401

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]

MARKER = f"SECRET-MARKER-{uuid4()}"
NOW = "now()"
DIAGRAM = "INSERT INTO diagrams (id, name, created_at, updated_at) VALUES (gen_random_uuid(), "
UNIQUE = [f"{DIAGRAM}'{MARKER}', {NOW}, {NOW})"] * 2
CHECK = [
    "INSERT INTO elements (id, element_type, layer, aspect, name, created_at, updated_at,"
    " approval_state, locked, revision, properties) VALUES (gen_random_uuid(), 'node',"
    f" 'technology', 'active_structure', '{MARKER}', {NOW}, {NOW}, 'bogus', false, 1, '{{}}')"
]
TOO_LONG = [f"{DIAGRAM}'{MARKER * 40}', {NOW}, {NOW})"]


@pytest.mark.parametrize("statements", [UNIQUE, CHECK, TOO_LONG], ids=["unique", "check", "length"])
async def test_no_record_of_a_database_failure_during_an_import_carries_the_file(
    whole: Any,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    statements: list[str],
) -> None:
    _, _, _, exchange, _ = whole

    async def exploding() -> frozenset[Any]:
        session = ambient_session()
        assert session is not None
        for statement in statements:
            await session.execute(text(statement))
        return frozenset()

    monkeypatch.setattr(exchange._repository, "known_relationship_keys", exploding)
    sent: list[dict[str, Any]] = []

    async def application(scope: Any, receive: Any, send: Any) -> None:
        with acting_as(an_editor()):
            await exchange.import_exchange(content(exchange_xml(el("a", "Node", MARKER))), REAL)

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": "/exchanges/imports"}

    class Collect(logging.Handler):
        def __init__(self) -> None:
            super().__init__(logging.DEBUG)
            self.records: list[logging.LogRecord] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.records.append(record)

    # Listen on the handler's own logger: another test's `dictConfig` may have shut or cut it off
    # from the root. (The statements' logger stays as the process configures it: closed.)
    collected = Collect()
    handler_logger = logging.getLogger("ea.api.errors")
    before = (handler_logger.disabled, handler_logger.level)
    handler_logger.disabled, handler_logger.level = False, logging.DEBUG
    handler_logger.addHandler(collected)
    try:
        await AnsweringUnexpectedFailures(application)(scope, receive, send)
    finally:
        handler_logger.removeHandler(collected)
        handler_logger.disabled, handler_logger.level = before

    assert sent[0]["status"] == 500
    ours = collected.records
    assert ours
    for record in ours:
        carried = [
            record.getMessage(),
            str(record.args),
            str(vars(record)),
            str(record.exc_text),
            str(record.stack_info),
        ]
        if record.exc_info:
            carried.append("".join(traceback.format_exception(*record.exc_info)))
        assert MARKER not in "\n".join(carried), record.getMessage()
    assert any("db_error" in vars(record) for record in ours)  # and it said what failed
