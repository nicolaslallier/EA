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

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base
from ea.domain.diagrams import MAX_DIAGRAM_NAME_LENGTH


class DiagramRecord(Base):
    """One named diagram."""

    __tablename__ = "diagrams"
    # The constraint rather than a read-then-insert, so two concurrent creations
    # cannot both find the name free.
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("generation in ('live', 'frozen')", name="generation"),
        CheckConstraint("num_nonnulls(source_query_id, source_package_id) <= 1", name="one_source"),
        CheckConstraint(
            "(generation is null) = (source_query_id is null and source_package_id is null)",
            name="generation_iff_source",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(MAX_DIAGRAM_NAME_LENGTH), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    package_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Moves on by one at every `PUT /layout` (docs/adr/0063) so a save
    # made on a stale copy is refused instead of overwriting a colleague's.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # A view generated from a query or a package (docs/adr/0065); NULL: drawn by hand.
    generation: Mapped[str | None] = mapped_column(String(6), nullable=True)
    # RESTRICT: a query or a package a view reads cannot be deleted from under it.
    source_query_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("saved_queries.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    source_package_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    source_recursive: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")


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
    parent_object_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("diagram_objects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    z: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class DiagramObjectRecord(Base):
    """A note or a group: drawn on one diagram, owning no fact.

    One table for both: same geometry, same stacking, same nesting; only the
    meaning of `text` differs (a group's title, a note's body).
    """

    __tablename__ = "diagram_objects"
    __table_args__ = (
        CheckConstraint("kind in ('note', 'group')", name="kind"),
        CheckConstraint("kind = 'note' or anchor_element_id is null", name="group_has_no_anchor"),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    diagram_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("diagrams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("diagram_objects.id", ondelete="SET NULL"), nullable=True
    )
    x: Mapped[float] = mapped_column(Double, nullable=False)
    y: Mapped[float] = mapped_column(Double, nullable=False)
    width: Mapped[float] = mapped_column(Double, nullable=False)
    height: Mapped[float] = mapped_column(Double, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    colour: Mapped[str | None] = mapped_column(String(7), nullable=True)
    z: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    # Indexed on its own: deleting an element sets this to NULL, and must not scan.
    anchor_element_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="SET NULL"), nullable=True, index=True
    )


class DiagramEdgeRecord(Base):
    """The route of one link on one diagram. No row = a straight segment."""

    __tablename__ = "diagram_edges"

    diagram_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("diagrams.id", ondelete="CASCADE"), primary_key=True
    )
    relationship_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("relationships.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    waypoints: Mapped[list[list[float]]] = mapped_column(JSONB, nullable=False, server_default="[]")
