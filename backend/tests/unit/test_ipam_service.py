"""The IPAM use cases, over the same in-memory graph the other services use.

These are the rules that need more than one address to check: is this one
already taken, does a declared subnet hold it, may this *kind* of element carry
one at all. The arithmetic they rest on is `test_ipam.py`; the Cypher that
finds an element by its address is `tests/integration/test_ipam_graph.py`.
"""

from __future__ import annotations

import re
from dataclasses import replace
from uuid import UUID

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.archimate import RelationshipType as R
from ea.domain.errors import (
    AddressAlreadyAssignedError,
    AddressNotAssignedError,
    AddressOutsideAnyNetworkError,
    DuplicateNetworkError,
    ElementNotFoundError,
    NetworkExhaustedError,
    NotAddressableError,
    NotASubnetError,
)
from ea.domain.ipam import ADDRESS_PROPERTY, DEFAULT_VRF
from ea.domain.model import Element
from ea.services.architecture import ArchitectureService
from ea.services.ipam import IpamService
from tests.conftest import FIXED_NOW, InMemoryRepository


@pytest.mark.asyncio
class TestDeclaringASubnet:
    async def test_a_subnet_is_a_communication_network_carrying_its_prefix(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        subnet = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24", vrf="dmz")

        stored = await service.get_element(subnet.element.id)
        assert stored.element_type is E.COMMUNICATION_NETWORK
        assert stored.properties["cidr"] == "10.0.1.0/24"
        assert stored.properties["vrf"] == "dmz"

    async def test_the_same_prefix_cannot_be_declared_twice_in_one_scope(
        self, ipam: IpamService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")

        with pytest.raises(DuplicateNetworkError, match=re.escape("10.0.1.0/24")):
            await ipam.declare_network(name="DMZ bis", cidr="10.0.1.0/24")

    async def test_the_same_prefix_in_another_vrf_is_another_subnet(
        self, ipam: IpamService
    ) -> None:
        """Overlapping ranges in separate VRFs are the point of having VRFs."""
        await ipam.declare_network(name="Client A", cidr="10.0.1.0/24", vrf="a")

        second = await ipam.declare_network(name="Client B", cidr="10.0.1.0/24", vrf="b")

        assert second.network.vrf == "b"

    async def test_a_nested_prefix_is_allowed_and_wins_the_longest_match(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="Corporate", cidr="10.0.0.0/8")
        precise = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")

        assigned = await ipam.assign_address(host.id, "10.0.1.12")

        assert assigned.subnet_id == precise.element.id

    async def test_a_prefix_written_with_host_bits_is_refused(self, ipam: IpamService) -> None:
        with pytest.raises(ValueError, match=re.escape("10.0.1.0/24")):
            await ipam.declare_network(name="DMZ", cidr="10.0.1.5/24")

    async def test_a_subnet_reports_what_it_can_hand_out(self, ipam: IpamService) -> None:
        subnet = await ipam.declare_network(
            name="DMZ", cidr="10.0.1.0/24", reserved="10.0.1.1, 10.0.1.200-10.0.1.254"
        )

        assert subnet.network.capacity == 254
        assert subnet.network.reserved_count == 56
        assert subnet.used == 0


@pytest.mark.asyncio
class TestAssigningAnAddress:
    async def test_the_address_lands_on_the_element_as_a_property(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")

        await ipam.assign_address(host.id, "10.0.1.12")

        stored = await service.get_element(host.id)
        assert stored.properties[ADDRESS_PROPERTY] == "10.0.1.12"
        assert stored.properties["vrf"] == DEFAULT_VRF

    async def test_an_element_that_cannot_be_pinged_cannot_hold_an_address(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Facturer")

        with pytest.raises(NotAddressableError, match="business_process"):
            await ipam.assign_address(process.id, "10.0.1.12")

    async def test_an_address_no_declared_subnet_holds_is_refused(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        """An address outside every declared prefix is a typo or a missing subnet.

        Storing it would grow an inventory nobody can reconcile, so the refusal
        names the scope it looked in.
        """
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")

        with pytest.raises(AddressOutsideAnyNetworkError, match=re.escape("192.168.4.1")):
            await ipam.assign_address(host.id, "192.168.4.1")

    async def test_an_address_another_element_already_holds_is_refused(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        first = await service.create_element(element_type=E.NODE, name="srv-app-01")
        second = await service.create_element(element_type=E.NODE, name="srv-app-02")
        await ipam.assign_address(first.id, "10.0.1.12")

        with pytest.raises(AddressAlreadyAssignedError, match="srv-app-01"):
            await ipam.assign_address(second.id, "10.0.1.12")

    async def test_re_assigning_the_same_address_to_the_same_element_is_not_a_clash(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")
        await ipam.assign_address(host.id, "10.0.1.12")

        again = await ipam.assign_address(host.id, "10.0.1.12")

        assert str(again.address) == "10.0.1.12"

    async def test_the_addresses_a_prefix_keeps_for_itself_are_refused(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")

        with pytest.raises(ValueError, match=r"broadcast|network address"):
            await ipam.assign_address(host.id, "10.0.1.255")

    async def test_a_reserved_address_is_refused_and_says_so(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24", reserved="10.0.1.1")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")

        with pytest.raises(ValueError, match="reserved"):
            await ipam.assign_address(host.id, "10.0.1.1")

    async def test_assigning_to_an_unknown_element_says_which_one(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        from uuid import uuid4

        missing = uuid4()
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")

        with pytest.raises(ElementNotFoundError, match=str(missing)):
            await ipam.assign_address(missing, "10.0.1.12")

    async def test_an_element_keeps_the_attributes_the_assignment_did_not_touch(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(
            element_type=E.NODE, name="srv-app-01", properties={"owner": "infra"}
        )

        await ipam.assign_address(host.id, "10.0.1.12")

        stored = await service.get_element(host.id)
        assert stored.properties["owner"] == "infra"


@pytest.mark.asyncio
class TestAllocatingTheNextFreeAddress:
    async def test_it_hands_out_the_first_address_nothing_holds(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        subnet = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24", reserved="10.0.1.1")
        first = await service.create_element(element_type=E.NODE, name="srv-app-01")
        second = await service.create_element(element_type=E.NODE, name="srv-app-02")

        one = await ipam.allocate_next(subnet.element.id, first.id)
        two = await ipam.allocate_next(subnet.element.id, second.id)

        assert (str(one.address), str(two.address)) == ("10.0.1.2", "10.0.1.3")

    async def test_a_full_subnet_refuses_rather_than_inventing_an_address(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        subnet = await ipam.declare_network(name="Lien", cidr="10.0.1.0/30")
        for name in ("a", "b"):
            host = await service.create_element(element_type=E.NODE, name=name)
            await ipam.allocate_next(subnet.element.id, host.id)
        latecomer = await service.create_element(element_type=E.NODE, name="c")

        with pytest.raises(NetworkExhaustedError, match=re.escape("10.0.1.0/30")):
            await ipam.allocate_next(subnet.element.id, latecomer.id)

    async def test_allocating_from_an_element_that_declares_no_prefix_is_refused(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        not_a_subnet = await service.create_element(
            element_type=E.COMMUNICATION_NETWORK, name="Liaison radio"
        )
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")

        with pytest.raises(NotASubnetError, match="Liaison radio"):
            await ipam.allocate_next(not_a_subnet.id, host.id)


@pytest.mark.asyncio
class TestReleasingAnAddress:
    async def test_the_address_goes_and_the_element_stays(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")
        await ipam.assign_address(host.id, "10.0.1.12")

        await ipam.release_address(host.id)

        stored = await service.get_element(host.id)
        assert ADDRESS_PROPERTY not in stored.properties

    async def test_releasing_an_element_that_holds_none_says_so(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")

        with pytest.raises(AddressNotAssignedError, match="srv-app-01"):
            await ipam.release_address(host.id)


@pytest.mark.asyncio
class TestFindingWhatAnAddressIs:
    async def test_it_answers_with_the_element_and_what_it_is_wired_to(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        """The question the whole feature exists for: "10.0.1.12, that is what?"

        The answer is the element *and* its links, so one call is enough to say
        "10.0.1.12 is srv-app-01, a node, and Billing runs on it".
        """
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")
        billing = await service.create_element(element_type=E.APPLICATION_COMPONENT, name="Billing")
        await ipam.assign_address(host.id, "10.0.1.12")
        await service.connect(relationship_type=R.SERVING, source_id=host.id, target_id=billing.id)

        found = await ipam.locate("10.0.1.12")

        assert found.assignment.element.name == "srv-app-01"
        assert {element.name for element in found.graph.elements} == {"srv-app-01", "Billing"}

    async def test_an_address_nothing_holds_is_a_not_found(self, ipam: IpamService) -> None:
        with pytest.raises(AddressNotAssignedError, match=re.escape("10.0.1.12")):
            await ipam.locate("10.0.1.12")


@pytest.mark.asyncio
class TestTheInventory:
    async def test_it_lists_every_address_with_the_subnet_it_falls_in(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        subnet = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        for name in ("srv-app-01", "srv-app-02"):
            host = await service.create_element(element_type=E.NODE, name=name)
            await ipam.allocate_next(subnet.element.id, host.id)

        inventory = await ipam.list_addresses()

        assert [str(entry.address) for entry in inventory] == ["10.0.1.1", "10.0.1.2"]
        assert {entry.subnet_id for entry in inventory} == {subnet.element.id}

    async def test_it_can_be_narrowed_to_one_subnet(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        dmz = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        lan = await ipam.declare_network(name="LAN", cidr="10.0.2.0/24")
        for name, subnet in (("srv-app-01", dmz), ("poste-01", lan)):
            host = await service.create_element(element_type=E.NODE, name=name)
            await ipam.allocate_next(subnet.element.id, host.id)

        inventory = await ipam.list_addresses(within="10.0.2.0/24")

        assert [entry.element.name for entry in inventory] == ["poste-01"]

    async def test_a_subnet_reports_how_full_it_is(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        subnet = await ipam.declare_network(name="Lien", cidr="10.0.1.0/30")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")
        await ipam.allocate_next(subnet.element.id, host.id)

        detail = await ipam.read_network(subnet.element.id)

        assert (detail.subnet.used, detail.subnet.network.capacity) == (1, 2)
        assert str(detail.next_free) == "10.0.1.2"
        assert [entry.element.name for entry in detail.addresses] == ["srv-app-01"]

    async def test_listing_subnets_can_be_narrowed_to_one_scope(self, ipam: IpamService) -> None:
        await ipam.declare_network(name="Client A", cidr="10.0.1.0/24", vrf="a")
        await ipam.declare_network(name="Client B", cidr="10.0.2.0/24", vrf="b")

        listed = await ipam.list_networks(vrf="b")

        assert [subnet.element.name for subnet in listed] == ["Client B"]


@pytest.mark.asyncio
class TestTheConventionIsEnforcedOnEveryWriteAndNotOnlyByThisService:
    """`update_element` takes free-form properties, so it is a second door.

    The IPAM lives in those properties; a rule only the IPAM service checked
    would be one `PATCH /elements/{id}` away from being ignored, and the
    inventory would quietly stop meaning anything. So the convention is
    validated wherever properties are written — see `docs/adr/0020`.
    """

    async def test_an_address_cannot_be_written_onto_a_business_process(
        self, service: ArchitectureService
    ) -> None:
        process = await service.create_element(element_type=E.BUSINESS_PROCESS, name="Facturer")

        with pytest.raises(NotAddressableError):
            await service.update_element(process.id, properties={"ip_address": "10.0.1.12"})

    async def test_a_malformed_address_is_refused_at_creation(
        self, service: ArchitectureService
    ) -> None:
        with pytest.raises(ValueError, match="not an IP address"):
            await service.create_element(
                element_type=E.NODE, name="srv-app-01", properties={"ip_address": "10.0.1.300"}
            )

    async def test_a_prefix_cannot_be_written_onto_anything_but_a_network(
        self, service: ArchitectureService
    ) -> None:
        with pytest.raises(NotASubnetError):
            await service.create_element(
                element_type=E.NODE, name="srv-app-01", properties={"cidr": "10.0.1.0/24"}
            )


@pytest.mark.asyncio
class TestOneBadRowDoesNotBreakTheInventory:
    """A property can be written by some other door, and one of them can be wrong.

    An inventory that raised on the first unreadable row would be unusable
    exactly when somebody needs it to find what is wrong — see `docs/adr/0020`.
    """

    async def test_an_unparseable_prefix_is_skipped_rather_than_raised_on(
        self, ipam: IpamService, repository: InMemoryRepository, service: ArchitectureService
    ) -> None:
        good = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        broken = await service.create_element(element_type=E.COMMUNICATION_NETWORK, name="Cassé")
        _store(repository, broken, {"cidr": "pas un préfixe"})

        assert [subnet.element.name for subnet in await ipam.list_networks()] == [good.element.name]

    async def test_an_unparseable_address_is_skipped_rather_than_raised_on(
        self, ipam: IpamService, repository: InMemoryRepository, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        good = await service.create_element(element_type=E.NODE, name="srv-app-01")
        await ipam.assign_address(good.id, "10.0.1.12")
        broken = await service.create_element(element_type=E.NODE, name="srv-app-02")
        _store(repository, broken, {"ip_address": "10.0.1.300"})

        assert [entry.element.name for entry in await ipam.list_addresses()] == ["srv-app-01"]


def _store(repository: InMemoryRepository, element: Element, properties: dict[str, str]) -> None:
    """Put properties into the graph behind the service, as another door would.

    `create_element` refuses these, which is the point: this is the row that
    got there some other way — an import, a hand-run Cypher statement, a
    version of this code that did not check yet.
    """
    repository.elements[element.id] = replace(element, properties=properties)


@pytest.mark.asyncio
class TestListingSubnetsOfBothFamilies:
    async def test_a_scope_holding_ipv4_and_ipv6_still_lists_its_subnets(
        self, ipam: IpamService
    ) -> None:
        """An IPv4 and an IPv6 network are not orderable against each other.

        Sorting them as networks raised inside every listing of a dual-stack
        VRF, so one IPv6 subnet made the whole IPAM screen unreadable. Family
        first, then address, then the narrower prefix last.
        """
        await ipam.declare_network(name="DMZ v6", cidr="2001:db8::/64", vrf="dmz")
        await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24", vrf="dmz")
        await ipam.declare_network(name="Admin", cidr="10.0.0.0/24", vrf="dmz")
        await ipam.declare_network(name="Corporate", cidr="10.0.0.0/8", vrf="dmz")

        listed = await ipam.list_networks()

        assert [subnet.element.name for subnet in listed] == [
            "Corporate",
            "Admin",
            "DMZ",
            "DMZ v6",
        ]

    async def test_a_64_with_reservations_reports_how_full_it_is_at_once(
        self, ipam: IpamService
    ) -> None:
        await ipam.declare_network(
            name="DMZ v6", cidr="2001:db8::/64", reserved="2001:db8::1-2001:db8::ff"
        )

        [subnet] = await ipam.list_networks()

        assert subnet.network.reserved_count == 255
        assert subnet.free == 2**64 - 1 - 255

    async def test_listing_subnets_reads_the_declared_subnets_once(self) -> None:
        repository = _CountingNetworkReads()
        service = ArchitectureService(repository, clock=lambda: FIXED_NOW)
        ipam = IpamService(service, repository)
        subnet = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        host = await service.create_element(element_type=E.NODE, name="srv-app-01")
        await ipam.allocate_next(subnet.element.id, host.id)
        repository.network_reads = 0

        [listed] = await ipam.list_networks()

        assert listed.used == 1
        assert repository.network_reads == 1


@pytest.mark.asyncio
class TestTheCatalogueWritesTheConventionInOneSpelling:
    """`create_element` and `update_element` are doors onto the IPAM too.

    The uniqueness constraints compare the stored strings, so a prefix or an
    address kept as typed would be one spelling away from a duplicate — see
    `docs/adr/0020`.
    """

    async def test_a_prefix_written_through_the_catalogue_is_stored_as_the_network(
        self, service: ArchitectureService
    ) -> None:
        network = await service.create_element(
            element_type=E.COMMUNICATION_NETWORK,
            name="DMZ",
            properties={"cidr": "10.0.1.0/255.255.255.0"},
        )

        stored = await service.get_element(network.id)
        assert dict(stored.properties) == {"cidr": "10.0.1.0/24", "vrf": DEFAULT_VRF}

    async def test_an_update_through_the_catalogue_is_stored_the_same_way(
        self, service: ArchitectureService
    ) -> None:
        network = await service.create_element(element_type=E.COMMUNICATION_NETWORK, name="DMZ")

        await service.update_element(
            network.id, properties={"cidr": " 2001:DB8::/64 ", "vrf": "dmz"}
        )

        stored = await service.get_element(network.id)
        assert dict(stored.properties) == {"cidr": "2001:db8::/64", "vrf": "dmz"}

    async def test_an_address_written_through_the_catalogue_is_seen_as_taken(
        self, ipam: IpamService, service: ArchitectureService
    ) -> None:
        await ipam.declare_network(name="DMZ v6", cidr="2001:db8::/64")
        await service.create_element(
            element_type=E.NODE, name="srv-app-01", properties={"ip_address": "2001:DB8::1"}
        )
        second = await service.create_element(element_type=E.NODE, name="srv-app-02")

        with pytest.raises(AddressAlreadyAssignedError, match="srv-app-01"):
            await ipam.assign_address(second.id, "2001:db8::1")


@pytest.mark.asyncio
class TestLosingAnAllocationRace:
    """Two callers allocating from one subnet both read the same free address.

    The constraint makes sure only one of them gets it. The other asked for
    *an* address, not that one, so it is handed the next rather than a refusal
    it would have to retry by itself — within a bound, so a subnet that keeps
    being raced for still answers.
    """

    async def test_the_loser_is_handed_the_next_address(self) -> None:
        repository, service, ipam = _a_graph_read_before_a_rival_wrote()
        subnet = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        rival = await service.create_element(element_type=E.NODE, name="rival")
        await ipam.assign_address(rival.id, "10.0.1.1")
        latecomer = await service.create_element(element_type=E.NODE, name="latecomer")
        repository.hide(rival.id, reads=1)

        assigned = await ipam.allocate_next(subnet.element.id, latecomer.id)

        assert str(assigned.address) == "10.0.1.2"

    async def test_a_race_lost_every_time_is_reported_rather_than_retried_forever(self) -> None:
        repository, service, ipam = _a_graph_read_before_a_rival_wrote()
        subnet = await ipam.declare_network(name="DMZ", cidr="10.0.1.0/24")
        rival = await service.create_element(element_type=E.NODE, name="rival")
        await ipam.assign_address(rival.id, "10.0.1.1")
        latecomer = await service.create_element(element_type=E.NODE, name="latecomer")
        repository.hide(rival.id, reads=1_000)

        with pytest.raises(AddressAlreadyAssignedError, match="rival"):
            await ipam.allocate_next(subnet.element.id, latecomer.id)

        assert repository.stale_reads > 900


class _CountingNetworkReads(InMemoryRepository):
    """The graph double, counting how often the declared subnets are fetched."""

    def __init__(self) -> None:
        super().__init__()
        self.network_reads = 0

    async def networks(self) -> tuple[Element, ...]:
        self.network_reads += 1
        return await super().networks()


class _StaleInventory(InMemoryRepository):
    """A graph whose inventory is read before a rival's address has landed.

    That is what losing a race looks like from inside the service: the listing
    says an address is free, and the write then finds it taken.
    """

    def __init__(self) -> None:
        super().__init__()
        self.stale_reads = 0
        self._hidden: set[UUID] = set()

    def hide(self, element_id: UUID, *, reads: int) -> None:
        self._hidden.add(element_id)
        self.stale_reads = reads

    async def addressed_elements(self) -> tuple[Element, ...]:
        rows = await super().addressed_elements()
        if self.stale_reads <= 0:
            return rows
        self.stale_reads -= 1
        return tuple(element for element in rows if element.id not in self._hidden)


def _a_graph_read_before_a_rival_wrote() -> tuple[
    _StaleInventory, ArchitectureService, IpamService
]:
    repository = _StaleInventory()
    service = ArchitectureService(repository, clock=lambda: FIXED_NOW)
    return repository, service, IpamService(service, repository)
