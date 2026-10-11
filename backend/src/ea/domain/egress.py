"""Which addresses an outbound call may reach — decided on the address, never on a name.

Pure: the standard library's `ipaddress` and nothing else. The server only calls hosts the
configuration names, but a name is resolved by somebody else's DNS, so the *address* it
resolves to is what is checked, for every address the name returns (docs/adr/0063).

Two lists. What nobody may reach whatever the configuration says: loopback, link-local
(which holds the cloud metadata services), unspecified, multicast, reserved, and the IPv6
forms that embed an IPv4 address. And what only a declared network may reach: every
non-global range (RFC 1918, CGNAT, unique-local) — an on-premises Jira is real, so the
operator names its network, and nothing broader than that is ever implied.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from typing import Final

type IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
type IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


class EgressRefusedError(Exception):
    """An address the policy forbids. Its message names the rule, never the address's owner."""


#: Reachable by nobody, whatever the configuration declares.
NEVER: Final[tuple[IPNetwork, ...]] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "100.100.100.200/32",
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::254/128",
        "64:ff9b::/96",
        "2002::/16",
    )
)


def _unwrapped(address: IPAddress) -> IPAddress:
    """The IPv4 address an IPv4-mapped IPv6 address stands for, else the address itself."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def check_address(address: IPAddress, *, allowed: Iterable[IPNetwork]) -> None:
    """Raise `EgressRefusedError` unless an outbound call may reach `address`."""
    target = _unwrapped(address)
    if any(target.version == net.version and target in net for net in NEVER):
        msg = "that address is one no outbound call may reach"
        raise EgressRefusedError(msg)
    if target.is_global:
        return
    if any(target.version == net.version and target in net for net in allowed):
        return
    msg = "that address is not public and its network is not declared"
    raise EgressRefusedError(msg)


def networks_from(cidrs: Iterable[str]) -> tuple[IPNetwork, ...]:
    """The networks a configuration declares as reachable, each checked against `NEVER`.

    A range that overlaps a forbidden one — `0.0.0.0/0` included — cannot be declared. The
    error never repeats the text it was given.
    """
    found: list[IPNetwork] = []
    for cidr in cidrs:
        try:
            network = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            msg = "a declared network is not a CIDR range"
            raise ValueError(msg) from None
        if any(network.version == never.version and network.overlaps(never) for never in NEVER):
            msg = "a declared network may not overlap loopback, link-local or reserved ranges"
            raise ValueError(msg)
        found.append(network)
    return tuple(found)
