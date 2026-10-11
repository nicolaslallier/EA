"""The links from an element to objects in other tools (docs/adr/0062).

The title and the state are a **cache** of what a provider said, refreshed on request: they are
third-party text, bounded here and rendered as text by the SPA. Nothing secret is stored — no
token, no host: those are configuration. `target` is the name of the configured target that
resolves the object, empty for a free link.

`element_id` follows its element by a foreign key, like `element_documents`: deleting an
element takes its references in the same transaction.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base
from ea.domain.external import (
    MAX_CREATED_BY_LENGTH,
    MAX_EXTERNAL_ID_LENGTH,
    MAX_TARGET_LENGTH,
    MAX_TITLE_LENGTH,
    MAX_URL_LENGTH,
)


class ExternalReferenceRecord(Base):
    __tablename__ = "external_references"
    __table_args__ = (
        # An element cites an object once per target: the constraint, not a read-then-insert,
        # so two concurrent links cannot both find it free.
        UniqueConstraint("element_id", "target", "external_id"),
        CheckConstraint("kind <> ''", name="kind_not_empty"),
        CheckConstraint("status in ('open', 'closed', 'unknown')", name="status_known"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target: Mapped[str] = mapped_column(
        String(MAX_TARGET_LENGTH), nullable=False, server_default=""
    )
    external_id: Mapped[str] = mapped_column(String(MAX_EXTERNAL_ID_LENGTH), nullable=False)
    url: Mapped[str] = mapped_column(String(MAX_URL_LENGTH), nullable=False)
    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False, server_default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(MAX_CREATED_BY_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
