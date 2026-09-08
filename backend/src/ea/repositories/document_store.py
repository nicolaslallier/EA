"""The PostgreSQL implementation of `DocumentRepository`.

It takes the *session factory* rather than a session, exactly as its Neo4j
neighbour takes the driver rather than a session: both are built once for the
process, and both open one unit of work per call. Every use case here is a
single write, so a session per call is a transaction per use case — the rule
`CLAUDE.md` states. A use case that ever spans two writes takes an
`AsyncSession` argument instead, and `api.dependencies.get_session` becomes the
seam it was built to be.

A document and its indexed passages are *two tables and one write*: they go in
together or not at all, which is what keeps the index from claiming a document
the store does not hold, or missing one it does. That is possible here and
nowhere else in this codebase, because both tables are in this database — see
docs/adr/0019. The embedding call that produced the vectors happens before the
transaction opens, deliberately: holding one across a network call to another
service is how a slow embedder becomes a locked table.

Every value is bound: SQLAlchemy constructs compile to parameters, and no
statement here is assembled from a string. The one exception is the raw SQL of
the migration, which is not a runtime value at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError

from ea.db.models.chunk import DocumentChunk
from ea.db.models.document import ElementDocument
from ea.domain.documents import Document, DocumentSummary
from ea.domain.errors import DocumentNotFoundError, DuplicateDocumentError
from ea.domain.search import DEFAULT_SEARCH_LIMIT, EmbeddedChunk, Passage

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _rows_affected(result: object) -> int:
    """How many rows a DELETE touched.

    `AsyncSession.execute` is typed as returning a `Result`, which declares no
    `rowcount`; a DML statement always yields a `CursorResult`, which does. The
    cast states that rather than letting a `type: ignore` hide it.
    """
    return cast("CursorResult[Any]", result).rowcount


def _chunk_rows(document: Document, chunks: Sequence[EmbeddedChunk]) -> list[DocumentChunk]:
    """The rows one document's passages become.

    `element_id` is copied off the document rather than taken from the chunk:
    the chunk describes a piece of text and has no opinion about which element
    carries it, and there is exactly one right answer.
    """
    return [
        DocumentChunk(
            id=uuid4(),
            document_id=document.id,
            element_id=document.element_id,
            ordinal=chunk.ordinal,
            heading_path=list(chunk.heading_path),
            content=chunk.text,
            model=chunk.model,
            embedding=list(chunk.embedding),
        )
        for chunk in chunks
    ]


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

    async def add(self, document: Document, chunks: Sequence[EmbeddedChunk] = ()) -> Document:
        """Store a new document and its passages, in one transaction.

        The unique constraint is what refuses a name the element already has,
        rather than "look first, then insert": the check and the insert are two
        statements, and between them another upload can take the name.
        `IntegrityError` is the only race-free answer to the question.

        `chunks` is empty when the deployment has no index — the document is
        stored all the same, and `make docs-reindex` catches it up later.
        """
        row = ElementDocument(
            id=document.id,
            element_id=document.element_id,
            filename=document.filename,
            content=document.content,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )
        async with self._sessions.begin() as session:
            session.add(row)
            # Flushed before the passages are added, and not left to the unit of
            # work to order: the two mappers are related by a foreign key and by
            # nothing else — there is no `relationship()` between them — so
            # SQLAlchemy is free to emit the chunk inserts first, and does. The
            # passages would then reference a document that is not there yet.
            #
            # It also puts the unique constraint exactly here, which is what
            # lets the refusal below name the right rule rather than whichever
            # constraint happened to fire at commit.
            try:
                await session.flush()
            except IntegrityError as error:
                msg = f"{document.filename!r} is already attached to this element"
                raise DuplicateDocumentError(msg) from error
            session.add_all(_chunk_rows(document, chunks))
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

    async def replace(self, document: Document, chunks: Sequence[EmbeddedChunk] = ()) -> Document:
        """Overwrite a stored document, and its passages, with a new revision.

        The old passages are deleted rather than updated: a revision cuts into
        a different number of pieces at different places, so there is no row to
        match up. Both halves are one transaction, which is what stops a
        revision from ever being searchable under its previous text.
        """
        async with self._sessions.begin() as session:
            row = await session.get(ElementDocument, document.id)
            if row is None:
                # Only reachable if the document was deleted between the
                # service reading it and this write — a race, not a bug.
                msg = f"no document with id {document.id}"
                raise DocumentNotFoundError(msg)
            row.content = document.content
            row.updated_at = document.updated_at
            await session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            session.add_all(_chunk_rows(document, chunks))
        return document

    async def delete(self, document_id: UUID) -> bool:
        """Delete a document. Its passages go with it, by the foreign key."""
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementDocument).where(ElementDocument.id == document_id)
            )
        return bool(_rows_affected(deleted))

    async def discard_for_element(self, element_id: UUID) -> int:
        """The cascade PostgreSQL cannot declare, because the element is a node.

        Called when an element is deleted from the graph — see
        `ArchitectureService.delete_element` and docs/adr/0017. The passages of
        those documents *are* declared, and follow by the foreign key.
        """
        async with self._sessions.begin() as session:
            deleted = await session.execute(
                delete(ElementDocument).where(ElementDocument.element_id == element_id)
            )
        return _rows_affected(deleted)

    async def all_document_ids(self) -> tuple[UUID, ...]:
        """Every document in the store, oldest first — the reindex walks these."""
        statement = select(ElementDocument.id).order_by(ElementDocument.created_at)
        async with self._sessions() as session:
            return tuple((await session.execute(statement)).scalars())

    async def search(
        self,
        embedding: Sequence[float],
        *,
        model: str,
        element_id: UUID | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
    ) -> tuple[Passage, ...]:
        """The passages closest to a query vector, nearest first.

        Cosine distance, because that is the operator class the HNSW index was
        built for: ordering by any other one gives correct answers slowly, by
        scanning the table, and says nothing about having done so. The score
        handed back is `1 - distance`, so that larger is better and the number
        a caller shows a human reads as a similarity.

        The `model` predicate is the point of storing the model at all. It is
        also why a change of embedding model empties the results rather than
        degrading them: nothing matches until the reindex has run.
        """
        distance = DocumentChunk.embedding.cosine_distance(list(embedding)).label("distance")
        statement = (
            select(
                DocumentChunk.document_id,
                DocumentChunk.element_id,
                ElementDocument.filename,
                DocumentChunk.heading_path,
                DocumentChunk.content,
                distance,
            )
            .join(ElementDocument, ElementDocument.id == DocumentChunk.document_id)
            .where(DocumentChunk.model == model)
            .order_by(distance)
            .limit(limit)
        )
        if element_id is not None:
            statement = statement.where(DocumentChunk.element_id == element_id)
        async with self._sessions() as session:
            rows = await session.execute(statement)
            return tuple(
                Passage(
                    document_id=row[0],
                    element_id=row[1],
                    filename=row[2],
                    heading_path=tuple(row[3]),
                    text=row[4],
                    score=1.0 - float(row[5]),
                )
                for row in rows
            )
