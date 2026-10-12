"""Which addresses an outbound call may reach — decided on the address, never on a name.

Pure rules (docs/adr/0073). A name that resolves to a loopback, a link-local, a cloud
metadata or a private address is the SSRF shape, and the check is done on every address
the name resolves to, so a name answering with one public and one private address is
refused as a whole.
"""

from __future__ import annotations

import ipaddress

import pytest

from ea.domain.egress import (
    EgressRefusedError,
    check_address,
    networks_from,
)

PUBLIC = "93.184.216.34"


def net(*cidrs: str) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    return networks_from(cidrs)


class TestAddressesNobodyMayReach:
    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            "0.0.0.0",
            "::",
            "169.254.169.254",
            "169.254.0.1",
            "fe80::1",
            "fd00:ec2::254",
            "224.0.0.1",
            "ff02::1",
            "240.0.0.1",
            "255.255.255.255",
            "::ffff:127.0.0.1",
            "::ffff:169.254.169.254",
            "::127.0.0.1",
            "::7f00:1",
            "::a00:1",
            "fec0::1",
            "100.100.100.200",
        ],
    )
    def test_they_are_refused_even_when_the_configuration_allows_everything_private(
        self, address: str
    ) -> None:
        everything = net("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fd12::/16")

        with pytest.raises(EgressRefusedError):
            check_address(ipaddress.ip_address(address), allowed=everything)


class TestPrivateAddresses:
    @pytest.mark.parametrize(
        "address", ["10.1.2.3", "172.16.0.1", "192.168.1.1", "100.64.0.1", "fd12:3456::1"]
    )
    def test_they_are_refused_unless_the_configuration_names_their_network(
        self, address: str
    ) -> None:
        with pytest.raises(EgressRefusedError):
            check_address(ipaddress.ip_address(address), allowed=())

    def test_a_declared_network_lets_its_addresses_through_and_only_those(self) -> None:
        allowed = net("10.1.0.0/16")

        check_address(ipaddress.ip_address("10.1.2.3"), allowed=allowed)
        with pytest.raises(EgressRefusedError):
            check_address(ipaddress.ip_address("10.2.0.1"), allowed=allowed)

    def test_an_ipv4_mapped_address_is_judged_as_the_ipv4_address_it_stands_for(self) -> None:
        allowed = net("10.1.0.0/16")

        check_address(ipaddress.ip_address("::ffff:10.1.2.3"), allowed=allowed)
        with pytest.raises(EgressRefusedError):
            check_address(ipaddress.ip_address("::ffff:10.2.2.3"), allowed=allowed)

    def test_a_public_address_needs_no_declaration(self) -> None:
        check_address(ipaddress.ip_address(PUBLIC), allowed=())


class TestDeclaringNetworks:
    @pytest.mark.parametrize(
        "cidr", ["127.0.0.0/8", "169.254.0.0/16", "0.0.0.0/0", "::/0", "fe80::/10", "224.0.0.0/4"]
    )
    def test_a_range_that_swallows_a_forbidden_one_cannot_be_declared(self, cidr: str) -> None:
        with pytest.raises(ValueError, match="network"):
            networks_from([cidr])

    def test_a_malformed_range_is_refused_without_echoing_it(self) -> None:
        with pytest.raises(ValueError) as raised:
            networks_from(["not-a-cidr"])

        assert "not-a-cidr" not in str(raised.value)

    def test_ranges_that_merely_contain_a_metadata_address_can_be_declared_without_it(self) -> None:
        wide = net("100.64.0.0/10", "fd00::/8")

        check_address(ipaddress.ip_address("100.64.0.9"), allowed=wide)
        check_address(ipaddress.ip_address("fd12::1"), allowed=wide)
        for metadata in ("100.100.100.200", "fd00:ec2::254"):
            with pytest.raises(EgressRefusedError):
                check_address(ipaddress.ip_address(metadata), allowed=wide)

    def test_a_private_range_can_be_declared(self) -> None:
        assert networks_from(["10.1.0.0/16", "fd12::/16"])
