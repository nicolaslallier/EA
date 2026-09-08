"""Where an IP address lives in the model, and what may be assigned where.

There is no `:IpAddress` node and no `:Subnet` node — see `docs/adr/0020`. An
address is a **property of the element that answers on it**, and a subnet is an
ArchiMate `communication_network` element carrying its prefix. So the IPAM adds
no concept to a metamodel that is deliberately closed at 61 types: it adds a
*convention* about four property names, and this module is that convention,
written once.

    communication_network "DMZ"        p_cidr = 10.0.1.0/24, p_vrf = dmz
    node "srv-app-01"                  p_ip_address = 10.0.1.12, p_vrf = dmz

The whole point of holding one address per element rather than a list is that
`p_vrf` and `p_ip_address` can then be a **uniqueness constraint** in Neo4j
(`db/schema.py`): two elements cannot claim the same address, and that is
enforced by the database rather than by a check that races. A host with two
NICs is two `technology_interface` elements composed into one node — which is
how ArchiMate says to model it anyway.

Everything here is pure. Reading an address off an element is parsing; deciding
whether it is free needs the rest of the catalogue and belongs to
`services/ipam.py`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Final
from uuid import UUID

from ea.domain.archimate import ElementType
from ea.domain.errors import NotAddressableError, NotASubnetError
from ea.domain.model import Element
from ea.domain.ports import GraphView

type IpAddress = IPv4Address | IPv6Address
type IpPrefix = IPv4Network | IPv6Network

#: The four property names the convention reserves. They are stored under the
#: `p_` prefix like every user-defined attribute (`db/schema.py`), so they stay
#: greppable in Cypher: `MATCH (e:Element) WHERE e.p_ip_address = '10.0.1.12'`.
ADDRESS_PROPERTY: Final = "ip_address"
PREFIX_PROPERTY: Final = "cidr"
VRF_PROPERTY: Final = "vrf"
RESERVED_PROPERTY: Final = "ip_reserved"

IPAM_PROPERTIES: Final = frozenset(
    {ADDRESS_PROPERTY, PREFIX_PROPERTY, VRF_PROPERTY, RESERVED_PROPERTY}
)

#: The routing scope an address is unique in. One flat network is still a VRF,
#: so the field is never empty and the uniqueness constraint never sees a null —
#: a composite constraint in Neo4j simply does not apply to a node missing one
#: of its properties, which would have made the guarantee vanish exactly where
#: someone forgot to name a VRF.
DEFAULT_VRF: Final = "default"

#: A subnet is this, and only this. `communication_network` is ArchiMate's own
#: word for "a set of nodes that can exchange data" — the concept was already
#: there, it simply had no prefix written on it.
NETWORK_TYPE: Final = ElementType.COMMUNICATION_NETWORK

#: What may answer on an address. A business process cannot be pinged, and an
#: address written on one is a modelling mistake that no later query can undo,
#: so it is refused at the door — the same discipline as the relationship
#: matrix, applied to the one attribute the metamodel does not cover.
ADDRESSABLE_TYPES: Final[frozenset[ElementType]] = frozenset(
    {
        ElementType.NODE,
        ElementType.DEVICE,
        ElementType.EQUIPMENT,
        ElementType.SYSTEM_SOFTWARE,
        ElementType.TECHNOLOGY_INTERFACE,
    }
)

#: How far `next_free_address` walks before giving up. A /8 holds sixteen
#: million addresses; scanning them all to answer "the first one free" would
#: turn one API call into a minute of arithmetic. Past this bound the honest
#: answer is that the prefix is too large to allocate from linearly, not a
#: number that arrived late.
MAX_ALLOCATION_SCAN: Final = 100_000


def is_addressable(element_type: ElementType) -> bool:
    """Whether an element of this type may carry an IP address."""
    return element_type in ADDRESSABLE_TYPES


def parse_address(text: str) -> IpAddress:
    """One address, canonicalised, so two spellings never become two rows."""
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError as error:
        msg = f"{text.strip()!r} is not an IP address"
        raise ValueError(msg) from error


def parse_prefix(text: str) -> IpPrefix:
    """One subnet. A bare address is the prefix holding only itself.

    Host bits set are refused rather than normalised: `10.0.1.5/24` is the
    commonest way to write a subnet wrongly, and silently storing
    `10.0.1.0/24` would put a network in the catalogue that nobody typed and
    nobody would think to check.
    """
    cleaned = text.strip()
    try:
        return ipaddress.ip_network(cleaned, strict=True)
    except ValueError as error:
        loose = _forgiving_prefix(cleaned)
        if loose is not None:
            msg = (
                f"{cleaned!r} has host bits set — write the network itself, "
                f"{loose.with_prefixlen!r}"
            )
            raise ValueError(msg) from error
        msg = f"{cleaned!r} is not a network prefix"
        raise ValueError(msg) from error


def _forgiving_prefix(text: str) -> IpPrefix | None:
    """The prefix the author probably meant, or nothing if it is not one at all."""
    try:
        return ipaddress.ip_network(text, strict=False)
    except ValueError:
        return None


def reserved_by_the_protocol(prefix: IpPrefix, address: IpAddress) -> bool:
    """Whether IP itself forbids handing this address to a host.

    IPv4 keeps the first and last address of a subnet for the network and the
    broadcast — except on a /31, where RFC 3021 gives both to the two ends of a
    point-to-point link, and on a /32, which *is* a host. IPv6 has no
    broadcast, but the first address of a subnet is the subnet-router anycast
    address (RFC 4291) and is not a host's either.
    """
    if prefix.prefixlen >= prefix.max_prefixlen - 1:
        return False
    if isinstance(prefix, IPv6Network):
        return address == prefix.network_address
    return address in (prefix.network_address, prefix.broadcast_address)


def capacity(prefix: IpPrefix) -> int:
    """How many addresses this prefix can actually hand to hosts."""
    if prefix.prefixlen >= prefix.max_prefixlen - 1:
        return prefix.num_addresses
    return prefix.num_addresses - (1 if isinstance(prefix, IPv6Network) else 2)


@dataclass(frozen=True, slots=True)
class Reservations:
    """Addresses set aside by hand: a gateway, a DHCP pool, a vendor's range.

    Held as ranges rather than as a set, because "10.0.0.0/16 is the DHCP pool"
    is one entry and sixty-five thousand addresses.
    """

    ranges: tuple[tuple[IpAddress, IpAddress], ...] = ()

    def __contains__(self, address: object) -> bool:
        if not isinstance(address, IPv4Address | IPv6Address):
            return False
        # Compared as integers: two addresses of different families are not
        # orderable at all, in the type system or at runtime, and the version
        # check alone does not tell a checker that.
        return any(
            first.version == address.version and int(first) <= int(address) <= int(last)
            for first, last in self.ranges
        )

    def count_within(self, prefix: IpPrefix) -> int:
        """How many of this prefix's addresses the reservations cover.

        Counted by walking the prefix's own addresses rather than by adding the
        range sizes, because two reservations may overlap and a total that
        double-counts them would report a subnet as fuller than it is.
        """
        return sum(1 for address in prefix if address in self)


def parse_reservations(text: str) -> Reservations:
    """Read the `ip_reserved` property: addresses, `first-last` ranges, or prefixes."""
    ranges: list[tuple[IpAddress, IpAddress]] = []
    for raw in text.split(","):
        entry = raw.strip()
        if not entry:
            continue
        ranges.append(_parse_reservation(entry))
    return Reservations(tuple(ranges))


def _parse_reservation(entry: str) -> tuple[IpAddress, IpAddress]:
    if "-" in entry:
        first_text, _, last_text = entry.partition("-")
        first, last = parse_address(first_text), parse_address(last_text)
        if first.version != last.version:
            msg = f"the two ends of the range {entry!r} are not of the same family"
            raise ValueError(msg)
        if int(last) < int(first):
            msg = f"the range {entry!r} ends before it starts"
            raise ValueError(msg)
        return first, last
    if "/" in entry:
        prefix = parse_prefix(entry)
        return prefix.network_address, prefix.broadcast_address
    address = parse_address(entry)
    return address, address


def assignable_addresses(prefix: IpPrefix, reserved: Reservations) -> Iterator[IpAddress]:
    """Every address of the prefix a host may be given, in order."""
    for address in prefix:
        if reserved_by_the_protocol(prefix, address) or address in reserved:
            continue
        yield address


def next_free_address(
    prefix: IpPrefix,
    *,
    taken: Iterable[IpAddress],
    reserved: Reservations,
) -> IpAddress | None:
    """The first address of the prefix nothing holds, or nothing if it is full.

    `None` rather than an exception: "this subnet is full" is an answer the
    caller may well want to display beside the others, and the service turns it
    into a refusal only when someone actually asked to be given one.
    """
    held = set(taken)
    for scanned, address in enumerate(assignable_addresses(prefix, reserved)):
        if scanned >= MAX_ALLOCATION_SCAN:
            return None
        if address not in held:
            return address
    return None


@dataclass(frozen=True, slots=True)
class IpNetwork:
    """A subnet, as read off the properties of a `communication_network` element."""

    prefix: IpPrefix
    vrf: str
    reserved: Reservations

    @classmethod
    def read(cls, properties: Mapping[str, str]) -> IpNetwork | None:
        """The subnet an element declares, or nothing if it declares none.

        `None` and not an error: a `communication_network` may perfectly well
        model a radio link with no prefix on it, and refusing to list the
        catalogue because one element is not an IP subnet would be absurd.
        """
        prefix_text = properties.get(PREFIX_PROPERTY, "").strip()
        if not prefix_text:
            return None
        return cls(
            prefix=parse_prefix(prefix_text),
            vrf=read_vrf(properties),
            reserved=parse_reservations(properties.get(RESERVED_PROPERTY, "")),
        )

    def holds(self, address: IpAddress) -> bool:
        return address.version == self.prefix.version and address in self.prefix

    @property
    def capacity(self) -> int:
        return capacity(self.prefix)

    @property
    def reserved_count(self) -> int:
        return self.reserved.count_within(self.prefix)


def read_vrf(properties: Mapping[str, str]) -> str:
    """The routing scope an element's addressing sits in. Never empty."""
    return properties.get(VRF_PROPERTY, "").strip() or DEFAULT_VRF


def read_address(properties: Mapping[str, str]) -> IpAddress | None:
    """The address an element answers on, or nothing if it has none."""
    text = properties.get(ADDRESS_PROPERTY, "").strip()
    return parse_address(text) if text else None


def most_specific(networks: Sequence[IpNetwork], address: IpAddress) -> IpNetwork | None:
    """Which declared subnet an address belongs to: the longest prefix holding it.

    Longest-prefix match, exactly as a routing table does it, so that declaring
    `10.0.0.0/8` as "the corporate range" does not make every host in
    `10.0.1.0/24` belong to it instead.
    """
    holders = [network for network in networks if network.holds(address)]
    if not holders:
        return None
    return max(holders, key=lambda network: network.prefix.prefixlen)


# --------------------------------------------------------------------------
# What a use case answers with
# --------------------------------------------------------------------------
# These carry the element itself rather than its id: an address is unreadable
# without the thing that answers on it, exactly as a relationship is unreadable
# without its endpoints — the reason `GraphView` exists.


@dataclass(frozen=True, slots=True)
class Subnet:
    """A declared subnet: the element that declares it, and how full it is."""

    element: Element
    network: IpNetwork
    used: int

    @property
    def free(self) -> int:
        return max(self.network.capacity - self.network.reserved_count - self.used, 0)


@dataclass(frozen=True, slots=True)
class Assignment:
    """One address, the element answering on it, and the subnet it falls in."""

    element: Element
    address: IpAddress
    vrf: str
    subnet_id: UUID | None


@dataclass(frozen=True, slots=True)
class SubnetDetail:
    """A subnet with its occupants — what a subnet's page shows."""

    subnet: Subnet
    addresses: tuple[Assignment, ...]
    next_free: IpAddress | None


@dataclass(frozen=True, slots=True)
class AddressLocation:
    """The answer to "this address, that is what?".

    The assignment says which element answers; `graph` says what that element
    is wired to, so one call is enough to write "10.0.1.12 is srv-app-01, and
    Billing runs on it" — which is the question the whole feature exists for.
    """

    assignment: Assignment
    graph: GraphView


# --------------------------------------------------------------------------
# The convention, enforced wherever properties are written
# --------------------------------------------------------------------------


def validate_ipam_properties(
    element_type: ElementType, properties: Mapping[str, str] | None
) -> None:
    """Refuse an IPAM property that contradicts the element carrying it.

    Called from `ArchitectureService` on every write, not only from the IPAM
    use cases. The addressing lives in free-form properties, so `PATCH
    /elements/{id}` is a second door onto it; a rule checked behind only one of
    the two doors is a rule the inventory cannot rely on — see `docs/adr/0020`.
    """
    if not properties:
        return
    address = properties.get(ADDRESS_PROPERTY, "").strip()
    if address:
        parse_address(address)
        if not is_addressable(element_type):
            msg = (
                f"an element of type {element_type.value} cannot answer on an IP address; "
                f"the types that can are {', '.join(sorted(t.value for t in ADDRESSABLE_TYPES))}"
            )
            raise NotAddressableError(msg)
    for key in (PREFIX_PROPERTY, RESERVED_PROPERTY):
        if properties.get(key, "").strip() and element_type is not NETWORK_TYPE:
            msg = (
                f"{key!r} describes a subnet, so it belongs on a "
                f"{NETWORK_TYPE.value} element, not on a {element_type.value}"
            )
            raise NotASubnetError(msg)
    if prefix := properties.get(PREFIX_PROPERTY, "").strip():
        parse_prefix(prefix)
    parse_reservations(properties.get(RESERVED_PROPERTY, ""))
