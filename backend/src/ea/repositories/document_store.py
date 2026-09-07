"""The PostgreSQL implementation of `DocumentRepository`.

It takes the *session factory* rather than a session, exactly as its Neo4j
neighbour takes the driver rather than a session: both are built once for the
process, and both open one unit of work per call. Every use case here is a
single write, so a session per call is a transaction per use case — the rule
`CLAUDE.md` states. A use case that ever spans two writes takes an
`AsyncSession` argument instead, and `api.dependencies.get_session` becomes the
seam it was built to be.

Every value is bound: SQLAlchemy constructs compile to parameters, and no
statement here is assembled from a string.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.document import ElementDocument
from ea.domain.documents import Document, DocumentSummary
from ea.domain.errors import DocumentNotFoundError, DuplicateDocumentError

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _rows_affected(result: object) -> int:
    """How many rows a DELETE touched.

    `AsyncSession.execute` is typed as returning a `Result`, which declares no
    `rowcount`; a DML statement always yields a `CursorResult`, which does. The
    cast states that rather than letting a `type: ignore` hide it.
    """
    return cast("CursorResult[Any]", result).rowcount


def _to_document(row: ElementDocument) -> Document:
    return Document(
        id=row.id,
        element_id=row.element_id,
        filename=row.filename,
        content=row.content,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class PostgresDocumentRepository:
    """Markdown documents, stored as text beside the graph they describe."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, document: Document) -> Document:
        """Store a new document, translating the unique constraint into a rule.

        The constraint rather than "look first, then insert": the check and the
        insert are two statements, and between them another upload can take the
        name. `IntegrityError` is the only race-free answer to the question.
        """
        row = ElementDocument(
            id=document.id,
            element_id=document.element_id,
            filename=document.filename,
            content=document.content,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )
        try:
            async with self._sessions.begin() as session:
                session.add(row)
        except IntegrityError as error:
            msg = f"{document.filename!r} is already attached to this element"
            raise DuplicateDocumentError(msg) from error
        return document

    async def get(self, document_id: UUID) -> Document | None:
        async with self._sessions() as session:
            row = await session.get(ElementDocument, document_id)
            return _to_document(row) if row is not None else None

    async def list_for_element(self, element_id: UUID) -> tuple[DocumentSummary, ...]:
        """The file names attached to an element — never their content.

        `octet_length` is computed by the server so that listing ten documents
        of a megabyte each costs ten integers rather than ten megabytes. It is
        also why the size is not a stored column: derived here, it cannot drift
        away from the text it measures.
        """
        statement = (
            select(
                ElementDocument.id,
                ElementDocument.element_id,
                ElementDocument.filename,
                func.octet_length(ElementDocument.content),
                ElementDocument.created_at,
                ElementDocument.updated_at,
            )
            .where(ElementDocument.element_id == element_id)
            .order_by(ElementDocument.created_at, ElementDocument.filename)
        )
        async with self._sessions() as session:
            rows = await session.execute(statement)
            return tuple(
                DocumentSummary(
                    id=row[0],
                    element_id=row[1],
                    filename=row[2],
                    byte_size=row[3],
                    created_at=row[4],
                    updated_at=row[5],
                )
                for row in rows
            )

    async def replace(self, document: Document) -> Document:
        """Overwrite a stored document with a new revision of the same file."""
        async with self._sessions.begin() as session:
            row = await session.get(ElementDocument, document.id)
            if row is None:
                # Only reachable if the document was deleted between the
                # service reading it and this write — a race, not a bug.
                msg = f"no document with id {document.id}"
                raise DocumentNotFoundError(msg)
            row.content = document.content
            row.updated_at = document.updated_at
        return document

    async def delete(self, document_id: UUID) -> bool:
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementDocument).where(ElementDocument.id == document_id)
            )
        return bool(_rows_affected(deleted))

    async def discard_for_element(self, element_id: UUID) -> int:
        """The cascade PostgreSQL cannot declare, because the element is a node.

        Called when an element is deleted from the graph — see
        `ArchitectureService.delete_element` and docs/adr/0017.
        """
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementDocument).where(ElementDocument.element_id == element_id)
            )
        return _rows_affected(deleted)
