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
than in a third one to remember: `/mcp` writes to the graph and, until auth
exists, authenticates nobody (docs/adr/0014), so a call that leaves no trace is
a write nobody can account for. One INFO line per call says which tool, what
came of it and how long it took; the arguments are one DEBUG line above it,
summarised, because an agent attaching a runbook sends forty thousand
characters and a log is not the place to keep a second copy of them.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from time import perf_counter
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from ea.core.logging import TOOLS_LOGGER, summarise
from ea.domain.errors import DomainError

logger = logging.getLogger(TOOLS_LOGGER)


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
                extra={"tool": name, "arguments": {k: summarise(v) for k, v in kwargs.items()}},
            )
        started = perf_counter()

        def took(outcome: str) -> dict[str, Any]:
            return {
                "tool": name,
                "outcome": outcome,
                "duration_ms": round((perf_counter() - started) * 1000, 1),
            }

        try:
            answer = await tool(*args, **kwargs)
        except (DomainError, ValueError) as failure:
            # Anticipated: the agent is being told something it can act on, so
            # it is INFO and it carries no traceback.
            logger.info("tool %s refused: %s", name, failure, extra=took("refused"))
            raise ToolError(str(failure)) from failure
        except Exception:
            logger.exception("tool %s failed", name, extra=took("failed"))
            raise
        logger.info("tool %s answered", name, extra=took("ok"))
        return answer

    return plainly
