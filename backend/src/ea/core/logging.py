"""What this process prints, and what it must never print.

Until now nothing configured logging at all: `logging.getLogger(__name__)` was
called in six modules, uvicorn configured its own three loggers, and every
`logger.info(...)` under `ea.` fell through to a root logger with no handler —
visible nowhere, at any level. The lines existed; the reader did not.

Four decisions live here — see `docs/adr/0021`.

**It is a pure function of the settings.** `logging_config` returns the
`dictConfig` dictionary and touches nothing; `configure_logging` is the one
line that applies it, called from the process entry point and not from
`create_app`, so a test that builds an app never reconfigures the logging of
the process running it.

**Each noisy stream has its own name and its own switch.** `EA_LOG_LEVEL` is
the level of *our* reasoning; the Cypher, the SQL and the embedding round trips
are three firehoses opened one at a time, by name, so raising the first does not
drown the reader in the other three.

**The request id is a `ContextVar`, injected by a filter.** A call site logs
what it has to say; which request it was serving is not its business, and
threading an id through every signature would be the alternative.

**Secrets are redacted at the handler.** `CLAUDE.md` says redaction belongs to
the processor and not to each call site, and the reason is exactly that a call
site cannot be relied on: the DSN in a driver's own error message was written
by SQLAlchemy, not by us.
"""

from __future__ import annotations

import json
import logging
import logging.config
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from ea.core.config import Settings

#: The id of the request being served, or `NO_REQUEST` outside one. Set by the
#: request-logging middleware, read by `RequestIdFilter`.
request_id: ContextVar[str] = ContextVar("request_id", default="-")

#: What a line logged outside any request carries — boot, shutdown, a reindex.
NO_REQUEST: Final = "-"

# The names each switch opens. They are constants rather than string literals
# spread over four modules for one reason: a logger whose name drifts from the
# setting that opens it is a switch that silently does nothing.
#: One line per HTTP request, with its id and its duration.
REQUESTS_LOGGER: Final = "ea.requests"
#: Every Cypher statement and how long it took — `EA_LOG_CYPHER`.
CYPHER_LOGGER: Final = "ea.cypher"
#: Every call to the embedding service — `EA_LOG_EMBEDDINGS`.
EMBEDDINGS_LOGGER: Final = "ea.embeddings"
#: Every MCP tool an agent calls, its outcome and its duration.
TOOLS_LOGGER: Final = "ea.mcp.tools"
#: SQLAlchemy's own, which already prints statements at INFO — `EA_LOG_SQL`.
SQL_LOGGER: Final = "sqlalchemy.engine"

#: What is left where a secret was.
MASK: Final = "***"

#: The shapes a secret arrives in. Deliberately few and deliberately blunt: a
#: pattern that misses costs a leaked password, a pattern that over-matches
#: costs a masked word. The first is the URL userinfo SQLAlchemy, asyncpg and
#: the Neo4j driver all print in their own error messages.
_SECRETS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"(?P<keep>://[^:/@\s]+:)[^@\s]+(?P<tail>@)"),
    re.compile(r"(?P<keep>\b(?:bearer|basic)\s+)\S+", re.IGNORECASE),
    re.compile(
        r"(?P<keep>[\"']?\b\w*(?:password|passwd|secret|token|api[_-]?key)\w*[\"']?\s*[=:]\s*[\"']?)"
        r"[^\s,;}\"']+",
        re.IGNORECASE,
    ),
)

#: Everything `logging.LogRecord` sets by itself. What a call site added with
#: `extra=` is whatever is left, and that is what becomes a JSON field.
_BUILT_IN: Final[frozenset[str]] = frozenset(
    set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}
)


def redact(text: str) -> str:
    """Mask anything shaped like a credential, leaving the rest readable."""
    for pattern in _SECRETS:
        text = pattern.sub(
            lambda match: f"{match.group('keep')}{MASK}{match.groupdict().get('tail') or ''}", text
        )
    return text


def summarise(value: object, *, limit: int = 120) -> str:
    """Render one value as a log field: redacted, and never a whole document.

    A tool argument may be the text of a runbook. Printing it turns one line
    into fifty pages; dropping it leaves a trace that says nothing. The length
    is kept, because "the agent sent 40 000 characters" is often the answer.
    """
    text = value if isinstance(value, str) else repr(value)
    if len(text) > limit:
        text = f"{text[:limit]}... ({len(text)} chars)"
    return redact(text)


class RequestIdFilter(logging.Filter):
    """Put the request being served on every record, so a format can print it."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id.get()
        return True


class RedactingFilter(logging.Filter):
    """Mask credentials in the message before any formatter renders it.

    The message is rendered here and the arguments dropped, which is the only
    way to catch a secret that arrived as an argument rather than in the format
    string.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    """What a call site attached with `extra=`, and nothing the library added."""
    return {
        key: value
        for key, value in record.__dict__.items()
        if key not in _BUILT_IN and key != "request_id"
    }


class PlainFormatter(logging.Formatter):
    """A line for a human, with the `extra=` fields appended as `key=value`.

    Without this the fields would exist only in JSON, which is exactly
    backwards: the JSON goes to a collector that can be *queried* for them,
    while the terminal is where somebody is reading the line right now. A
    `duration_ms` visible only in production is a duration nobody ever sees.
    """

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        fields = " ".join(f"{key}={value}" for key, value in _extras(record).items())
        # Redacted here and not only in `RedactingFilter`, which sees the
        # message alone: a DSN handed over as `extra={"dsn": ...}` reaches the
        # stream through this line and through no other.
        return redact(f"{line} — {fields}") if fields else line


class JsonFormatter(logging.Formatter):
    """One JSON object per line, keeping `extra=` fields as fields.

    A collector can then be asked "every request that answered 500" instead of
    being handed prose to match with a regular expression.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", NO_REQUEST),
            "message": record.getMessage(),
        }
        payload.update(_extras(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False))


#: Read by a human, in a terminal where `make run` interleaves two servers.
PLAIN_FORMAT: Final = "%(asctime)s %(levelname)-8s [%(request_id)s] %(name)s: %(message)s"


def _level(*, opened: bool, when_open: str = "DEBUG", when_shut: str = "WARNING") -> str:
    return when_open if opened else when_shut


def logging_config(settings: Settings) -> dict[str, Any]:
    """The `dictConfig` this process would apply. Pure — nothing is configured."""
    return {
        "version": 1,
        # uvicorn has already configured its own loggers by the time this is
        # applied; disabling them would silence the server's startup lines.
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": RequestIdFilter},
            "redact": {"()": RedactingFilter},
        },
        "formatters": {
            "plain": {"()": PlainFormatter, "format": PLAIN_FORMAT},
            "json": {"()": JsonFormatter},
        },
        "handlers": {
            # No `level`: what is printed is decided per logger, and a level
            # here would quietly override every one of them.
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "json" if settings.json_logs else "plain",
                "filters": ["request_id", "redact"],
            }
        },
        "root": {"handlers": ["console"], "level": settings.log_level},
        "loggers": {
            "ea": {"level": settings.log_level, "handlers": ["console"], "propagate": False},
            REQUESTS_LOGGER: {"level": _level(opened=settings.log_requests, when_open="INFO")},
            CYPHER_LOGGER: {"level": _level(opened=settings.log_cypher)},
            EMBEDDINGS_LOGGER: {"level": _level(opened=settings.log_embeddings)},
            "neo4j": {"level": _level(opened=settings.log_cypher)},
            "httpx": {"level": _level(opened=settings.log_embeddings)},
            "httpcore": {"level": _level(opened=settings.log_embeddings)},
            SQL_LOGGER: {"level": _level(opened=settings.log_sql, when_open="INFO")},
            "uvicorn": {"handlers": ["console"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"level": "INFO"},
            # Exactly one access line per request. Ours carries the id and the
            # duration; uvicorn's carries neither, so it stands down while ours
            # is on rather than printing the same request twice.
            "uvicorn.access": {
                "handlers": ["console"],
                "level": "WARNING" if settings.log_requests else "INFO",
                "propagate": False,
            },
        },
    }


def configure_logging(settings: Settings) -> None:
    """Apply the configuration above. Called once, by the process entry point."""
    logging.config.dictConfig(logging_config(settings))
