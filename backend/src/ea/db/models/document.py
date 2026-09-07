"""The table holding the markdown attached to architecture elements.

The first table of the relational store (docs/adr/0017). Two things about it
are worth stating, because neither is obvious from the columns.

**`content` is `Text`, not `LargeBinary`.** Markdown is prose: it is read,
searched and diffed by people. `bytea` would make every one of those a decoding
step, and would let a file that is not text in. The domain refuses anything
that is not UTF-8 before it reaches here — see `domain/documents.py`.

**`element_id` has no foreign key, and cannot have one.** The element it names
is a `:Element` node in Neo4j (docs/adr/0004); PostgreSQL has nothing to point
at. The cascade PostgreSQL would have done for free is therefore explicit, in
`ArchitectureService.delete_element` through the `ElementAttachments` port. The
index is what makes "the documents of this element" a cheap question.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base
from ea.domain.documents import MAX_FILENAME_LENGTH


class ElementDocument(Base):
    """One markdown file, attached to one element of the architecture graph."""

    __tablename__ = "element_documents"
    __table_args__ = (
        # One file name per element: uploading `README.md` twice onto the same
        # element is a revision of the first, never a second row nobody can
        # tell apart. The constraint rather than a read-then-insert, so two
        # concurrent uploads cannot both find the name free.
        UniqueConstraint("element_id", "filename"),
    )

    # `Uuid` rather than the PostgreSQL-specific type: it renders as a native
    # `UUID` column on PostgreSQL all the same, and keeps the model readable
    # by a dialect that has none.
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(MAX_FILENAME_LENGTH), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # `timezone=True`: the domain hands over aware datetimes and expects aware
    # ones back. A naive column would silently drop the offset and hand back a
    # timestamp an hour wrong twice a year.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
