"""Turning domain failures into the one error an agent is meant to read — and
leaving a trace of every call that was made.

`api/errors.py` maps a `DomainError` onto an HTTP status. There is no status
here; the distinction the protocol draws instead is *anticipated* against
*crash*. A `ToolError` comes back as `is_error=True` carrying our message, for
the model to read and correct itself, and is logged at INFO without a
traceback. Anything else reaches the model as `Error executing tool <name>` and
nothing more, with the traceback going to the log.

That is the same rule as the HTTP adapter, stated in the other protocol's
terms: a `DomainError`'s message is written for a human and names no internals,
so it is safe to hand over; everything else stays in the logs.

The tracing lives here, in the decorator every tool already carries, rather
than in a third one to remember: `/mcp` writes to the graph (docs/adr/0014),
so a call that leaves no trace is a write nobody can account for. One INFO line
per call says which tool, what came of it and how long it took; the arguments
are one DEBUG line above it, summarised, because an agent attaching a runbook
sends forty thousand characters and a log is not the place to keep a second
copy of them.

The caller lives here too. The transport authenticated the request that carried
the call, and the SDK hands its token to the handler (`get_access_token`); the
tool runs as that token's caller, so the services decide on it exactly as they
do behind the REST dependency (docs/adr/0032).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager, nullcontext
from functools import wraps
from time import perf_counter
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError

from ea.api.errors import safe_failure
from ea.core.logging import TOOLS_LOGGER, summarise
from ea.domain.audit import AuditChannel
from ea.domain.errors import DomainError
from ea.mcp.auth import caller_of
from ea.services.caller import acting_as, acting_through

logger = logging.getLogger(TOOLS_LOGGER)

#: Arguments that are the text of a file: logged by their length, never by their first characters.
FILE_TEXTS = frozenset({"content", "elements", "relations", "properties"})
#: Tools whose failures carry a file's values.
IMPORTING_TOOLS = frozenset({"import_model"})


def speaking_plainly[**P, T](tool: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Re-raise the failures the caller can act on as `ToolError`, and trace the call.

    `ValueError` is included for the same reason `api/errors.py` answers it
    with a 422: the domain raises it for a rejected input — a blank name, a
    property key that is not an identifier — which is the caller's mistake and
    not a fault of the server. An agent that reads "an element name cannot be
    blank" retries; one that reads "Error executing tool create_element" gives
    up or, worse, invents a reason.

    `functools.wraps` is load-bearing, not decoration: the SDK builds a tool's
    JSON schema from the wrapped function's signature and its docstring, and
    reaches them through `__wrapped__`.
    """

    @wraps(tool)
    async def plainly(*args: P.args, **kwargs: P.kwargs) -> T:
        name = tool.__name__
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "tool %s called",
                name,
                extra={
                    "tool": name,
                    "arguments": {
                        k: f"<{len(v)} chars>"
                        if k in FILE_TEXTS and isinstance(v, str)
                        else summarise(v)
                        for k, v in kwargs.items()
                    },
                },
            )
        started = perf_counter()

        def took(outcome: str) -> dict[str, Any]:
            return {
                "tool": name,
                "outcome": outcome,
                "duration_ms": round((perf_counter() - started) * 1000, 1),
            }

        # Without a token (auth off, or a test calling the server directly)
        # whoever the context already acts as stays.
        token = get_access_token()
        caller: AbstractContextManager[object] = (
            nullcontext() if token is None else acting_as(caller_of(token))
        )
        try:
            with caller, acting_through(AuditChannel.MCP):
                answer = await tool(*args, **kwargs)
        except (DomainError, ValueError) as failure:
            # Anticipated: the agent is being told something it can act on, so
            # it is INFO and it carries no traceback.
            logger.info("tool %s refused: %s", name, failure, extra=took("refused"))
            raise ToolError(str(failure)) from failure
        except Exception as failure:
            # What is logged of an unexpected failure is its structure: a driver's message quotes
            # the values it refused, and an import's values are a file's (api/errors.py).
            info, said = safe_failure(failure, importing=name in IMPORTING_TOOLS)
            logger.exception(
                "tool %s failed", name, exc_info=info, extra={**took("failed"), **said}
            )
            raise
        logger.info("tool %s answered", name, extra=took("ok"))
        return answer

    return plainly
