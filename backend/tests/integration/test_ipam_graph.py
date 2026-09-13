"""The IP addressing against a real Neo4j.

Two claims here are about the database and cannot be made with a double: that
`(p_vrf, p_ip_address)` really is a uniqueness constraint — the thing that
makes "this address is mine" true rather than likely when two callers allocate
at once — and that the three queries behind the inventory find what they say
they find. Everything else about the addressing is unit-tested.
"""

from __future__ import annotations

import asyncio
import re

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.errors import AddressAlreadyAssignedError
from ea.domain.ipam import ADDRESS_PROPERTY, VRF_PROPERTY
from ea.repositories.archimate_graph import Neo4jArchitectureRepository
from ea.services.architecture import ArchitectureService
from ea.services.ipam import IpamService

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.fixture
def graph_ipam(
    graph_service: ArchitectureService, graph_repository: Neo4jArchitectureRepository
) -> IpamService:
    """The IP use cases over the real graph — one class answers both ports."""
    return IpamService(graph_service, graph_repository)


class TestTheUniquenessConstraint:
    async def test_the_database_refuses_a_second_element_on_one_address(
        self, graph_service: ArchitectureService, graph_repository: Neo4jArchitectureRepository
    ) -> None:
        """Written straight through the repository, so no service check is in the way.

        This is the claim the whole one-address-per-element design rests on: the
        service checks first, but only the constraint survives two callers
        reading "free" in the same instant.
        """
        await graph_service.create_element(
            element_type=E.NODE,
            name="srv-app-01",
            properties={ADDRESS_PROPERTY: "10.0.1.12", VRF_PROPERTY: "default"},
        )

        with pytest.raises(AddressAlreadyAssignedError, match=re.escape("10.0.1.12")):
            await graph_service.create_element(
                element_type=E.NODE,
                name="srv-app-02",
                properties={ADDRESS_PROPERTY: "10.0.1.12", VRF_PROPERTY: "default"},
            )

    async def test_the_same_address_in_another_scope_is_allowed(
        self, graph_service: ArchitectureService
    ) -> None:
        """Overlapping ranges in separate VRFs are the point of having VRFs."""
        await graph_service.create_element(
            element_type=E.NODE,
            name="client-a-gw",
            properties={ADDRESS_PROPERTY: "10.0.1.1", VRF_PROPERTY: "a"},
        )

        second = await graph_service.create_element(
            element_type=E.NODE,
            name="client-b-gw",
            properties={ADDRESS_PROPERTY: "10.0.1.1", VRF_PROPERTY: "b"},
        )

        assert second.properties[VRF_PROPERTY] == "b"

    async def test_a_taken_address_is_refused_by_name_rather_than_as_a_name_clash(
        self, graph_ipam: IpamService, graph_service: ArchitectureService
    ) -> None:
        """Two constraints can refuse an element; the caller is told which one.

        An agent handed "already named" after losing an allocation race would
        rename the host and try again, forever.
        """
        await graph_ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        first = await graph_service.create_element(element_type=E.NODE, name="srv-app-01")
        second = await graph_service.create_element(element_type=E.NODE, name="srv-app-02")
        await graph_ipam.assign_address(first.id, "10.0.1.12")

        with pytest.raises(AddressAlreadyAssignedError):
            await graph_ipam.assign_address(second.id, "10.0.1.12")


class TestTheQueriesBehindTheInventory:
    async def test_an_address_is_found_by_its_exact_value_and_scope(
        self, graph_ipam: IpamService, graph_service: ArchitectureService
    ) -> None:
        """`10.0.1.1` must not be found by looking for `10.0.1.1` inside `10.0.1.10`."""
        await graph_ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        for name, address in (("srv-app-01", "10.0.1.1"), ("srv-app-10", "10.0.1.10")):
            host = await graph_service.create_element(element_type=E.NODE, name=name)
            await graph_ipam.assign_address(host.id, address)

        found = await graph_ipam.locate("10.0.1.1")

        assert found.assignment.element.name == "srv-app-01"

    async def test_a_subnet_declared_in_the_graph_is_read_back_with_its_occupancy(
        self, graph_ipam: IpamService, graph_service: ArchitectureService
    ) -> None:
        subnet = await graph_ipam.declare_network(
            name="DMZ", cidr="10.0.1.0/24", reserved="10.0.1.1"
        )
        host = await graph_service.create_element(element_type=E.NODE, name="srv-app-01")
        assigned = await graph_ipam.allocate_next(subnet.element.id, host.id)

        detail = await graph_ipam.read_network(subnet.element.id)

        assert str(assigned.address) == "10.0.1.2"
        assert detail.subnet.used == 1
        assert [entry.element.name for entry in detail.addresses] == ["srv-app-01"]

    async def test_deleting_the_element_frees_its_address(
        self, graph_ipam: IpamService, graph_service: ArchitectureService
    ) -> None:
        """No cascade to write: the address was an attribute of the element."""
        await graph_ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await graph_service.create_element(element_type=E.NODE, name="srv-app-01")
        await graph_ipam.assign_address(host.id, "10.0.1.12")

        await graph_service.delete_element(host.id)

        assert await graph_ipam.list_addresses() == ()

    async def test_an_ipv6_prefix_and_address_survive_a_round_trip(
        self, graph_ipam: IpamService, graph_service: ArchitectureService
    ) -> None:
        await graph_ipam.declare_network(name="v6", cidr="2001:db8::/64")
        host = await graph_service.create_element(element_type=E.NODE, name="srv-app-01")

        assigned = await graph_ipam.assign_address(host.id, "2001:0db8:0000::1")

        assert str(assigned.address) == "2001:db8::1"
        assert (await graph_ipam.locate("2001:db8::1")).assignment.element.name == "srv-app-01"


class TestConcurrentAllocation:
    async def test_two_simultaneous_allocations_in_one_subnet_hand_out_two_addresses(
        self, graph_ipam: IpamService, graph_service: ArchitectureService
    ) -> None:
        """The race the uniqueness constraint exists for, run for real.

        Both calls read the subnet before either writes, so both compute the
        same "next free" address; the constraint refuses the second write, and
        `allocate_next` must take that refusal as "look again" rather than
        report it. Two agents asking for an address at once is the normal case
        over MCP, and an allocation that fails half the time is one they learn
        to route around by writing addresses themselves — see docs/adr/0020.
        """
        subnet = await graph_ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        first = await graph_service.create_element(element_type=E.NODE, name="srv-app-01")
        second = await graph_service.create_element(element_type=E.NODE, name="srv-app-02")

        one, other = await asyncio.gather(
            graph_ipam.allocate_next(subnet.element.id, first.id),
            graph_ipam.allocate_next(subnet.element.id, second.id),
        )

        assert one.address != other.address
        detail = await graph_ipam.read_network(subnet.element.id)
        assert detail.subnet.used == 2
        assert {str(entry.address) for entry in detail.addresses} == {
            str(one.address),
            str(other.address),
        }
