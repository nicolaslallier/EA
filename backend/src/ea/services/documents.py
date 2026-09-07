"""Use cases over the markdown attached to architecture elements.

The rule this layer owns is the one no single object can check: **an element
must exist in the graph before a file can be attached to it**. The element is a
node in Neo4j and the document a row in PostgreSQL (docs/adr/0017), so no
foreign key states it — this service does, by asking the architecture service
first. An adapter that skipped it would write documents onto elements nobody
can reach.

The other half of that missing foreign key is the cascade, and it lives where
the deletion does: `ArchitectureService.delete_element` discards the documents
through the narrow `ElementAttachments` port.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.documents import Document, DocumentSummary, clean_filename, decode_markdown
from ea.domain.errors import DocumentNotFoundError

if TYPE_CHECKING:
    from ea.domain.ports import DocumentRepository
    from ea.services.architecture import ArchitectureService

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class DocumentService:
    """The single entry point `api/` uses to attach, read and remove markdown."""

    def __init__(
        self,
        repository: DocumentRepository,
        architecture: ArchitectureService,
        *,
        clock: Clock = _utc_now,
    ) -> None:
        self._repository = repository
        self._architecture = architecture
        self._now = clock

    async def attach(self, element_id: UUID, *, filename: str, raw: bytes) -> Document:
        """Attach an uploaded markdown file to an element.

        `raw` rather than text: decoding is a rule about what a document *is*
        (`domain/documents.py`), so every adapter that holds an upload gets the
        same answer for a file that is not UTF-8 — and the same message.
        """
        return await self.attach_text(element_id, filename=filename, content=decode_markdown(raw))

    async def attach_text(self, element_id: UUID, *, filename: str, content: str) -> Document:
        """Attach markdown an adapter already holds as text.

        The MCP tools land here: an agent composes a document, it never
        uploads one, and encoding that text only to decode it again would let
        `decode_markdown` refuse — for not being UTF-8 — a string that by
        construction is. Everything a document must satisfy once it *is* text
        is checked below this line, in `Document.create`, so the two entry
        points differ by exactly the decoding step and nothing else.
        """
        await self._architecture.get_element(element_id)
        document = Document.create(
            element_id=element_id,
            filename=filename,
            content=content,
            now=self._now(),
        )
        return await self._repository.add(document)

    async def get(self, document_id: UUID) -> Document:
        """One document, content included, or a clear statement that it is gone."""
        document = await self._repository.get(document_id)
        if document is None:
            msg = f"no document with id {document_id}"
            raise DocumentNotFoundError(msg)
        return document

    async def list_for_element(self, element_id: UUID) -> tuple[DocumentSummary, ...]:
        """What is attached to an element, without loading a single body.

        The element is read first so that asking about one that does not exist
        is a 404 rather than an empty list — an empty list would mean "this
        element has no documents", which is a different answer.
        """
        await self._architecture.get_element(element_id)
        return await self._repository.list_for_element(element_id)

    async def revise(self, document_id: UUID, *, filename: str, raw: bytes) -> Document:
        """Replace the content of a stored document with a new upload.

        The file name has to match: a document is identified to a human by its
        name, and quietly overwriting `runbook.md` with the contents of
        `notes.md` under the old name is how a reader ends up misled.
        """
        return await self.revise_text(document_id, filename=filename, content=decode_markdown(raw))

    async def revise_text(self, document_id: UUID, *, filename: str, content: str) -> Document:
        """Replace a stored document with markdown an adapter holds as text.

        The counterpart of `attach_text`, and it keeps the name check: an agent
        rewriting `runbook.md` has to say which document it is rewriting, so a
        tool call aimed at the wrong id is refused rather than silently
        replacing the wrong file.
        """
        current = await self.get(document_id)
        offered = clean_filename(filename)
        if offered != current.filename:
            msg = f"this document is {current.filename!r}, and the file offered is {offered!r}"
            raise ValueError(msg)
        revised = current.revise(content, now=self._now())
        return await self._repository.replace(revised)

    async def discard(self, document_id: UUID) -> None:
        if not await self._repository.delete(document_id):
            msg = f"no document with id {document_id}"
            raise DocumentNotFoundError(msg)
