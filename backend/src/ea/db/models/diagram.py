"""The two tables of a saved diagram — see docs/adr/0031.

A diagram is a *view*: it owns no element and no relationship, only which
elements are drawn and where. So the tables hold exactly that.

**Both keys are foreign keys.** `element_id` names a row of `elements` since
docs/adr/0033, and `diagram_id` a row next door: deleting either an element
or a diagram takes its boxes by DDL.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, Double, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base
from ea.domain.diagrams import MAX_DIAGRAM_NAME_LENGTH


class DiagramRecord(Base):
    """One named diagram."""

    __tablename__ = "diagrams"
    # The constraint rather than a read-then-insert, so two concurrent creations
    # cannot both find the name free.
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_DIAGRAM_NAME_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DiagramNodeRecord(Base):
    """One element placed on one diagram: the top-left corner of its box, and its size."""

    __tablename__ = "diagram_nodes"

    diagram_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("diagrams.id", ondelete="CASCADE"), primary_key=True
    )
    # Indexed on its own: the primary key starts with `diagram_id`, so it cannot
    # answer "every box of this element", which the cascade asks.
    element_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    x: Mapped[float] = mapped_column(Double, nullable=False)
    y: Mapped[float] = mapped_column(Double, nullable=False)
    # A server default so the boxes stored before 0007 keep the size they were drawn at.
    width: Mapped[float] = mapped_column(Double, nullable=False, server_default="132")
    height: Mapped[float] = mapped_column(Double, nullable=False, server_default="46")
