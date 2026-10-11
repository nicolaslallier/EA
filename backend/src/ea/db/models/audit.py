"""The audit trail: one append-only table — see docs/adr/0044.

`entity_id` is deliberately not a foreign key: an event has to outlive the
element it describes, which is the whole point of a trail. Immutability is a
trigger created in revision 0011, not a column.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Index, Text, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_kind", "entity_id", text("at DESC")),
        Index("ix_audit_events_at", text("at DESC")),
        Index("ix_audit_events_actor_sub", "actor_sub", text("at DESC")),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_sub: Mapped[str] = mapped_column(Text, nullable=False)
    actor_name: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str] = mapped_column(Text, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False, server_default="system")
    correlation: Mapped[str | None] = mapped_column(Text, nullable=True)
