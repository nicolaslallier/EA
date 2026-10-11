"""Profiles, stereotypes and where they are applied (docs/adr/0064).

Reference data: a stereotype never changes the metamodel, so `base_type` is a
plain string checked by the domain — a PostgreSQL enum would freeze ArchiMate
into the schema. What an element carries of a stereotype is one row of
`element_stereotypes`; the *values* live in `elements.properties`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class ProfileRecord(Base):
    __tablename__ = "profiles"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class StereotypeRecord(Base):
    __tablename__ = "profile_stereotypes"
    __table_args__ = (UniqueConstraint("profile_id", "name"),)

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    profile_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_type: Mapped[str] = mapped_column(String(64), nullable=False)
    colour: Mapped[str | None] = mapped_column(String(7), nullable=True)
    icon_key: Mapped[str] = mapped_column(String(16), nullable=False)


class PropertyRecord(Base):
    __tablename__ = "profile_properties"

    stereotype_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("profile_stereotypes.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(63), primary_key=True)
    # The declared order, so a form shows the properties as the author wrote them.
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    allowed_values: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    template: Mapped[str | None] = mapped_column(Text, nullable=True)
    help: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class ElementStereotypeRecord(Base):
    """One stereotype laid on one element. RESTRICT on the definition, CASCADE on the element."""

    __tablename__ = "element_stereotypes"

    element_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="CASCADE"), primary_key=True
    )
    stereotype_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("profile_stereotypes.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
