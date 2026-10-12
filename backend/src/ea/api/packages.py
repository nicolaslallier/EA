"""The endpoints of the classification tree, and the move of elements into it.

A package files elements and diagrams; it carries no relationship and never
appears in a traversal. `POST /elements/move` lives here and not in
`architecture.py` because it is the tree's operation: it changes where an
element is filed, never what the element says.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from ea.api.dependencies import Packages
from ea.api.schemas import (
    ErrorResponse,
    MoveElements,
    PackageCreate,
    PackageRead,
    PackageTarget,
    PackageUpdate,
    PackageVersionRead,
    PublishPackage,
    PublishResult,
    ReopenPackage,
    package_target,
)
from ea.domain.packages import UNSET, Unset

router = APIRouter(tags=["packages"])

_ERROR = {"model": ErrorResponse}
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


@router.get("/packages", response_model=list[PackageRead])
async def list_packages(packages: Packages) -> list[PackageRead]:
    """The whole tree, flat: each package names its parent, so the client builds the tree."""
    return [PackageRead.of(summary) for summary in await packages.list_packages()]


@router.post(
    "/packages", response_model=PackageRead, status_code=status.HTTP_201_CREATED, responses=REJECTED
)
async def create_package(body: PackageCreate, packages: Packages) -> PackageRead:
    created = await packages.create(
        name=body.name, description=body.description, parent_id=body.parent_id
    )
    return PackageRead.of(created)


@router.get("/packages/{package_id}", response_model=PackageRead, responses=NOT_FOUND)
async def read_package(package_id: UUID, packages: Packages) -> PackageRead:
    return PackageRead.of(await packages.get(package_id))


@router.patch("/packages/{package_id}", response_model=PackageRead, responses=REJECTED)
async def update_package(package_id: UUID, body: PackageUpdate, packages: Packages) -> PackageRead:
    """Rename, describe or move. `parent_id: null` puts the package at the root; omit it to keep."""
    parent = body.parent_id if "parent_id" in body.model_fields_set else UNSET
    updated = await packages.update(
        package_id, name=body.name, description=body.description, parent_id=parent
    )
    return PackageRead.of(updated)


@router.delete("/packages/{package_id}", status_code=status.HTTP_204_NO_CONTENT, responses=REJECTED)
async def delete_package(
    package_id: UUID,
    packages: Packages,
    move_contents_to: Annotated[PackageTarget | None, Query()] = None,
) -> Response:
    """Delete a package — never what it holds.

    A package that still holds anything is a 409 `package_not_empty`; pass
    `move_contents_to` (a package id, or `root`) to move its direct contents
    there in the same transaction.
    """
    target: UUID | Unset | None = UNSET
    if move_contents_to is not None:
        target = package_target(move_contents_to)
    await packages.delete(package_id, move_contents_to=target)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/packages/{package_id}/publish", response_model=PublishResult, responses=REJECTED)
async def publish_package(
    package_id: UUID, body: PublishPackage, packages: Packages
) -> PublishResult:
    """Freeze the package and everything below it under a new version (an approver's gesture).

    A preview unless `dry_run` is false: the version it would take and what it would freeze.
    A MAJOR names its ADR. Nothing below a published package can be written until it is reopened.
    """
    return PublishResult.of(
        await packages.publish(
            package_id, bump=body.bump, notes=body.notes, adr=body.adr, dry_run=body.dry_run
        )
    )


@router.post("/packages/{package_id}/reopen", response_model=PackageVersionRead, responses=REJECTED)
async def reopen_package(
    package_id: UUID, body: ReopenPackage, packages: Packages
) -> PackageVersionRead:
    """Close the version in force, with a reason (an approver's gesture)."""
    return PackageVersionRead.of(await packages.reopen(package_id, reason=body.reason))


@router.get(
    "/packages/{package_id}/versions",
    response_model=list[PackageVersionRead],
    responses=NOT_FOUND,
)
async def list_package_versions(package_id: UUID, packages: Packages) -> list[PackageVersionRead]:
    """Every version the package was published under, the latest first."""
    return [PackageVersionRead.of(v) for v in await packages.versions(package_id)]


@router.post(
    "/elements/move", status_code=status.HTTP_204_NO_CONTENT, responses=REJECTED, tags=["packages"]
)
async def move_elements(body: MoveElements, packages: Packages) -> Response:
    """File up to 100 elements in a package, or at the root; all or none."""
    await packages.move_elements(body.element_ids, body.package_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
