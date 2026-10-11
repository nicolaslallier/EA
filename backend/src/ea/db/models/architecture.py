"""The architecture graph as two tables — see docs/adr/0033.

An element is a row of `elements`; a relationship is a row of `relationships`
whose two ends are foreign keys, so deleting an element takes its links with it
by DDL. User-defined attributes are one `jsonb` map: the domain restricts their
keys to plain identifiers and their values to strings (`domain/model.py`).

The IP addressing of docs/adr/0020 is still an attribute of the element. Its
two uniqueness rules — an address, and a prefix, once per VRF — are partial
unique indexes on expressions over that map, and the `WHERE` makes them ignore
an element missing either key.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
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
        # Static DDL text. The review state (docs/adr/0046): three values, and a
        # lock only on an approved element.
        CheckConstraint(
            "approval_state IN ('draft', 'in_review', 'approved')", name="approval_state_known"
        ),
        CheckConstraint(
            "NOT locked OR approval_state = 'approved'", name="locked_implies_approved"
        ),
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
    # Static DDL default, no runtime value: an empty jsonb object, spelled the
    # way PostgreSQL requires so it is cast rather than sent as a string.
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    package_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The review state sits beside `properties`, not in it: the (type, name) and
    # IPAM indexes are untouched, and a `PATCH` that replaces `properties`
    # cannot erase it. Static DDL defaults, no runtime value.
    approval_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="draft")
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    approved_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Bumped by every write to the element's content; an approval names the one it saw.
    last_modified_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))


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
    # Static DDL default, no runtime value: the SQL keyword `false`.
    directed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # Static DDL default, no runtime value: an empty jsonb object, spelled the
    # way PostgreSQL requires so it is cast rather than sent as a string.
    properties: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
