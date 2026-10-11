"""Baselines: a normalised copy of the graph — see docs/adr/0045.

The copy repeats the columns of `elements` and `relationships` under a
`baseline_id`, so a diff is a join on typed columns and a baseline stays
readable if the audit trail is purged. Nothing here references the live model:
the copy must outlive what it copied — the elements, and the packages and
diagrams it remembers by id (`package_id`, `diagram_id` are plain columns).
Documents are not copied (docs/adr/0045).
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Double, ForeignKey, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class BaselineRecord(Base):
    __tablename__ = "baselines"
    # The constraint rather than a read-then-insert: two captures cannot both find the name free.
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_by_sub: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BaselineElementRecord(Base):
    __tablename__ = "baseline_elements"

    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_type: Mapped[str] = mapped_column(Text, nullable=False)
    layer: Mapped[str] = mapped_column(Text, nullable=False)
    aspect: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    documentation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # Static DDL default, no runtime value: an empty jsonb object.
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    #: The package that filed the element, and its path (`A / B`) when it was
    #: captured — for a diff that names places, not ids. No foreign key.
    package_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    package_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BaselineRelationshipRecord(Base):
    __tablename__ = "baseline_relationships"

    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    target_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    access_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Static DDL default, no runtime value: the SQL keyword `false`.
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BaselineBoxRecord(Base):
    """A box of a diagram at capture time. No foreign key to `diagrams`: the diagram may be gone."""

    __tablename__ = "baseline_boxes"

    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="CASCADE"), primary_key=True
    )
    diagram_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    x: Mapped[float] = mapped_column(Double, nullable=False)
    y: Mapped[float] = mapped_column(Double, nullable=False)
    width: Mapped[float] = mapped_column(Double, nullable=False)
    height: Mapped[float] = mapped_column(Double, nullable=False)
