"""The addressing rules, checked without a graph and without a clock.

`domain/ipam.py` is pure: it turns the free-form properties an element carries
into addresses and prefixes, and it decides what may be assigned where. Every
rule that needs only an address and a prefix is here; the rules that need the
rest of the catalogue — is this address already taken, does the element's type
allow one at all — are `IpamService`'s, in `test_ipam_service.py`.
"""

from __future__ import annotations

import re
from ipaddress import ip_address, ip_network

import pytest

from ea.domain.archimate import ElementType as E
from ea.domain.ipam import (
    DEFAULT_VRF,
    MAX_ALLOCATION_SCAN,
    NETWORK_TYPE,
    IpNetwork,
    capacity,
    is_addressable,
    next_free_address,
    parse_address,
    parse_prefix,
    parse_reservations,
    reserved_by_the_protocol,
)


class TestParsingAPrefix:
    def test_a_prefix_is_read_as_written(self) -> None:
        assert parse_prefix("10.0.1.0/24") == ip_network("10.0.1.0/24")

    def test_a_bare_address_is_a_single_host_prefix(self) -> None:
        assert parse_prefix("10.0.1.5") == ip_network("10.0.1.5/32")

    def test_host_bits_are_refused_and_the_message_names_the_prefix_meant(self) -> None:
        """`10.0.1.5/24` is the commonest way to write a subnet wrongly.

        Silently normalising it would store a network nobody typed; the error
        says which one was probably meant, so the correction is one edit.
        """
        with pytest.raises(ValueError, match=re.escape("10.0.1.0/24")):
            parse_prefix("10.0.1.5/24")

    def test_nonsense_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a network"):
            parse_prefix("chez moi")

    def test_ipv6_is_read_the_same_way(self) -> None:
        assert parse_prefix("2001:db8::/64") == ip_network("2001:db8::/64")


class TestParsingAnAddress:
    def test_an_address_is_canonicalised(self) -> None:
        """Two spellings of one address must not become two rows."""
        assert str(parse_address(" 2001:0db8:0000::1 ")) == "2001:db8::1"

    def test_a_prefix_is_not_an_address(self) -> None:
        with pytest.raises(ValueError, match="not an IP address"):
            parse_address("10.0.1.0/24")


class TestWhatMayBeAssigned:
    def test_the_network_and_broadcast_addresses_of_an_ipv4_prefix_are_not_hosts(self) -> None:
        prefix = ip_network("10.0.1.0/24")

        assert reserved_by_the_protocol(prefix, ip_address("10.0.1.0"))
        assert reserved_by_the_protocol(prefix, ip_address("10.0.1.255"))
        assert not reserved_by_the_protocol(prefix, ip_address("10.0.1.1"))

    def test_a_point_to_point_link_has_no_such_pair(self) -> None:
        """RFC 3021: both addresses of a /31 are usable, and routers rely on it."""
        prefix = ip_network("10.0.1.0/31")

        assert not reserved_by_the_protocol(prefix, ip_address("10.0.1.0"))
        assert not reserved_by_the_protocol(prefix, ip_address("10.0.1.1"))

    def test_the_subnet_router_anycast_address_of_an_ipv6_prefix_is_not_a_host(self) -> None:
        prefix = ip_network("2001:db8::/64")

        assert reserved_by_the_protocol(prefix, ip_address("2001:db8::"))
        assert not reserved_by_the_protocol(prefix, ip_address("2001:db8::ffff"))

    def test_capacity_excludes_the_pair_a_prefix_cannot_hand_out(self) -> None:
        assert capacity(ip_network("10.0.1.0/24")) == 254
        assert capacity(ip_network("10.0.1.0/31")) == 2
        assert capacity(ip_network("10.0.1.1/32")) == 1


class TestReservations:
    def test_an_entry_may_be_an_address_a_range_or_a_sub_prefix(self) -> None:
        reserved = parse_reservations("10.0.1.1, 10.0.1.10-10.0.1.20, 10.0.1.128/25")

        assert ip_address("10.0.1.1") in reserved
        assert ip_address("10.0.1.15") in reserved
        assert ip_address("10.0.1.200") in reserved
        assert ip_address("10.0.1.2") not in reserved

    def test_nothing_reserved_is_the_empty_reservation(self) -> None:
        assert ip_address("10.0.1.1") not in parse_reservations("  ")

    def test_a_backwards_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="ends before it starts"):
            parse_reservations("10.0.1.20-10.0.1.10")

    def test_a_range_that_mixes_families_is_refused(self) -> None:
        with pytest.raises(ValueError, match="same family"):
            parse_reservations("10.0.1.1-2001:db8::1")


class TestNextFreeAddress:
    def test_it_is_the_first_host_nothing_holds(self) -> None:
        prefix = ip_network("10.0.1.0/24")

        assert next_free_address(prefix, taken=(), reserved=parse_reservations("")) == ip_address(
            "10.0.1.1"
        )

    def test_it_skips_what_is_taken_and_what_is_reserved(self) -> None:
        prefix = ip_network("10.0.1.0/24")

        found = next_free_address(
            prefix,
            taken={ip_address("10.0.1.1"), ip_address("10.0.1.3")},
            reserved=parse_reservations("10.0.1.2"),
        )

        assert found == ip_address("10.0.1.4")

    def test_a_full_prefix_answers_with_nothing_rather_than_a_wrong_address(self) -> None:
        prefix = ip_network("10.0.1.0/30")

        assert (
            next_free_address(
                prefix,
                taken={ip_address("10.0.1.1"), ip_address("10.0.1.2")},
                reserved=parse_reservations(""),
            )
            is None
        )


class TestWhichElementsMayCarryAnAddress:
    @pytest.mark.parametrize(
        "element_type", [E.NODE, E.DEVICE, E.EQUIPMENT, E.SYSTEM_SOFTWARE, E.TECHNOLOGY_INTERFACE]
    )
    def test_the_things_that_really_have_one(self, element_type: E) -> None:
        assert is_addressable(element_type)

    @pytest.mark.parametrize("element_type", [E.BUSINESS_PROCESS, E.CAPABILITY, E.GOAL])
    def test_and_nothing_else(self, element_type: E) -> None:
        assert not is_addressable(element_type)

    def test_a_subnet_is_a_communication_network_and_not_an_addressable_thing(self) -> None:
        """The prefix is the network's own property, not an address on it."""
        assert NETWORK_TYPE is E.COMMUNICATION_NETWORK
        assert not is_addressable(NETWORK_TYPE)


class TestReadingANetworkOffItsProperties:
    def test_the_properties_of_a_communication_network_become_a_prefix(self) -> None:
        network = IpNetwork.read({"cidr": "10.0.1.0/24", "vrf": "dmz", "ip_reserved": "10.0.1.1"})

        assert network is not None
        assert network.prefix == ip_network("10.0.1.0/24")
        assert network.vrf == "dmz"
        assert ip_address("10.0.1.1") in network.reserved

    def test_an_element_without_a_cidr_is_simply_not_a_subnet(self) -> None:
        assert IpNetwork.read({"owner": "réseau"}) is None

    def test_an_unstated_vrf_is_the_default_one(self) -> None:
        network = IpNetwork.read({"cidr": "10.0.1.0/24"})

        assert network is not None
        assert network.vrf == DEFAULT_VRF


class TestTheBoundOnAllocation:
    def test_a_prefix_too_large_to_walk_answers_with_nothing(self) -> None:
        """A /8 holds sixteen million addresses.

        Told that every address it scanned is taken, the honest answer is that
        one does not allocate linearly from a prefix this size — not a number
        that arrives a minute later.
        """
        prefix = ip_network("10.0.0.0/8")
        wall = MAX_ALLOCATION_SCAN + 1

        found = next_free_address(
            prefix,
            taken={ip_address(f"10.{n // 65536}.{n // 256 % 256}.{n % 256}") for n in range(wall)},
            reserved=parse_reservations(""),
        )

        assert found is None
