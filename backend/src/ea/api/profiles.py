"""The endpoints of profiles and stereotypes — reference data, extending no ArchiMate type.

A stereotype is a label on an element of one base type (docs/adr/0070). The values
stay in `elements.properties`; these routes only define the labels and lay them on
elements. Import and export are versioned documents: the import is validated by the
very constructors a manual write uses.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request, Response, status
from pydantic import ValidationError

from ea.api.dependencies import Profiles
from ea.api.schemas import (
    MAX_PROFILE_DOCUMENT_BYTES,
    ElementRead,
    ElementStereotypesSet,
    ErrorResponse,
    ProfileCreate,
    ProfileDocument,
    ProfileRead,
    ProfileUpdate,
    StereotypeRead,
    StereotypeWrite,
)

router = APIRouter(tags=["profiles"])

_TOO_LARGE = "a profile document is at most 256 KB"

_ERROR = {"model": ErrorResponse}
NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {status.HTTP_404_NOT_FOUND: _ERROR}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: _ERROR,
    status.HTTP_409_CONFLICT: _ERROR,
    status.HTTP_422_UNPROCESSABLE_CONTENT: _ERROR,
}


async def _read_bounded(request: Request) -> bytes:
    """The body, read chunk by chunk and abandoned the moment it passes the ceiling.

    The bound is laid *before* the whole body is held: a declared length that is too
    large is refused unread, and a stream that exceeds the ceiling stops being read.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_PROFILE_DOCUMENT_BYTES:
        raise ValueError(_TOO_LARGE)
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > MAX_PROFILE_DOCUMENT_BYTES:
            raise ValueError(_TOO_LARGE)
    return bytes(received)


@router.get("/profiles", response_model=list[ProfileRead])
async def list_profiles(profiles: Profiles) -> list[ProfileRead]:
    """Every profile with its stereotypes, sorted by name."""
    return [ProfileRead.of(p) for p in await profiles.list_profiles()]


@router.post(
    "/profiles", response_model=ProfileRead, status_code=status.HTTP_201_CREATED, responses=REJECTED
)
async def create_profile(body: ProfileCreate, profiles: Profiles) -> ProfileRead:
    return ProfileRead.of(
        await profiles.create_profile(name=body.name, description=body.description)
    )


@router.post(
    "/profiles/import",
    response_model=ProfileRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": {"$ref": "#/components/schemas/ProfileDocument"}}
            },
        }
    },
)
async def import_profile(request: Request, profiles: Profiles) -> ProfileRead:
    """Create a profile from an exported document (at most 256 KB, `profile_format: 1`).

    The body is read and measured here rather than parsed by FastAPI, so the
    size ceiling is checked on the bytes and a malformed document is a typed
    422 rather than a validation report naming the server's models.
    """
    raw = await _read_bounded(request)
    try:
        document = ProfileDocument.model_validate_json(raw)
    except ValidationError as error:
        first = error.errors()[0]
        place = ".".join(str(part) for part in first["loc"]) or "document"
        msg = f"invalid profile document: {place}: {first['msg']}"
        raise ValueError(msg) from None
    return ProfileRead.of(await profiles.import_profile(document.model_dump(mode="python")))


@router.get("/profiles/{profile_id}", response_model=ProfileRead, responses=NOT_FOUND)
async def read_profile(profile_id: UUID, profiles: Profiles) -> ProfileRead:
    return ProfileRead.of(await profiles.get_profile(profile_id))


@router.put("/profiles/{profile_id}", response_model=ProfileRead, responses=REJECTED)
async def update_profile(profile_id: UUID, body: ProfileUpdate, profiles: Profiles) -> ProfileRead:
    return ProfileRead.of(
        await profiles.update_profile(profile_id, name=body.name, description=body.description)
    )


@router.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT, responses=REJECTED)
async def delete_profile(profile_id: UUID, profiles: Profiles) -> Response:
    """Delete a profile — refused while one of its stereotypes is applied to an element."""
    await profiles.delete_profile(profile_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/profiles/{profile_id}/export", response_model=ProfileDocument, responses=NOT_FOUND)
async def export_profile(profile_id: UUID, profiles: Profiles) -> ProfileDocument:
    """The profile as a versioned document that `POST /profiles/import` accepts."""
    return ProfileDocument.model_validate(await profiles.export_profile(profile_id))


@router.post(
    "/profiles/{profile_id}/stereotypes",
    response_model=StereotypeRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def add_stereotype(
    profile_id: UUID, body: StereotypeWrite, profiles: Profiles
) -> StereotypeRead:
    return StereotypeRead.of(
        await profiles.add_stereotype(
            profile_id,
            name=body.name,
            base_type=body.base_type,
            colour=body.colour,
            icon_key=body.icon_key,
            properties=[p.declared() for p in body.properties],
        )
    )


@router.get("/stereotypes", response_model=list[StereotypeRead])
async def list_stereotypes(profiles: Profiles) -> list[StereotypeRead]:
    """Every stereotype, flat — what the SPA needs to draw and to offer them."""
    return [StereotypeRead.of(s) for s in await profiles.list_stereotypes()]


@router.put("/stereotypes/{stereotype_id}", response_model=StereotypeRead, responses=REJECTED)
async def replace_stereotype(
    stereotype_id: UUID, body: StereotypeWrite, profiles: Profiles
) -> StereotypeRead:
    return StereotypeRead.of(
        await profiles.replace_stereotype(
            stereotype_id,
            name=body.name,
            base_type=body.base_type,
            colour=body.colour,
            icon_key=body.icon_key,
            properties=[p.declared() for p in body.properties],
        )
    )


@router.delete(
    "/stereotypes/{stereotype_id}", status_code=status.HTTP_204_NO_CONTENT, responses=REJECTED
)
async def delete_stereotype(stereotype_id: UUID, profiles: Profiles) -> Response:
    """Delete a stereotype — refused, with the count, while elements carry it."""
    await profiles.delete_stereotype(stereotype_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/elements/{element_id}/stereotypes", response_model=ElementRead, responses=REJECTED)
async def set_element_stereotypes(
    element_id: UUID, body: ElementStereotypesSet, profiles: Profiles
) -> ElementRead:
    """Replace the set of stereotypes an element carries.

    A stereotype of another base type is a 422, and so is a set of stereotypes
    declaring one key twice or a stored value that breaks a declared format.
    """
    return ElementRead.of(await profiles.apply_stereotypes(element_id, body.stereotype_ids))
