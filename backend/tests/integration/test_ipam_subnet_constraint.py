"""A subnet may be declared once per routing scope — and the database says so.

`IpamService.declare_network` looks for the prefix before creating it, which
gives a good message and loses a race: two callers can both look, both find
nothing, and both create. `(vrf, cidr)` is a uniqueness constraint for the
same reason `(vrf, ip_address)` is one (`db/models/architecture.py`), and these tests
prove the constraint rather than the check, so most of them write through the
catalogue where no check stands in the way.
"""

from __future__ import annotations

import asyncio
import re

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from ea.domain.archimate import ElementType as E
from ea.domain.errors import DuplicateNetworkError
from ea.domain.ipam import PREFIX_PROPERTY, VRF_PROPERTY
from ea.repositories.architecture_store import PostgresArchitectureRepository
from ea.services.architecture import ArchitectureService
from ea.services.ipam import IpamService

pytestmark = [pytest.mark.integration, pytest.mark.postgres, pytest.mark.asyncio]


@pytest.fixture
def graph_ipam(
    graph_service: ArchitectureService, graph_repository: PostgresArchitectureRepository
) -> IpamService:
    return IpamService(graph_service, graph_repository)


class TestTheSubnetConstraint:
    async def test_it_is_declared_on_the_database(self, engine_at_head: AsyncEngine) -> None:
        async with engine_at_head.connect() as connection:
            definition = await connection.scalar(
                # A catalogue read with no runtime value: nothing to bind.
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'uq_elements_vrf_cidr'")
            )

        assert definition is not None
        assert "UNIQUE" in definition
        assert "'vrf'" in definition and "'cidr'" in definition

    async def test_the_database_refuses_a_second_network_on_one_prefix(
        self, graph_service: ArchitectureService
    ) -> None:
        await graph_service.create_element(
            element_type=E.COMMUNICATION_NETWORK,
            name="DMZ",
            properties={PREFIX_PROPERTY: "10.0.1.0/24", VRF_PROPERTY: "dmz"},
        )

        with pytest.raises(DuplicateNetworkError, match=re.escape("10.0.1.0/24")):
            await graph_service.create_element(
                element_type=E.COMMUNICATION_NETWORK,
                name="DMZ bis",
                properties={PREFIX_PROPERTY: "10.0.1.0/24", VRF_PROPERTY: "dmz"},
            )

    async def test_another_spelling_of_the_same_prefix_is_refused_too(
        self, graph_service: ArchitectureService
    ) -> None:
        """The constraint compares strings, so the catalogue stores one spelling.

        Without that, `10.0.1.0/255.255.255.0` would be a second value for the
        same network and walk straight past the constraint.
        """
        await graph_service.create_element(
            element_type=E.COMMUNICATION_NETWORK,
            name="DMZ",
            properties={PREFIX_PROPERTY: "10.0.1.0/255.255.255.0"},
        )

        with pytest.raises(DuplicateNetworkError, match=re.escape("10.0.1.0/24")):
            await graph_service.create_element(
                element_type=E.COMMUNICATION_NETWORK,
                name="DMZ bis",
                properties={PREFIX_PROPERTY: "10.0.1.0/24"},
            )

    async def test_the_same_prefix_in_another_scope_is_another_subnet(
        self, graph_service: ArchitectureService
    ) -> None:
        await graph_service.create_element(
            element_type=E.COMMUNICATION_NETWORK,
            name="Client A",
            properties={PREFIX_PROPERTY: "10.0.1.0/24", VRF_PROPERTY: "a"},
        )

        second = await graph_service.create_element(
            element_type=E.COMMUNICATION_NETWORK,
            name="Client B",
            properties={PREFIX_PROPERTY: "10.0.1.0/24", VRF_PROPERTY: "b"},
        )

        assert second.properties[VRF_PROPERTY] == "b"

    async def test_two_declarations_racing_for_one_prefix_leave_one_subnet(
        self, graph_ipam: IpamService
    ) -> None:
        """Both pass the service's look-before-create; only one may land."""
        outcomes = await asyncio.gather(
            graph_ipam.declare_network(name="DMZ", cidr="10.0.1.0/24"),
            graph_ipam.declare_network(name="DMZ bis", cidr="10.0.1.0/24"),
            return_exceptions=True,
        )

        refused = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
        assert len(refused) == 1
        assert isinstance(refused[0], DuplicateNetworkError)
        assert len(await graph_ipam.list_networks()) == 1
