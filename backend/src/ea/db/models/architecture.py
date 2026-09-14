"""The architecture graph as two tables — see docs/adr/0033.

An element is a row of `elements`; a relationship is a row of `relationships`
whose two ends are foreign keys, so deleting an element takes its links with it
by DDL. User-defined attributes are one `jsonb` map: the domain restricts their
keys to plain identifiers and their values to strings (`domain/model.py`).

The IP addressing of docs/adr/0020 is still an attribute of the element. Its
two uniqueness rules — an address, and a prefix, once per VRF — are partial
unique indexes on expressions over that map, and the `WHERE` makes them ignore
an element missing either key, exactly as Neo4j's composite constraints did.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class ElementRecord(Base):
    """One ArchiMate element."""

    __tablename__ = "elements"
    __table_args__ = (
        # Two applications called "Billing" are a modelling mistake, not a fact.
        UniqueConstraint("element_type", "name"),
        # Static DDL text, no runtime value: the expressions of two indexes.
        Index(
            "uq_elements_vrf_ip_address",
            text("(properties ->> 'vrf')"),
            text("(properties ->> 'ip_address')"),
            unique=True,
            postgresql_where=text("properties ? 'vrf' AND properties ? 'ip_address'"),
        ),
        Index(
            "uq_elements_vrf_cidr",
            text("(properties ->> 'vrf')"),
            text("(properties ->> 'cidr')"),
            unique=True,
            postgresql_where=text("properties ? 'vrf' AND properties ? 'cidr'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    element_type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    layer: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    aspect: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    documentation: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RelationshipRecord(Base):
    """One typed, directed link between two elements."""

    __tablename__ = "relationships"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    relationship_type: Mapped[str] = mapped_column(Text, nullable=False)
    # Indexed one by one: a traversal walks from either end.
    source_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    target_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("elements.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    access_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
