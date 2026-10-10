"""The named, shared filters of the catalogue.

`query` is the versioned JSON of `domain.query.to_json`. It carries no foreign
key into the rest of the schema — a package is named by id inside the JSON — so
that a package or a metamodel entry going away leaves the row readable and the
run explains what went stale, instead of a constraint deleting someone's query.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class SavedQueryRecord(Base):
    __tablename__ = "saved_queries"
    # The constraint rather than a read-then-insert: two editors saving the same
    # name at once cannot both find it free.
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    query: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
