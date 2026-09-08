"""The IP address management endpoints.

They read and write the same graph `/elements` serves: a subnet is a
`communication_network` element and an address is a property of the element
answering on it (`docs/adr/0020`). So these routes add no store and no second
truth — deleting a host through `DELETE /elements/{id}` takes its address with
it, because the address *was* the host.

Two of them are the point of the whole section. `GET /ipam/addresses/{address}`
answers "this address, that is what?" with the element **and its links**, and
`POST /ipam/subnets/{id}/allocate` answers "give me a free one", which is the
question a spreadsheet can never answer twice in a row without a collision.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Path, Query, Response, status

from ea.api.dependencies import Ipam
from ea.api.schemas import (
    AddressAllocate,
    AddressAssign,
    AddressLocationRead,
    AddressRead,
    ErrorResponse,
    SubnetCreate,
    SubnetDetailRead,
    SubnetRead,
)
from ea.domain.ipam import DEFAULT_VRF

router = APIRouter(prefix="/ipam", tags=["ipam"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}
REFUSED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}

Scope = Annotated[str | None, Query(max_length=64, description="Narrow to one routing scope.")]


# --- Subnets --------------------------------------------------------------


@router.post(
    "/subnets",
    response_model=SubnetRead,
    status_code=status.HTTP_201_CREATED,
    responses=REFUSED,
)
async def declare_subnet(payload: SubnetCreate, service: Ipam) -> SubnetRead:
    """Declare a subnet. A prefix may be declared once per routing scope."""
    return SubnetRead.of(
        await service.declare_network(
            name=payload.name,
            cidr=payload.cidr,
            vrf=payload.vrf,
            reserved=payload.reserved,
            description=payload.description,
        )
    )


@router.get("/subnets", response_model=list[SubnetRead])
async def list_subnets(service: Ipam, vrf: Scope = None) -> list[SubnetRead]:
    """Every declared subnet, with how full each one is."""
    return [SubnetRead.of(subnet) for subnet in await service.list_networks(vrf=vrf)]


@router.get("/subnets/{subnet_id}", response_model=SubnetDetailRead, responses=REFUSED)
async def read_subnet(subnet_id: UUID, service: Ipam) -> SubnetDetailRead:
    """One subnet, its occupants, and the address it would hand out next.

    `subnet_id` is an element id: a subnet *is* an element of the catalogue.
    It is named for the role it plays here, because the allocation route below
    takes two element ids and they are not interchangeable.
    """
    return SubnetDetailRead.of(await service.read_network(subnet_id))


@router.post(
    "/subnets/{subnet_id}/allocate",
    response_model=AddressRead,
    status_code=status.HTTP_201_CREATED,
    responses=REFUSED,
)
async def allocate_address(subnet_id: UUID, payload: AddressAllocate, service: Ipam) -> AddressRead:
    """Give an element the first address this subnet has free.

    Two element ids: the subnet in the path, the element receiving the address
    in the body.
    """
    return AddressRead.of(await service.allocate_next(subnet_id, payload.element_id))


# --- Addresses ------------------------------------------------------------


@router.get("/addresses", response_model=list[AddressRead])
async def list_addresses(
    service: Ipam,
    vrf: Scope = None,
    within: Annotated[
        str | None,
        Query(max_length=64, description="Only addresses inside this prefix, e.g. `10.0.1.0/26`."),
    ] = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
) -> list[AddressRead]:
    """The inventory: every assigned address, in address order."""
    return [
        AddressRead.of(assignment)
        for assignment in await service.list_addresses(vrf=vrf, within=within, search=search)
    ]


@router.post(
    "/addresses",
    response_model=AddressRead,
    status_code=status.HTTP_201_CREATED,
    responses=REFUSED,
)
async def assign_address(payload: AddressAssign, service: Ipam) -> AddressRead:
    """Give one element one address, if every rule allows it."""
    return AddressRead.of(
        await service.assign_address(payload.element_id, payload.address, vrf=payload.vrf)
    )


@router.get(
    "/addresses/{address}",
    response_model=AddressLocationRead,
    responses=REFUSED,
)
async def locate_address(
    address: Annotated[str, Path(max_length=64, description="An IPv4 or IPv6 address.")],
    service: Ipam,
    vrf: Annotated[str, Query(max_length=64)] = DEFAULT_VRF,
) -> AddressLocationRead:
    """What answers on this address, and what that thing is wired to."""
    return AddressLocationRead.of(await service.locate(address, vrf=vrf))


@router.delete(
    "/elements/{element_id}/address",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def release_address(element_id: UUID, service: Ipam) -> Response:
    """Take an element's address back. The element itself is left alone."""
    await service.release_address(element_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
