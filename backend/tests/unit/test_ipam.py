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
    assignable_addresses,
    capacity,
    is_addressable,
    next_free_address,
    parse_address,
    parse_prefix,
    parse_reservations,
    reserved_by_the_protocol,
    validate_ipam_properties,
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


#: Small enough to walk, so the arithmetic can be checked against a brute force
#: that visits every address. Both families, and each of the sizes where a
#: prefix keeps something for itself differently: the ordinary pair, RFC 3021's
#: /31, the /32 that is a host, IPv6's subnet-router anycast, the /128.
SMALL_PREFIXES = (
    "10.0.1.0/28",
    "10.0.1.0/30",
    "10.0.1.0/31",
    "10.0.1.5/32",
    "2001:db8::/124",
    "2001:db8::/127",
    "2001:db8::1/128",
)

#: Every way two reservations can sit against each other and against a prefix.
#: Each string mixes families on purpose: an IPv4 range must count for nothing
#: in an IPv6 prefix, and the other way round.
RESERVATION_SHAPES = {
    "nothing": "",
    "overlapping": (
        "10.0.1.2-10.0.1.6, 10.0.1.4-10.0.1.9, 10.0.1.5, "
        "2001:db8::2-2001:db8::6, 2001:db8::4-2001:db8::9, 2001:db8::5"
    ),
    "adjacent": (
        "10.0.1.1-10.0.1.3, 10.0.1.4-10.0.1.7, 2001:db8::1-2001:db8::3, 2001:db8::4-2001:db8::7"
    ),
    "partly outside": (
        "10.0.0.250-10.0.1.2, 10.0.1.14-10.0.2.3, "
        "2001:db7:ffff:ffff:ffff:ffff:ffff:fff0-2001:db8::2, 2001:db8::e-2001:db8::1:0"
    ),
    "what the prefix keeps anyway": (
        "10.0.1.0, 10.0.1.15, 10.0.1.3, 10.0.1.0/28, 2001:db8::, 2001:db8::/124"
    ),
    "wholly outside": "10.0.2.0/24, 192.168.0.1, 2001:db9::/64",
    "nested": "10.0.1.0/29, 10.0.1.3-10.0.1.12, 2001:db8::/125, 2001:db8::3-2001:db8::c",
}


class TestCountingReservations:
    """`reserved_count` is computed for every subnet on every listing.

    So it must cost what the *reservations* cost, never what the prefix holds:
    walking a /64 to count its reserved addresses is eighteen quintillion
    iterations on the event loop, and every request of the process waits behind
    them.
    """

    def test_a_64_is_counted_without_being_walked(self) -> None:
        network = IpNetwork.read(
            {"cidr": "2001:db8::/64", "ip_reserved": "2001:db8::/65, 2001:db8::1-2001:db8::ffff"}
        )

        assert network is not None
        # The /65 swallows the second range whole, and its first address is the
        # subnet-router anycast, which the capacity has already set aside.
        assert network.reserved_count == 2**63 - 1

    @pytest.mark.parametrize("prefix_text", SMALL_PREFIXES)
    @pytest.mark.parametrize("shape", sorted(RESERVATION_SHAPES))
    def test_it_agrees_with_walking_every_address(self, prefix_text: str, shape: str) -> None:
        prefix = ip_network(prefix_text)
        reserved = parse_reservations(RESERVATION_SHAPES[shape])

        walked = sum(
            1
            for address in prefix
            if address in reserved and not reserved_by_the_protocol(prefix, address)
        )

        assert reserved.count_within(prefix) == walked

    @pytest.mark.parametrize("prefix_text", SMALL_PREFIXES)
    @pytest.mark.parametrize("shape", sorted(RESERVATION_SHAPES))
    def test_capacity_less_reservations_is_exactly_what_can_be_handed_out(
        self, prefix_text: str, shape: str
    ) -> None:
        """The subnet's `free` figure is this subtraction, so it has to be exact.

        A reservation covering the network or broadcast address must not take
        that address off a second time: the capacity never counted it.
        """
        prefix = ip_network(prefix_text)
        reserved = parse_reservations(RESERVATION_SHAPES[shape])
        walked = [
            address
            for address in prefix
            if not reserved_by_the_protocol(prefix, address) and address not in reserved
        ]

        assert list(assignable_addresses(prefix, reserved)) == walked
        assert capacity(prefix) - reserved.count_within(prefix) == len(walked)


class TestAllocatingFromAHugePrefix:
    def test_a_reserved_stretch_is_jumped_rather_than_walked(self) -> None:
        """`MAX_ALLOCATION_SCAN` bounds the addresses *offered*, not the ones skipped.

        So a reservation must be stepped over in one move: otherwise the first
        half of a /64 set aside is 2**63 addresses examined and refused before
        the bound ever counts one.
        """
        found = next_free_address(
            ip_network("2001:db8::/64"), taken=(), reserved=parse_reservations("2001:db8::/65")
        )

        assert found == ip_address("2001:db8::8000:0:0:0")


class TestTheConventionIsStoredInOneSpelling:
    """A uniqueness constraint compares strings, not networks.

    `10.0.1.0/24` and `10.0.1.0/255.255.255.0` are one subnet and two values, so
    a property stored as typed would let the same prefix be declared twice past
    the constraint that exists to forbid it — and the same goes for an address.
    Whatever door the properties come through, they leave in canonical form.
    """

    @pytest.mark.parametrize(
        ("typed", "stored"),
        [
            (" 10.0.1.0/255.255.255.0 ", "10.0.1.0/24"),
            ("2001:DB8::/64", "2001:db8::/64"),
            ("2001:0db8:0000::/64", "2001:db8::/64"),
            ("10.0.1.5", "10.0.1.5/32"),
        ],
    )
    def test_a_prefix_is_stored_as_the_network_it_names(self, typed: str, stored: str) -> None:
        canonical = validate_ipam_properties(NETWORK_TYPE, {"cidr": typed})

        assert canonical == {"cidr": stored, "vrf": DEFAULT_VRF}

    def test_an_address_is_stored_canonical_beside_its_scope(self) -> None:
        canonical = validate_ipam_properties(
            E.NODE, {"ip_address": " 2001:DB8::1 ", "vrf": " dmz ", "owner": "infra"}
        )

        assert canonical == {"ip_address": "2001:db8::1", "vrf": "dmz", "owner": "infra"}

    def test_an_unstated_scope_is_written_rather_than_implied(self) -> None:
        """A composite constraint ignores a node missing one of its properties.

        An address or a prefix stored without `vrf` would therefore be outside
        the only guarantee that it is unique, so the default is written out.
        """
        canonical = validate_ipam_properties(E.NODE, {"ip_address": "10.0.1.12"})

        assert canonical == {"ip_address": "10.0.1.12", "vrf": DEFAULT_VRF}

    def test_properties_that_say_nothing_about_addressing_are_left_alone(self) -> None:
        assert validate_ipam_properties(E.NODE, {"owner": "infra"}) == {"owner": "infra"}
        assert validate_ipam_properties(E.NODE, None) is None
