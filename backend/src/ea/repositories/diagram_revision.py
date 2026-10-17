"""Move the revision of every diagram whose content a deletion is about to change.

A diagram is a view: deleting an element or a link takes its boxes, notes' anchors and routes
with it, by the foreign keys, under the feet of whoever has the diagram open. Their copy is then
stale, and saving it would be refused for an unknown element (a 422, retried for ever) instead of
the 409 that tells them to reload. So every path that deletes elements or links calls this first,
in the same transaction, while the rows that name the diagrams still exist.

One statement, bounded by the ids it is given (a plan, or one element) and reaching the diagrams
through indexed foreign keys.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import or_, select, update

from ea.db.models.architecture import RelationshipRecord
from ea.db.models.diagram import (
    DiagramEdgeRecord,
    DiagramNodeRecord,
    DiagramObjectRecord,
    DiagramRecord,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _showing(element_ids: Collection[UUID], relationship_ids: Collection[UUID]) -> list[Any]:
    """The clauses that pick the diagrams showing these elements (their links included,
    which go with them by cascade) or these links."""
    clauses: list[Any] = []
    if element_ids:
        elements = list(element_ids)
        node_diagrams = select(DiagramNodeRecord.diagram_id).where(
            DiagramNodeRecord.element_id.in_(elements)
        )
        note_diagrams = select(DiagramObjectRecord.diagram_id).where(
            DiagramObjectRecord.anchor_element_id.in_(elements)
        )
        route_diagrams = (
            select(DiagramEdgeRecord.diagram_id)
            .join(RelationshipRecord, RelationshipRecord.id == DiagramEdgeRecord.relationship_id)
            .where(
                RelationshipRecord.source_id.in_(elements)
                | RelationshipRecord.target_id.in_(elements)
            )
        )
        clauses += [
            DiagramRecord.id.in_(node_diagrams),
            DiagramRecord.id.in_(note_diagrams),
            DiagramRecord.id.in_(route_diagrams),
        ]
    if relationship_ids:
        clauses.append(
            DiagramRecord.id.in_(
                select(DiagramEdgeRecord.diagram_id).where(
                    DiagramEdgeRecord.relationship_id.in_(list(relationship_ids))
                )
            )
        )
    return clauses


async def bump_diagrams_of(
    session: AsyncSession,
    *,
    element_ids: Collection[UUID] = (),
    relationship_ids: Collection[UUID] = (),
) -> None:
    """Add one to `revision` of each diagram showing these elements or these links.

    The links of the elements are included: they go with them by cascade.
    """
    clauses = _showing(element_ids, relationship_ids)
    if clauses:
        await session.execute(
            update(DiagramRecord).where(or_(*clauses)).values(revision=DiagramRecord.revision + 1)
        )


async def packages_of_diagrams(
    session: AsyncSession,
    *,
    element_ids: Collection[UUID] = (),
    relationship_ids: Collection[UUID] = (),
    diagram_ids: Collection[UUID] = (),
) -> frozenset[UUID]:
    """The packages of the diagrams `bump_diagrams_of` would write, and of `diagram_ids`."""
    clauses = _showing(element_ids, relationship_ids)
    if diagram_ids:
        clauses.append(DiagramRecord.id.in_(list(diagram_ids)))
    if not clauses:
        return frozenset()
    found = await session.scalars(
        select(DiagramRecord.package_id)
        .where(or_(*clauses), DiagramRecord.package_id.is_not(None))
        .distinct()
    )
    return frozenset(p for p in found if p is not None)
