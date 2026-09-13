"""Who is calling, and what the services refuse when nobody is."""

from __future__ import annotations

import pytest

from ea.domain.auth import EDITOR_ROLE, LOCAL_DEVELOPER, SYSTEM, Caller
from ea.domain.errors import DomainError, NotAuthenticatedError, NotAuthorisedError
from ea.services.caller import acting_as, current_caller, require_caller, require_editor


def test_a_caller_with_the_editor_role_can_write() -> None:
    assert Caller("s", "alice", frozenset({EDITOR_ROLE})).can_write
    assert not Caller("s", "bob", frozenset({"offline_access"})).can_write


def test_the_built_in_callers_are_editors() -> None:
    assert LOCAL_DEVELOPER.can_write and SYSTEM.can_write


def test_both_refusals_are_domain_errors_so_both_adapters_translate_them() -> None:
    assert issubclass(NotAuthenticatedError, DomainError)
    assert issubclass(NotAuthorisedError, DomainError)


def test_nobody_calling_is_refused() -> None:
    token = current_caller.set(None)
    try:
        with pytest.raises(NotAuthenticatedError):
            require_caller()
        with pytest.raises(NotAuthenticatedError):
            require_editor()
    finally:
        current_caller.reset(token)


def test_a_reader_may_read_but_not_write() -> None:
    reader = Caller("s", "bob")
    with acting_as(reader):
        assert require_caller() is reader
        with pytest.raises(NotAuthorisedError, match=EDITOR_ROLE):
            require_editor()


def test_acting_as_puts_the_previous_caller_back() -> None:
    before = current_caller.get()
    with acting_as(SYSTEM):
        assert current_caller.get() is SYSTEM
    assert current_caller.get() is before
