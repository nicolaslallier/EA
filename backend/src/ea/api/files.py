"""The endpoints over the file bucket — see docs/adr/0036.

Keys hold `/`, so they travel in the query string rather than the path. An
upload is read under a cap, one byte past the limit, like `api/documents.py`.

A download is always an attachment, with `nosniff` and a sandboxing CSP: the
bucket takes any file, and an HTML or SVG file served inline on this origin
would run with the session of whoever opened it.
"""

from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from ea.api.dependencies import Files
from ea.api.schemas import (
    ErrorResponse,
    FileDescription,
    FileDetailsWrite,
    FileKey,
    FileListingRead,
    FilePrefix,
    FileRead,
    FileTitle,
    ReconcileRead,
)
from ea.domain.files import MAX_FILE_BYTES, FileDetails, guess_content_type, join_key

router = APIRouter(tags=["files"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    **NOT_FOUND,
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}

Upload = Annotated[UploadFile, File(description="Any file, at most 50 MB.")]
KeyQuery = Annotated[FileKey, Query()]
PrefixQuery = Annotated[FilePrefix, Query()]

_READ_LIMIT = MAX_FILE_BYTES + 1


def attachment(name: str) -> str:
    """RFC 6266 with RFC 5987 encoding: any name, no header injection."""
    return f"attachment; filename*=UTF-8''{quote(name, safe='')}"


@router.get("/files", response_model=FileListingRead, responses=NOT_FOUND)
async def list_files(files: Files, prefix: PrefixQuery = "") -> FileListingRead:
    """One folder of the bucket: its sub-folders, then its files."""
    return FileListingRead.of(await files.list_folder(prefix))


@router.post(
    "/files", response_model=FileRead, status_code=status.HTTP_201_CREATED, responses=REJECTED
)
async def upload_file(
    files: Files,
    file: Upload,
    prefix: Annotated[FilePrefix, Form()] = "",
    overwrite: Annotated[bool, Form()] = False,
    title: Annotated[FileTitle, Form()] = "",
    description: Annotated[FileDescription, Form()] = "",
    tags: Annotated[list[str], Form()] = [],  # noqa: B006 - FastAPI reads the default, never mutates it
) -> FileRead:
    """Store a file in the folder `prefix`, under its own name.

    A file already there is a 409 unless `overwrite` is true. `title`,
    `description` and `tags` are recorded beside it (docs/adr/0039); leaving
    them out on an overwrite keeps whatever was already written about the file.

    `tags` is a form field repeated once per tag, because a multipart body has
    no arrays — `tags=budget&tags=réseau`.
    """
    key = join_key(prefix, file.filename or "")
    raw = await file.read(_READ_LIMIT)
    stored = await files.upload(
        key,
        raw,
        content_type=file.content_type or guess_content_type(key),
        overwrite=overwrite,
        details=FileDetails(title=title, description=description, tags=tuple(tags)),
    )
    return FileRead.of(stored)


@router.get("/files/metadata", response_model=FileRead, responses=NOT_FOUND)
async def read_file_metadata(files: Files, key: KeyQuery) -> FileRead:
    """One file of the bucket, and what the catalogue knows about it."""
    return FileRead.of(await files.describe(key))


@router.put("/files/metadata", response_model=FileRead, responses=REJECTED)
async def describe_file(files: Files, written: FileDetailsWrite, key: KeyQuery) -> FileRead:
    """Replace what is written about a file: its title, description and tags.

    A `PUT` and not a `PATCH`, because the body replaces the whole description
    rather than merging into it — see `FileDetailsWrite`.
    """
    return FileRead.of(await files.set_details(key, written.details()))


@router.post("/files/reconcile", response_model=ReconcileRead, responses=NOT_FOUND)
async def reconcile_files(files: Files) -> ReconcileRead:
    """Make the catalogue agree with the bucket, and say what changed.

    The catch-up for a file written or removed by a door that is not this API.
    Harmless to run twice, and it never touches what a person wrote.
    """
    report = await files.reconcile()
    return ReconcileRead(recorded=report.recorded, forgotten=report.forgotten)


@router.get(
    "/files/content",
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
            "description": "The file's bytes, as an attachment.",
        },
        **NOT_FOUND,
    },
)
async def download_file(files: Files, key: KeyQuery) -> StreamingResponse:
    """The bytes of one file."""
    stored, chunks = await files.open(key)
    return StreamingResponse(
        chunks,
        media_type=stored.content_type,
        headers={
            "Content-Disposition": attachment(stored.name),
            "Content-Length": str(stored.size),
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.delete("/files", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND)
async def delete_file(files: Files, key: KeyQuery) -> Response:
    await files.delete(key)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
