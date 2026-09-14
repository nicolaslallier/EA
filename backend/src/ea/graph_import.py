"""Copy the graph Neo4j held into the two PostgreSQL tables — once.

Used by `scripts/import_neo4j.py` (`make graph-import`) during the cut-over of
docs/adr/0033, then deleted with it. It never imports the Neo4j driver: the
script reads the records and hands them over as plain mappings, so this module
is typed, tested and covered like the rest of `ea`.

The copy is one transaction and refuses a non-empty `elements`, so a failure
leaves nothing behind and a second run cannot duplicate the first. Ids are
kept, which is what keeps every document and every diagram attached.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final, cast
from uuid import UUID

from sqlalchemy import func, select

from ea.db.models.architecture import ElementRecord, RelationshipRecord
from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.model import Element, Relationship
from ea.repositories.architecture_store import element_row, relationship_row

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

#: How Neo4j kept user-defined attributes apart from the fields the model owns.
NEO4J_PROPERTY_PREFIX: Final = "p_"


class GraphImportError(RuntimeError):
    """The copy was refused, or does not match what was read."""


def _native(value: Any) -> datetime:
    """Neo4j hands back its own temporal type; the domain speaks `datetime`."""
    return cast("datetime", value.to_native() if hasattr(value, "to_native") else value)


def _user_properties(stored: Mapping[str, Any]) -> dict[str, str]:
    prefix = len(NEO4J_PROPERTY_PREFIX)
    return {
        key[prefix:]: str(value)
        for key, value in stored.items()
        if key.startswith(NEO4J_PROPERTY_PREFIX)
    }


def element_from_neo4j(stored: Mapping[str, Any]) -> Element:
    return Element(
        id=UUID(str(stored["id"])),
        element_type=ElementType(stored["element_type"]),
        name=stored["name"],
        created_at=_native(stored["created_at"]),
        updated_at=_native(stored["updated_at"]),
        description=stored.get("description") or "",
        documentation=stored.get("documentation") or "",
        properties=_user_properties(stored),
    )


def relationship_from_neo4j(stored: Mapping[str, Any]) -> Relationship:
    access = stored.get("access_type")
    return Relationship(
        id=UUID(str(stored["id"])),
        relationship_type=RelationshipType(stored["relationship_type"]),
        source_id=UUID(str(stored["source_id"])),
        target_id=UUID(str(stored["target_id"])),
        source_type=ElementType(stored["source_type"]),
        target_type=ElementType(stored["target_type"]),
        created_at=_native(stored["created_at"]),
        name=stored.get("name") or "",
        access_type=AccessType(access) if access else None,
        directed=bool(stored.get("directed", False)),
        properties=_user_properties(stored),
    )


async def copy_graph(
    sessions: async_sessionmaker[AsyncSession],
    elements: Sequence[Element],
    relationships: Sequence[Relationship],
) -> None:
    """Write every element, then every relationship, in one transaction."""
    async with sessions.begin() as session:
        already = (
            await session.execute(select(func.count()).select_from(ElementRecord))
        ).scalar_one()
        if already:
            msg = f"elements already holds {already} row(s): the graph was imported before"
            raise GraphImportError(msg)
        session.add_all(ElementRecord(**element_row(element)) for element in elements)
        await session.flush()
        session.add_all(
            RelationshipRecord(**relationship_row(relationship)) for relationship in relationships
        )


async def verify_copy(
    sessions: async_sessionmaker[AsyncSession],
    elements: Sequence[Element],
    relationships: Sequence[Relationship],
) -> None:
    """Fail unless PostgreSQL holds exactly the ids that were read from Neo4j."""
    async with sessions() as session:
        stored_elements = set(await session.scalars(select(ElementRecord.id)))
        stored_links = set(await session.scalars(select(RelationshipRecord.id)))
    for kind, read, stored in (
        ("element", {e.id for e in elements}, stored_elements),
        ("relationship", {r.id for r in relationships}, stored_links),
    ):
        if read != stored:
            msg = (
                f"{kind}s differ: {len(read - stored)} read but not stored, "
                f"{len(stored - read)} stored but not read"
            )
            raise GraphImportError(msg)
