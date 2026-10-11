"""Arranging packages and views straight in the throwaway database, for the exchange tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import delete, update

from ea.db.models.architecture import ElementRecord
from ea.db.models.diagram import DiagramNodeRecord, DiagramRecord
from ea.db.models.package import PackageRecord

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def make_package(
    sessions: async_sessionmaker[AsyncSession], name: str, parent: UUID | None = None
) -> UUID:
    package_id, now = uuid4(), datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            PackageRecord(
                id=package_id,
                parent_id=parent,
                name=name,
                description="",
                created_at=now,
                updated_at=now,
            )
        )
    return package_id


async def file_element(
    sessions: async_sessionmaker[AsyncSession], element_id: UUID, package_id: UUID
) -> None:
    async with sessions.begin() as session:
        await session.execute(
            update(ElementRecord)
            .where(ElementRecord.id == element_id)
            .values(package_id=package_id)
        )


async def empty_packages(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Children first: the parent link is `ON DELETE RESTRICT`."""
    async with sessions.begin() as session:
        await session.execute(delete(DiagramRecord))
        await session.execute(delete(PackageRecord).where(PackageRecord.parent_id.is_not(None)))
        await session.execute(delete(PackageRecord))


async def make_view(
    sessions: async_sessionmaker[AsyncSession], name: str, element_ids: list[UUID]
) -> UUID:
    diagram_id, now = uuid4(), datetime.now(UTC)
    async with sessions.begin() as session:
        session.add(
            DiagramRecord(id=diagram_id, name=name, description="", created_at=now, updated_at=now)
        )
        await session.flush()
        session.add_all(
            DiagramNodeRecord(diagram_id=diagram_id, element_id=e, x=1, y=2, width=30, height=40)
            for e in element_ids
        )
    return diagram_id
