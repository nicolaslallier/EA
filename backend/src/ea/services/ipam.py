"""IP address management, as a reading of the architecture graph.

This service adds no store. A subnet is a `communication_network` element
carrying a prefix, an address is a property of the element that answers on it,
and both are written through `ArchitectureService` — so an IP appears in the
catalogue, in the neighbourhood diagram and in the impact analysis without one
line of those knowing that IPAM exists. See `docs/adr/0020`.

What lives here is the handful of rules an address cannot check alone:

* the element's *type* must be one that can answer on an address at all;
* the address must fall inside a subnet somebody declared, in the same scope —
  an inventory that accepts addresses belonging to no subnet is one nobody can
  reconcile;
* the address must not be one the prefix keeps for itself, nor one the subnet
  reserves by hand;
* nothing else may already hold it.

The last rule is checked here *and* enforced by a uniqueness constraint in
Neo4j, which is what makes it true rather than likely: two agents allocating at
the same moment both read a free address, and only the constraint stops them
both writing it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final
from uuid import UUID

from ea.domain.errors import (
    AddressAlreadyAssignedError,
    AddressNotAssignedError,
    AddressOutsideAnyNetworkError,
    DuplicateNetworkError,
    NetworkExhaustedError,
    NotAddressableError,
    NotASubnetError,
)
from ea.domain.ipam import (
    ADDRESS_PROPERTY,
    DEFAULT_VRF,
    NETWORK_TYPE,
    PREFIX_PROPERTY,
    RESERVED_PROPERTY,
    VRF_PROPERTY,
    AddressLocation,
    Assignment,
    IpAddress,
    IpNetwork,
    Subnet,
    SubnetDetail,
    is_addressable,
    most_specific,
    next_free_address,
    parse_address,
    parse_prefix,
    read_address,
    read_vrf,
    reserved_by_the_protocol,
)

if TYPE_CHECKING:
    from ea.domain.model import Element
    from ea.domain.ports import IpamRepository
    from ea.services.architecture import ArchitectureService


logger = logging.getLogger(__name__)

#: How many times `allocate_next` reads the subnet again after losing a race
#: for the address it read as free. Enough to ride out a few callers
#: allocating from one subnet at once; few enough that a subnet which loses
#: every time is reported to the caller rather than waited on.
ALLOCATION_ATTEMPTS: Final = 3


class IpamService:
    """The IP use cases, over the architecture service and two extra queries."""

    def __init__(self, architecture: ArchitectureService, repository: IpamRepository) -> None:
        self._architecture = architecture
        self._repository = repository

    # --- Subnets ----------------------------------------------------------

    async def declare_network(
        self,
        *,
        name: str,
        cidr: str,
        vrf: str = DEFAULT_VRF,
        reserved: str = "",
        description: str = "",
    ) -> Subnet:
        """Declare a subnet: a `communication_network` element carrying a prefix.

        A prefix may be declared once per scope, and may sit inside another —
        `10.0.0.0/8` as the corporate range and `10.0.1.0/24` as the DMZ is the
        normal case, and an address then belongs to the longest one holding it.

        The look-before-create below is what names the subnet already there. It
        is not what makes the rule hold: two declarations can both look and
        both find nothing, and it is the `(p_vrf, p_cidr)` uniqueness
        constraint (`db/schema.py`) that refuses the second — translated by the
        repository into the same `DuplicateNetworkError`.
        """
        prefix = parse_prefix(cidr)
        scope = vrf.strip() or DEFAULT_VRF
        for existing in await self._subnets():
            if existing.network.vrf == scope and existing.network.prefix == prefix:
                msg = (
                    f"{prefix.with_prefixlen} is already declared in VRF {scope!r}, "
                    f"as {existing.element.name!r}"
                )
                raise DuplicateNetworkError(msg)
        element = await self._architecture.create_element(
            element_type=NETWORK_TYPE,
            name=name,
            description=description,
            properties={
                PREFIX_PROPERTY: prefix.with_prefixlen,
                VRF_PROPERTY: scope,
                **({RESERVED_PROPERTY: reserved.strip()} if reserved.strip() else {}),
            },
        )
        logger.info(
            "subnet %s declared in VRF %r as %r",
            prefix.with_prefixlen,
            scope,
            element.name,
            extra={
                "action": "subnet_declared",
                "cidr": prefix.with_prefixlen,
                "vrf": scope,
                "element_id": str(element.id),
            },
        )
        return await self._with_occupancy(element)

    async def list_networks(self, *, vrf: str | None = None) -> tuple[Subnet, ...]:
        """Every declared subnet, narrowest prefix last, with how full each is."""
        every = await self._subnets()
        assignments = await self._assignments(every)
        chosen = every if vrf is None else tuple(s for s in every if s.network.vrf == vrf)
        return tuple(
            sorted((self._occupancy(subnet, assignments) for subnet in chosen), key=_listing_order)
        )

    async def read_network(self, element_id: UUID) -> SubnetDetail:
        """One subnet with its occupants and the next address it would hand out."""
        element = await self._architecture.get_element(element_id)
        subnet = self._as_subnet(element)
        assignments = await self._assignments()
        inside = tuple(
            assignment
            for assignment in assignments
            if assignment.vrf == subnet.network.vrf and subnet.network.holds(assignment.address)
        )
        return SubnetDetail(
            subnet=self._occupancy(subnet, assignments),
            addresses=inside,
            next_free=next_free_address(
                subnet.network.prefix,
                taken={assignment.address for assignment in inside},
                reserved=subnet.network.reserved,
            ),
        )

    # --- Addresses --------------------------------------------------------

    async def assign_address(
        self, element_id: UUID, address: str, *, vrf: str = DEFAULT_VRF
    ) -> Assignment:
        """Give one element one address, refusing everything the rules forbid."""
        wanted = parse_address(address)
        scope = vrf.strip() or DEFAULT_VRF
        element = await self._architecture.get_element(element_id)
        if not is_addressable(element.element_type):
            msg = (
                f"{element.name!r} is a {element.element_type.value} and cannot answer "
                "on an IP address"
            )
            raise NotAddressableError(msg)

        subnets = await self._subnets()
        subnet = most_specific(
            [entry.network for entry in subnets if entry.network.vrf == scope], wanted
        )
        if subnet is None:
            msg = (
                f"no subnet declared in VRF {scope!r} holds {wanted}; "
                "declare the subnet before assigning addresses inside it"
            )
            raise AddressOutsideAnyNetworkError(msg)
        if reserved_by_the_protocol(subnet.prefix, wanted):
            role = "network address" if wanted == subnet.prefix.network_address else "broadcast"
            msg = f"{wanted} is the {role} of {subnet.prefix.with_prefixlen} and is not a host"
            raise ValueError(msg)
        if wanted in subnet.reserved:
            msg = f"{wanted} is reserved in {subnet.prefix.with_prefixlen}"
            raise ValueError(msg)

        holder = await self._repository.element_at(str(wanted), vrf=scope)
        if holder is not None and holder.id != element_id:
            msg = f"{wanted} is already assigned to {holder.name!r} in VRF {scope!r}"
            raise AddressAlreadyAssignedError(msg)

        return await self._write_address(element, wanted, scope, subnets)

    async def allocate_next(self, network_id: UUID, element_id: UUID) -> Assignment:
        """Hand the first free address of a subnet to an element.

        Two callers allocating at once both read the same address as free, and
        only one of them may write it — the service's check or, in the same
        instant, the uniqueness constraint says so. The other asked for *an*
        address rather than that one, so it reads the subnet again and takes the
        next, up to `ALLOCATION_ATTEMPTS` times; after that the refusal goes
        through, because a subnet that loses every race is news for the caller.
        """
        attempt = 1
        while True:
            detail = await self.read_network(network_id)
            if detail.next_free is None:
                msg = (
                    f"{detail.subnet.network.prefix.with_prefixlen} has no free address left "
                    f"in VRF {detail.subnet.network.vrf!r}"
                )
                raise NetworkExhaustedError(msg)
            try:
                return await self.assign_address(
                    element_id, str(detail.next_free), vrf=detail.subnet.network.vrf
                )
            except AddressAlreadyAssignedError:
                if attempt >= ALLOCATION_ATTEMPTS:
                    raise
                logger.info(
                    "%s was taken while being allocated; reading the subnet again",
                    detail.next_free,
                    extra={
                        "action": "allocation_retried",
                        "element_id": str(element_id),
                        "attempt": attempt,
                    },
                )
                attempt += 1

    async def release_address(self, element_id: UUID) -> None:
        """Take an element's address back. The element itself is left alone."""
        element = await self._architecture.get_element(element_id)
        if read_address(element.properties) is None:
            msg = f"{element.name!r} holds no IP address"
            raise AddressNotAssignedError(msg)
        remaining = {
            key: value for key, value in element.properties.items() if key != ADDRESS_PROPERTY
        }
        await self._architecture.update_element(element_id, properties=remaining)
        logger.info(
            "address released by %r",
            element.name,
            extra={"action": "address_released", "element_id": str(element_id)},
        )

    async def locate(self, address: str, *, vrf: str = DEFAULT_VRF) -> AddressLocation:
        """What answers on an address, and what that thing is wired to."""
        wanted = parse_address(address)
        scope = vrf.strip() or DEFAULT_VRF
        element = await self._repository.element_at(str(wanted), vrf=scope)
        if element is None:
            msg = f"nothing holds {wanted} in VRF {scope!r}"
            raise AddressNotAssignedError(msg)
        return AddressLocation(
            assignment=self._assignment(element, wanted, scope, await self._subnets()),
            graph=await self._architecture.relations_of(element.id),
        )

    async def list_addresses(
        self, *, vrf: str | None = None, within: str | None = None, search: str | None = None
    ) -> tuple[Assignment, ...]:
        """The inventory: every assigned address, in address order.

        `within` narrows it to one prefix — written as a prefix rather than as
        a subnet id, so "what is in 10.0.1.0/26" is answerable whether or not
        anyone declared that particular slice.
        """
        prefix = parse_prefix(within) if within else None
        needle = (search or "").strip().lower()
        return tuple(
            sorted(
                (
                    assignment
                    for assignment in await self._assignments()
                    if (vrf is None or assignment.vrf == vrf)
                    and (
                        prefix is None
                        or (
                            assignment.address.version == prefix.version
                            and assignment.address in prefix
                        )
                    )
                    and (not needle or needle in assignment.element.name.lower())
                ),
                key=lambda assignment: (
                    assignment.vrf,
                    assignment.address.version,
                    assignment.address,
                ),
            )
        )

    # --- Reading the graph as addresses ------------------------------------

    async def _subnets(self) -> tuple[Subnet, ...]:
        """Every element that declares a prefix, read as a subnet.

        An element whose `cidr` cannot be parsed is skipped rather than raised
        on: it was written through some other door, and one bad row must not
        make the whole inventory unreadable.
        """
        found: list[Subnet] = []
        for element in await self._repository.networks():
            try:
                network = IpNetwork.read(element.properties)
            except ValueError:
                continue
            if network is not None:
                found.append(Subnet(element=element, network=network, used=0))
        return tuple(found)

    async def _assignments(
        self, subnets: tuple[Subnet, ...] | None = None
    ) -> tuple[Assignment, ...]:
        """Every readable address, with the subnet it falls in.

        A caller that has already read the subnets hands them in: they are a
        query of their own, and listing the subnets used to issue it twice.
        """
        if subnets is None:
            subnets = await self._subnets()
        found: list[Assignment] = []
        for element in await self._repository.addressed_elements():
            try:
                address = read_address(element.properties)
            except ValueError:
                continue
            if address is not None:
                found.append(
                    self._assignment(element, address, read_vrf(element.properties), subnets)
                )
        return tuple(found)

    def _assignment(
        self,
        element: Element,
        address: IpAddress,
        vrf: str,
        subnets: tuple[Subnet, ...],
    ) -> Assignment:
        holder = most_specific(
            [subnet.network for subnet in subnets if subnet.network.vrf == vrf], address
        )
        subnet_id = next(
            (
                subnet.element.id
                for subnet in subnets
                if holder is not None
                and subnet.network.vrf == vrf
                and subnet.network.prefix == holder.prefix
            ),
            None,
        )
        return Assignment(element=element, address=address, vrf=vrf, subnet_id=subnet_id)

    def _as_subnet(self, element: Element) -> Subnet:
        network = IpNetwork.read(element.properties) if element.properties else None
        if network is None:
            msg = f"{element.name!r} declares no IP prefix, so it is not a subnet"
            raise NotASubnetError(msg)
        return Subnet(element=element, network=network, used=0)

    def _occupancy(self, subnet: Subnet, assignments: tuple[Assignment, ...]) -> Subnet:
        used = sum(
            1
            for assignment in assignments
            if assignment.vrf == subnet.network.vrf and subnet.network.holds(assignment.address)
        )
        return Subnet(element=subnet.element, network=subnet.network, used=used)

    async def _with_occupancy(self, element: Element) -> Subnet:
        return self._occupancy(self._as_subnet(element), await self._assignments())

    async def _write_address(
        self,
        element: Element,
        address: IpAddress,
        vrf: str,
        subnets: tuple[Subnet, ...],
    ) -> Assignment:
        """Write the two properties, keeping every attribute they do not own."""
        properties = {
            **dict(element.properties),
            ADDRESS_PROPERTY: str(address),
            VRF_PROPERTY: vrf,
        }
        updated = await self._architecture.update_element(element.id, properties=properties)
        logger.info(
            "%s assigned to %r in VRF %r",
            address,
            updated.name,
            vrf,
            extra={
                "action": "address_assigned",
                "address": str(address),
                "vrf": vrf,
                "element_id": str(element.id),
            },
        )
        return self._assignment(updated, address, vrf, subnets)


def _listing_order(subnet: Subnet) -> tuple[str, int, int, int]:
    """Scope, then family, then address, then the narrower prefix last.

    Family before address because an IPv4 and an IPv6 network are not orderable
    against each other: sorting on the networks themselves raised `TypeError`
    for every scope holding both, which is every dual-stack one.
    """
    prefix = subnet.network.prefix
    return (subnet.network.vrf, prefix.version, int(prefix.network_address), prefix.prefixlen)
