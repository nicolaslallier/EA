"""The endpoints that attach markdown files to architecture elements.

An upload is `multipart/form-data`, which is the one place this API takes
something other than JSON: a markdown file is a file, and asking a client to
base64 it into a JSON field would double its size and lose its name.

The bytes are read under a cap rather than in full. `UploadFile` spools a large
body to disk, so an unbounded `read()` would happily materialise whatever was
sent; reading one byte past the limit is enough to know the file is too big and
never holds more than that.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Response, UploadFile, status

from ea.api.dependencies import Documents
from ea.api.schemas import DocumentRead, DocumentSummaryRead, ErrorResponse
from ea.domain.documents import MAX_DOCUMENT_BYTES

router = APIRouter(tags=["documents"])

NOT_FOUND: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}
}
REJECTED: dict[int | str, dict[str, type[ErrorResponse]]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
}

#: `Annotated` rather than a default value: `file: UploadFile = File(...)`
#: evaluates a call in a signature, which `ruff` flags (B008) and which
#: FastAPI itself now documents as the older spelling.
Markdown = Annotated[UploadFile, File(description="A UTF-8 markdown file, at most 1 MB.")]

#: One byte past the limit: enough to prove the file is too large, and the most
#: this process ever holds of a body it is going to refuse.
_READ_LIMIT = MAX_DOCUMENT_BYTES + 1


async def _uploaded(file: UploadFile) -> tuple[str, bytes]:
    """The name and the bytes of an upload, both still unvalidated.

    A client is free to send no filename at all; the empty string that arrives
    then is refused by `clean_filename` with the same message a `.txt` gets,
    rather than by an `AttributeError` here.
    """
    return file.filename or "", await file.read(_READ_LIMIT)


@router.post(
    "/elements/{element_id}/documents",
    response_model=DocumentRead,
    status_code=status.HTTP_201_CREATED,
    responses=REJECTED,
)
async def attach_document(
    element_id: UUID,
    documents: Documents,
    file: Markdown,
) -> DocumentRead:
    """Attach a markdown file to an element.

    The element must already exist, and it may hold only one document under a
    given file name — uploading the same name twice is a 409, and revising it
    is `PUT /documents/{document_id}`.
    """
    filename, raw = await _uploaded(file)
    return DocumentRead.of(await documents.attach(element_id, filename=filename, raw=raw))


@router.get(
    "/elements/{element_id}/documents",
    response_model=list[DocumentSummaryRead],
    responses=NOT_FOUND,
)
async def list_element_documents(
    element_id: UUID, documents: Documents
) -> list[DocumentSummaryRead]:
    """What is attached to an element: the names and the sizes, not the text.

    A 404 rather than an empty list when the element is unknown — an empty list
    is the answer to a different question.
    """
    return [
        DocumentSummaryRead.of(summary) for summary in await documents.list_for_element(element_id)
    ]


@router.get("/documents/{document_id}", response_model=DocumentRead, responses=NOT_FOUND)
async def read_document(document_id: UUID, documents: Documents) -> DocumentRead:
    """One document with its markdown, as text."""
    return DocumentRead.of(await documents.get(document_id))


@router.put("/documents/{document_id}", response_model=DocumentRead, responses=REJECTED)
async def revise_document(
    document_id: UUID,
    documents: Documents,
    file: Markdown,
) -> DocumentRead:
    """Replace the content of a document with a newer version of the same file.

    The uploaded name must match the stored one: a document is known to its
    readers by its name, so overwriting `runbook.md` with something else under
    that name is refused rather than silently accepted.
    """
    filename, raw = await _uploaded(file)
    return DocumentRead.of(await documents.revise(document_id, filename=filename, raw=raw))


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
)
async def discard_document(document_id: UUID, documents: Documents) -> Response:
    await documents.discard(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
