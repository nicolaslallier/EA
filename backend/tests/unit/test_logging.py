"""What the process actually prints, and what it must never print.

`logging_config` is a pure function of the settings for the same reason
`graphLayout` is pure on the other side: the interesting decisions — which
level, which format, which of the noisy third-party loggers is opened — are
then checked without touching the logging of the process running the tests.
"""

from __future__ import annotations

import json
import logging
import logging.config
from typing import Any

import pytest

from ea.core.config import Settings
from ea.core.logging import (
    CYPHER_LOGGER,
    EMBEDDINGS_LOGGER,
    NO_REQUEST,
    PLAIN_FORMAT,
    REQUESTS_LOGGER,
    SQL_LOGGER,
    JsonFormatter,
    PlainFormatter,
    RedactingFilter,
    RequestIdFilter,
    logging_config,
    redact,
    request_id,
    summarise,
)


def settings_of(**overrides: Any) -> Settings:
    """Settings built from the arguments alone — never from a local `.env`."""
    overrides.setdefault("debug", True)
    return Settings(_env_file=None, **overrides)  # type: ignore[call-arg]


def levels_of(config: dict[str, Any]) -> dict[str, str]:
    return {name: logger["level"] for name, logger in config["loggers"].items()}


class TestLevels:
    def test_the_level_is_the_one_configured(self) -> None:
        config = logging_config(settings_of(log_level="DEBUG"))

        assert config["root"]["level"] == "DEBUG"
        assert levels_of(config)["ea"] == "DEBUG"

    def test_a_level_is_read_however_it_is_spelled(self) -> None:
        assert settings_of(log_level="debug").log_level == "DEBUG"

    def test_a_level_that_is_not_one_is_refused_at_boot(self) -> None:
        """Better than starting silently at a level nobody meant."""
        with pytest.raises(ValueError, match="log_level"):
            settings_of(log_level="chatty")


class TestFormat:
    def test_debug_prints_lines_a_human_reads(self) -> None:
        config = logging_config(settings_of(debug=True))

        assert config["handlers"]["console"]["formatter"] == "plain"

    def test_a_deployment_prints_json(self) -> None:
        config = logging_config(settings_of(debug=False, neo4j_password="x", postgres_password="x"))

        assert config["handlers"]["console"]["formatter"] == "json"

    def test_the_choice_can_be_stated_rather_than_derived(self) -> None:
        config = logging_config(settings_of(debug=True, log_json=True))

        assert config["handlers"]["console"]["formatter"] == "json"


class TestTheNoisyStreams:
    """Each is off by default and has its own switch — see docs/adr/0021."""

    def test_cypher_is_quiet_until_it_is_asked_for(self) -> None:
        assert levels_of(logging_config(settings_of()))[CYPHER_LOGGER] == "WARNING"

    def test_cypher_opens_the_driver_too(self) -> None:
        levels = levels_of(logging_config(settings_of(log_cypher=True)))

        assert levels[CYPHER_LOGGER] == "DEBUG"
        assert levels["neo4j"] == "DEBUG"

    def test_sql_is_the_engine_logger_sqlalchemy_already_has(self) -> None:
        assert levels_of(logging_config(settings_of(log_sql=True)))[SQL_LOGGER] == "INFO"
        assert levels_of(logging_config(settings_of()))[SQL_LOGGER] == "WARNING"

    def test_embeddings_opens_our_traces_and_the_http_client(self) -> None:
        levels = levels_of(logging_config(settings_of(log_embeddings=True)))

        assert levels[EMBEDDINGS_LOGGER] == "DEBUG"
        assert levels["httpx"] == "DEBUG"

    def test_a_global_debug_level_does_not_drag_them_open(self) -> None:
        """`EA_LOG_LEVEL=DEBUG` is for our own code, not for three firehoses."""
        levels = levels_of(logging_config(settings_of(log_level="DEBUG")))

        assert levels[CYPHER_LOGGER] == "WARNING"
        assert levels[SQL_LOGGER] == "WARNING"
        assert levels[EMBEDDINGS_LOGGER] == "WARNING"


class TestTheAccessLog:
    def test_one_access_line_per_request_and_it_is_ours(self) -> None:
        """Ours carries the request id and the duration; uvicorn's carries neither."""
        levels = levels_of(logging_config(settings_of(log_requests=True)))

        assert levels[REQUESTS_LOGGER] == "INFO"
        assert levels["uvicorn.access"] == "WARNING"

    def test_turning_ours_off_gives_uvicorn_its_own_back(self) -> None:
        levels = levels_of(logging_config(settings_of(log_requests=False)))

        assert levels[REQUESTS_LOGGER] == "WARNING"
        assert levels["uvicorn.access"] == "INFO"


class TestTheConfigItself:
    def test_dictconfig_accepts_it(self) -> None:
        """A config that only looks right is a process that starts up mute."""
        logging.config.dictConfig(logging_config(settings_of(log_level="DEBUG")))

        assert logging.getLogger("ea").getEffectiveLevel() == logging.DEBUG

    def test_nothing_filters_at_the_handler(self) -> None:
        """The levels are decided per logger; a handler level would override them."""
        config = logging_config(settings_of())

        assert "level" not in config["handlers"]["console"]


class TestTheRequestId:
    def test_a_record_carries_the_request_being_served(self) -> None:
        record = logging.makeLogRecord({"msg": "hello"})
        token = request_id.set("abc123")
        try:
            RequestIdFilter().filter(record)
        finally:
            request_id.reset(token)

        assert record.request_id == "abc123"  # type: ignore[attr-defined]

    def test_a_line_logged_outside_any_request_says_so(self) -> None:
        record = logging.makeLogRecord({"msg": "booting"})

        RequestIdFilter().filter(record)

        assert record.request_id == NO_REQUEST  # type: ignore[attr-defined]


class TestRedaction:
    """Redacted once, at the handler, rather than at each of a hundred calls."""

    @pytest.mark.parametrize(
        "line",
        [
            "connecting to postgresql+asyncpg://ea:hunter2@192.168.1.252/ea",
            "headers: {'authorization': 'Bearer hunter2'}",
            "EA_NEO4J_PASSWORD=hunter2",
            'body: {"api_key": "hunter2"}',
            "token=hunter2",
        ],
    )
    def test_a_secret_never_reaches_the_stream(self, line: str) -> None:
        assert "hunter2" not in redact(line)

    def test_what_is_left_still_says_where_it_was_going(self) -> None:
        redacted = redact("postgresql+asyncpg://ea:hunter2@192.168.1.252/ea")

        assert "192.168.1.252" in redacted
        assert redacted.count("ea") >= 2

    def test_an_ordinary_line_is_untouched(self) -> None:
        assert redact("element created in 12 ms") == "element created in 12 ms"

    def test_the_filter_redacts_the_formatted_message(self) -> None:
        record = logging.makeLogRecord({"msg": "connecting as %s", "args": ("Bearer hunter2",)})

        RedactingFilter().filter(record)

        assert "hunter2" not in record.getMessage()


class TestJsonLines:
    def format(self, record: logging.LogRecord) -> dict[str, Any]:
        RequestIdFilter().filter(record)
        parsed: dict[str, Any] = json.loads(JsonFormatter().format(record))
        return parsed

    def test_one_object_per_line(self) -> None:
        line = self.format(
            logging.makeLogRecord({"msg": "element created", "levelname": "INFO", "name": "ea.api"})
        )

        assert line["message"] == "element created"
        assert line["level"] == "INFO"
        assert line["logger"] == "ea.api"
        assert line["request_id"] == NO_REQUEST
        assert "timestamp" in line

    def test_the_fields_a_call_site_added_are_fields_and_not_prose(self) -> None:
        """`extra={"status": 404}` is what makes a log searchable."""
        line = self.format(logging.makeLogRecord({"msg": "done", "status": 404, "path": "/x"}))

        assert line["status"] == 404
        assert line["path"] == "/x"

    def test_a_traceback_is_carried_as_one_field(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.getLogger("ea").makeRecord(
                "ea", logging.ERROR, "f", 1, "failed", None, __import__("sys").exc_info()
            )

        line = self.format(record)

        assert "ValueError: boom" in line["exception"]

    def test_a_secret_in_a_field_is_redacted_too(self) -> None:
        line = self.format(logging.makeLogRecord({"msg": "sent", "dsn": "db://u:hunter2@h/d"}))

        assert "hunter2" not in json.dumps(line)


class TestSummarise:
    def test_a_short_value_is_itself(self) -> None:
        assert summarise("srv-app-01") == "srv-app-01"

    def test_a_document_body_is_cut_rather_than_printed(self) -> None:
        summary = summarise("x" * 5000, limit=40)

        assert len(summary) <= 60
        assert summary.endswith("... (5000 chars)")

    def test_a_secret_shaped_value_is_redacted(self) -> None:
        assert "hunter2" not in summarise("Bearer hunter2")


class TestPlainLines:
    def line(self, record: logging.LogRecord) -> str:
        RequestIdFilter().filter(record)
        return PlainFormatter(PLAIN_FORMAT).format(record)

    def test_the_id_and_the_message_are_the_line(self) -> None:
        line = self.line(logging.makeLogRecord({"msg": "element created", "name": "ea.services"}))

        assert "element created" in line
        assert f"[{NO_REQUEST}]" in line

    def test_the_fields_are_appended_rather_than_dropped(self) -> None:
        """A duration visible only in the JSON is a duration nobody ever reads."""
        line = self.line(logging.makeLogRecord({"msg": "GET /elements", "duration_ms": 3.2}))

        assert "duration_ms=3.2" in line

    def test_a_line_with_nothing_attached_gains_no_dangling_dash(self) -> None:
        assert self.line(logging.makeLogRecord({"msg": "booting"})).endswith("booting")

    def test_a_secret_in_a_field_is_redacted_here_too(self) -> None:
        line = self.line(logging.makeLogRecord({"msg": "sent", "dsn": "db://u:hunter2@h/d"}))

        assert "hunter2" not in line
