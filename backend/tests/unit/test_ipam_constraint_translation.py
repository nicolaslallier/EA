"""Which uniqueness constraint refused a write, told apart without a database.

PostgreSQL names the constraint it enforced; the constraints themselves are
proved against a real server in `tests/integration/test_ipam_subnet_constraint.py`.
What is checked here is the translation — a lost subnet declaration is not a
name clash, and an agent told "already named" would rename and retry forever.
"""

from __future__ import annotations

import re

from sqlalchemy.exc import IntegrityError

from ea.domain.archimate import ElementType as E
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    DuplicateElementError,
    DuplicateNetworkError,
)
from ea.domain.model import Element
from ea.repositories.architecture_store import (
    ADDRESS_TAKEN,
    NAME_TAKEN,
    PREFIX_TAKEN,
    constraint_of,
    rejected,
)
from tests.conftest import FIXED_NOW


def _network() -> Element:
    return Element.create(
        element_type=E.COMMUNICATION_NETWORK,
        name="DMZ",
        properties={"cidr": "10.0.1.0/24", "vrf": "dmz"},
        now=FIXED_NOW,
    )


def _host() -> Element:
    return Element.create(
        element_type=E.NODE,
        name="srv-01",
        properties={"ip_address": "10.0.1.12", "vrf": "dmz"},
        now=FIXED_NOW,
    )


def test_a_taken_prefix_is_a_duplicate_subnet() -> None:
    refusal = rejected(_network(), PREFIX_TAKEN)

    assert isinstance(refusal, DuplicateNetworkError)
    assert re.search(re.escape("10.0.1.0/24") + ".*'dmz'", str(refusal))


def test_a_taken_address_is_an_address_already_assigned() -> None:
    refusal = rejected(_host(), ADDRESS_TAKEN)

    assert isinstance(refusal, AddressAlreadyAssignedError)
    assert "10.0.1.12" in str(refusal)


def test_a_taken_name_is_still_a_name_clash() -> None:
    refusal = rejected(_network(), NAME_TAKEN)

    assert isinstance(refusal, DuplicateElementError)
    assert "already named 'DMZ'" in str(refusal)


def test_an_unknown_constraint_is_not_dressed_up_as_a_duplicate() -> None:
    """Neo4j's fallback was "already named"; an unrecognised refusal now stays itself."""
    assert rejected(_network(), "some_other_constraint") is None
    assert rejected(_network(), None) is None


def test_the_constraint_name_is_read_from_the_driver_exception_under_the_adapter() -> None:
    # `__cause__` must be a real exception (Python refuses anything else), so the
    # fake asyncpg error is one too, exactly as the real driver's is.
    asyncpg_error = Exception("duplicate key value violates unique constraint")
    asyncpg_error.constraint_name = PREFIX_TAKEN  # type: ignore[attr-defined]
    adapter = Exception("duplicate key")
    adapter.__cause__ = asyncpg_error

    assert constraint_of(IntegrityError("INSERT", {}, adapter)) == PREFIX_TAKEN


def test_an_exception_without_a_constraint_name_gives_none() -> None:
    assert constraint_of(IntegrityError("INSERT", {}, Exception("boom"))) is None
