"""Which uniqueness constraint refused a write, told apart without a database.

The constraints themselves are proved against a real Neo4j in
`tests/integration/test_ipam_subnet_constraint.py`. What is checked here is the
translation: the server names the properties it found taken, and a caller must
be handed the refusal that matches — a lost subnet declaration is not a name
clash, and an agent told "already named" would rename and retry forever.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from neo4j.exceptions import ConstraintError

from ea.db.schema import SCHEMA_STATEMENTS
from ea.domain.archimate import ElementType as E
from ea.domain.errors import DuplicateElementError, DuplicateNetworkError
from ea.domain.model import Element
from ea.repositories.archimate_graph import Neo4jArchitectureRepository
from tests.conftest import FIXED_NOW

#: The server's own wording for a composite uniqueness violation, as Neo4j 5
#: writes it: the label, then every property of the constraint with its value.
TAKEN_PREFIX = (
    "Node(5) already exists with label `Element` and properties "
    "`p_vrf` = 'dmz', `p_cidr` = '10.0.1.0/24'"
)
TAKEN_NAME = (
    "Node(5) already exists with label `Element` and properties "
    "`element_type` = 'communication_network', `name` = 'DMZ'"
)


class _RefusingDriver:
    """A driver whose every statement is refused by the same constraint."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def execute_query(self, *_args: Any, **_kwargs: Any) -> Any:
        raise ConstraintError(self._message)


def _network() -> Element:
    return Element.create(
        element_type=E.COMMUNICATION_NETWORK,
        name="DMZ",
        properties={"cidr": "10.0.1.0/24", "vrf": "dmz"},
        now=FIXED_NOW,
    )


def _repository(message: str) -> Neo4jArchitectureRepository:
    return Neo4jArchitectureRepository(_RefusingDriver(message), database="neo4j")  # type: ignore[arg-type]


def test_the_schema_declares_a_prefix_once_per_scope() -> None:
    assert any(
        "REQUIRE (e.p_vrf, e.p_cidr) IS UNIQUE" in statement for statement in SCHEMA_STATEMENTS
    )


@pytest.mark.asyncio
class TestARefusedSubnet:
    async def test_a_taken_prefix_is_a_duplicate_subnet_on_creation(self) -> None:
        with pytest.raises(DuplicateNetworkError, match=re.escape("10.0.1.0/24") + ".*'dmz'"):
            await _repository(TAKEN_PREFIX).add_element(_network())

    async def test_a_taken_prefix_is_a_duplicate_subnet_on_update(self) -> None:
        with pytest.raises(DuplicateNetworkError, match=re.escape("10.0.1.0/24")):
            await _repository(TAKEN_PREFIX).save_element(_network())

    async def test_a_network_whose_name_is_taken_is_still_a_name_clash(self) -> None:
        with pytest.raises(DuplicateElementError, match="already named 'DMZ'"):
            await _repository(TAKEN_NAME).add_element(_network())
