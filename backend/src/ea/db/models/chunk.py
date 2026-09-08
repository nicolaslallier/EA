"""The table holding the passages a document was cut into, and their vectors.

The second table of the relational store, and the first one that can do what
`element_documents` could not: **carry a foreign key**. A document is a row
here, not a node in Neo4j, so a passage can point at the document it came from
and let PostgreSQL delete it when that document goes. The cascade
`ArchitectureService` has to perform by hand for documents is, for passages,
one line of DDL — see docs/adr/0019.

Three columns are decisions.

**`embedding` is `vector(1024)`, a fixed width.** pgvector indexes a column of
one declared dimension; the number is `EMBEDDING_DIMENSIONS`, imported rather
than typed again, so the settings, this table and the migration cannot disagree
about it.

**`model` is stored beside every vector.** Cosine distance between vectors from
two different models is a number that means nothing. Filtering searches on the
model is what makes a half-finished reindex return too little — visibly — and
never something plausible and wrong.

**`heading_path` is an array and not a joined string.** It is rendered as
`a.md > B > C` for a reader, but a heading is free to contain that separator,
and a path that cannot be taken apart again is a path that has been lost.
"""

from __future__ import annotations

from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base
from ea.domain.search import EMBEDDING_DIMENSIONS

#: A model name is an identifier like `bge-m3` or `text-embedding-3-small`,
#: not prose. The bound is generous and exists so the column is not `TEXT`.
MAX_MODEL_NAME_LENGTH = 100


class DocumentChunk(Base):
    """One passage of one document, with the vector it is retrieved by."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        # A document's passages are numbered in reading order, and a document
        # holds each number once: re-indexing replaces the set, it never adds a
        # second copy of it.
        UniqueConstraint("document_id", "ordinal"),
        # The approximate-nearest-neighbour index. HNSW rather than IVFFlat: it
        # needs no training pass over an already-populated table, which matters
        # for a corpus that starts empty and grows one upload at a time.
        # `vector_cosine_ops` because the distance the repository orders by is
        # the cosine one — an index built for another operator is simply not
        # used, silently, and the search degrades to a full scan.
        Index(
            "ix_document_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    # The foreign key `element_documents.element_id` could not have. Deleting a
    # document — or the element that carries it, which deletes its documents —
    # takes its passages with it, in the database rather than in a service.
    document_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("element_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised from the document so that scoping a search to one element is
    # a predicate on this table rather than on the join — which is what lets
    # the vector index do the filtering. It cannot drift: nothing moves a
    # document from one element to another, and no entry point offers to.
    element_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(String(MAX_MODEL_NAME_LENGTH), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    # There is deliberately no timestamp. A passage is derived data: it is
    # exactly as old as the revision it was cut from, which `element_documents`
    # already records, and a column saying otherwise would only ever be a
    # second, disagreeing answer to the same question.
