"""Turning domain failures into the one error an agent is meant to read.

`api/errors.py` maps a `DomainError` onto an HTTP status. There is no status
here; the distinction the protocol draws instead is *anticipated* against
*crash*. A `ToolError` comes back as `is_error=True` carrying our message, for
the model to read and correct itself, and is logged at INFO without a
traceback. Anything else reaches the model as `Error executing tool <name>` and
nothing more, with the traceback going to the log.

That is the same rule as the HTTP adapter, stated in the other protocol's
terms: a `DomainError`'s message is written for a human and names no internals,
so it is safe to hand over; everything else stays in the logs.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps

from mcp.server.mcpserver.exceptions import ToolError

from ea.domain.errors import DomainError


def speaking_plainly[**P, T](tool: Callable[P, Awaitable[T]]) -> Callable[P, Awaitable[T]]:
    """Re-raise the failures the caller can act on as `ToolError`.

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
        try:
            return await tool(*args, **kwargs)
        except (DomainError, ValueError) as failure:
            raise ToolError(str(failure)) from failure

    return plainly
