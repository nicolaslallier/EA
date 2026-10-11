"""A deadlock or a serialization failure is a replayable conflict, in one place.

PostgreSQL aborts one of two transactions that wait for each other (40P01) or that
cannot be serialised (40001), and asks the client to run it again. The writers that
lock several rows are wrapped here, so every one of them answers a typed 409
(`WriteConflictError`) instead of an unexplained 500 (docs/adr/0048).
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from sqlalchemy.exc import DBAPIError

from ea.domain.errors import WriteConflictError

logger = logging.getLogger(__name__)


def is_serialization_failure(error: DBAPIError) -> bool:
    """SQLSTATE 40001 (and 40P01, a deadlock) — carried by the driver's own exception."""
    cause = getattr(error.orig, "__cause__", None)
    code = getattr(cause, "sqlstate", None) or getattr(error.orig, "sqlstate", None)
    return code in ("40001", "40P01")


def replayable[**Params, R](
    write: Callable[Params, Coroutine[Any, Any, R]],
) -> Callable[Params, Coroutine[Any, Any, R]]:
    """Turn the database's "run it again" into `WriteConflictError`; anything else passes."""

    @functools.wraps(write)
    async def wrapped(*args: Params.args, **kwargs: Params.kwargs) -> R:
        try:
            return await write(*args, **kwargs)
        except DBAPIError as error:
            if not is_serialization_failure(error):
                raise
            logger.warning(
                "write conflict, to be replayed",
                extra={"action": "write_conflict", "writer": write.__qualname__},
            )
            msg = "another change got in the way of this one: read the element again and retry"
            raise WriteConflictError(msg) from error

    wrapped.__replayable__ = True  # type: ignore[attr-defined]
    return wrapped
