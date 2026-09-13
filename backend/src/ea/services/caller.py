"""The caller of the use case being run, and the two checks every use case makes.

A `ContextVar`, like the request id of docs/adr/0021: the adapter that knows who
is calling (the REST dependency, the MCP decorator, `ea.reindex`) sets it, and
the service reads it. Nobody set it means nobody is calling — refused, so a
forgotten wire-up is a 401 in a test rather than an open door. See docs/adr/0032.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from ea.domain.auth import EDITOR_ROLE, Caller
from ea.domain.errors import NotAuthenticatedError, NotAuthorisedError

current_caller: ContextVar[Caller | None] = ContextVar("ea_current_caller", default=None)


def require_caller() -> Caller:
    caller = current_caller.get()
    if caller is None:
        msg = "authentication is required"
        raise NotAuthenticatedError(msg)
    return caller


def require_editor() -> Caller:
    caller = require_caller()
    if not caller.can_write:
        msg = f"changing the catalogue needs the {EDITOR_ROLE} role"
        raise NotAuthorisedError(msg)
    return caller


@contextmanager
def acting_as(caller: Caller) -> Iterator[Caller]:
    token = current_caller.set(caller)
    try:
        yield caller
    finally:
        current_caller.reset(token)
