"""Every domain refusal has an HTTP status — otherwise it leaves as a bare 400."""

from __future__ import annotations

import pytest

from ea.api.errors import _STATUS
from ea.domain.errors import DomainError


def all_domain_errors() -> list[type[DomainError]]:
    found: list[type[DomainError]] = []
    frontier = list(DomainError.__subclasses__())
    while frontier:
        error = frontier.pop()
        found.append(error)
        frontier.extend(error.__subclasses__())
    return found


@pytest.mark.parametrize("error", all_domain_errors(), ids=lambda e: e.__name__)
def test_every_domain_error_has_a_row_in_the_status_table(error: type[DomainError]) -> None:
    assert error in _STATUS, f"{error.__name__} would be answered 400 invalid_request"
