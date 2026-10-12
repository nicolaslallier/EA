"""The versions a package was published under (docs/adr/0074).

A row is written when the package is published and closed (`reopened_*`) when it
is reopened; nothing else about it ever changes — triggers refuse it. The package
is *frozen* exactly while a row of it is in force, which is what the partial
unique index lets there be at most one of.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from ea.db.base import Base


class PackageVersionRecord(Base):
    __tablename__ = "package_versions"
    __table_args__ = (
        CheckConstraint("bump in ('major', 'minor', 'patch')", name="bump_known"),
        CheckConstraint(
            "(reopened_at is null) = (reopened_by_sub is null)"
            " and (reopened_at is null) = (reopened_by is null)"
            " and (reopened_at is null) = (reopen_reason is null)"
            " and (reopened_at is null) = (reopen_audit_event_id is null)",
            name="reopening_whole",
        ),
        Index("uq_package_versions_package_version", "package_id", "version", unique=True),
        # Static DDL text: at most one version in force per package.
        Index(
            "uq_package_versions_in_force",
            "package_id",
            unique=True,
            postgresql_where=text("reopened_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    package_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("packages.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[str] = mapped_column(Text, nullable=False)
    bump: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False)
    adr: Mapped[str | None] = mapped_column(Text, nullable=True)
    baseline_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("baselines.id", ondelete="RESTRICT"), nullable=False
    )
    element_count: Mapped[int] = mapped_column(Integer, nullable=False)
    relationship_count: Mapped[int] = mapped_column(Integer, nullable=False)
    box_count: Mapped[int] = mapped_column(Integer, nullable=False)
    published_by_sub: Mapped[str] = mapped_column(Text, nullable=False)
    published_by: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # No foreign key to `audit_events`, by design (docs/adr/0046): the trail outlives its subject.
    publish_audit_event_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reopened_by_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reopen_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reopen_audit_event_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
