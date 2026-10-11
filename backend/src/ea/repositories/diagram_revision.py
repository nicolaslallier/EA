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
from typing import TYPE_CHECKING
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


async def bump_diagrams_of(
    session: AsyncSession,
    *,
    element_ids: Collection[UUID] = (),
    relationship_ids: Collection[UUID] = (),
) -> None:
    """Add one to `revision` of each diagram showing these elements or these links.

    The links of the elements are included: they go with them by cascade.
    """
    clauses = []
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
    if clauses:
        await session.execute(
            update(DiagramRecord).where(or_(*clauses)).values(revision=DiagramRecord.revision + 1)
        )
