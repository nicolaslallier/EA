"""Turning a stored document into passages a question can find.

Two pure things sit on either side of this: `domain/chunking.py`, which decides
where a document is cut and what trail of headings each piece carries, and an
`Embedder`, which turns text into a vector. This is the seam, and the one thing
it decides is **what text is actually embedded** — the trail, then the passage:

    runbook.md > Incidents > Escalation

    Call the level-2 on-call after two failed restarts.

The stored text is the passage alone. The trail is context the model needs and
a reader already has, so showing it back inside the passage would be saying it
twice. Both come out of the same function, which is why a hit renders its trail
exactly as it was embedded — see `Passage.trail`.

The whole document goes in one call. Batching is the client's business
(`repositories/embeddings.py` knows the service's limits); an indexer that
called once per passage would defeat it, and a runbook of forty sections would
become forty round trips.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ea.domain.chunking import embedding_input, split_markdown
from ea.domain.search import EmbeddedChunk

if TYPE_CHECKING:
    from ea.domain.documents import Document
    from ea.domain.ports import Embedder


class DocumentIndexer:
    """Cuts documents into passages, embeds them, and embeds the questions.

    It holds no store: it hands the passages back and `DocumentService` writes
    them with the document, in one transaction. That is what keeps a document
    from ever being stored under text its index does not describe.
    """

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder

    @property
    def model(self) -> str:
        """The name written beside every vector, and the filter every search runs.

        Read off the embedder rather than configured twice: a stored passage
        and the query it is compared against have to agree on this string, and
        two places to type it is one place to get it wrong.
        """
        return self._embedder.model

    async def passages_of(self, document: Document) -> tuple[EmbeddedChunk, ...]:
        """The document, cut up and embedded, ready to be stored."""
        chunks = split_markdown(document.content)
        if not chunks:
            return ()
        vectors = await self._embedder.embed_passages(
            [embedding_input(document.filename, chunk) for chunk in chunks]
        )
        return tuple(
            EmbeddedChunk(
                ordinal=chunk.ordinal,
                heading_path=chunk.heading_path,
                text=chunk.text,
                embedding=vector,
                model=self.model,
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        )

    async def embed_query(self, question: str) -> tuple[float, ...]:
        """One question, embedded the way questions are rather than passages."""
        return await self._embedder.embed_query(question)
