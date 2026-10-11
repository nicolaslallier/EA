"""The classification tree of the catalogue.

A package is a row pointing at its parent. The foreign keys that name it —
`packages.parent_id`, `elements.package_id`, `diagrams.package_id` — are all
`RESTRICT`: deleting a package never deletes (or silently relocates) what it
holds; the service decides what becomes of the contents and says so.

Sibling names are unique ignoring case, and that takes two partial indexes
rather than one `NULLS NOT DISTINCT` constraint, which would tie the schema to
the PostgreSQL version of the Infra.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class PackageRecord(Base):
    __tablename__ = "packages"
    __table_args__ = (
        # Static DDL text, no runtime value.
        Index(
            "uq_packages_root_name",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_packages_child_name",
            "parent_id",
            text("lower(name)"),
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=True
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
