"""Use cases over the architecture graph.

This layer owns the rules that need more than one object to check: an element
must exist before it can be linked, and a containment edge must not close a
loop. The rules that need only the two *types* live in the domain and are
enforced by `Relationship.between`.

When authentication lands, the permission checks go here — never in the router
and never in the SPA, per `CLAUDE.md`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

from ea.domain.archimate import AccessType, ElementType, RelationshipType
from ea.domain.errors import CyclicContainmentError, ElementNotFoundError
from ea.domain.ipam import validate_ipam_properties
from ea.domain.model import Element, Relationship

if TYPE_CHECKING:
    from ea.domain.ports import (
        ArchitectureRepository,
        ElementAttachments,
        ElementFilter,
        GraphView,
    )

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]

#: Relationships that build the containment tree, and so must stay acyclic.
_CONTAINMENT = frozenset({RelationshipType.COMPOSITION, RelationshipType.AGGREGATION})


def _utc_now() -> datetime:
    return datetime.now(UTC)


class ArchitectureService:
    """The single entry point `api/` uses to read and change the graph."""

    def __init__(
        self,
        repository: ArchitectureRepository,
        *,
        clock: Clock = _utc_now,
        attachments: ElementAttachments | None = None,
    ) -> None:
        self._repository = repository
        self._now = clock
        self._attachments = attachments

    # --- Elements ---------------------------------------------------------

    async def create_element(
        self,
        *,
        element_type: ElementType,
        name: str,
        description: str = "",
        documentation: str = "",
        properties: Mapping[str, str] | None = None,
    ) -> Element:
        properties = validate_ipam_properties(element_type, properties)
        element = Element.create(
            element_type=element_type,
            name=name,
            description=description,
            documentation=documentation,
            properties=properties,
            now=self._now(),
        )
        stored = await self._repository.add_element(element)
        logger.info(
            "element created: %s %r",
            stored.element_type.value,
            stored.name,
            extra={
                "action": "created",
                "element_id": str(stored.id),
                "element_type": stored.element_type.value,
            },
        )
        return stored

    async def get_element(self, element_id: UUID) -> Element:
        """Fetch an element or say which one is missing."""
        element = await self._repository.get_element(element_id)
        if element is None:
            msg = f"no element with id {element_id}"
            raise ElementNotFoundError(msg)
        return element

    async def list_elements(self, criteria: ElementFilter) -> tuple[Element, ...]:
        return await self._repository.list_elements(criteria)

    async def count_elements(self, criteria: ElementFilter) -> int:
        return await self._repository.count_elements(criteria)

    async def update_element(
        self,
        element_id: UUID,
        *,
        name: str | None = None,
        description: str | None = None,
        documentation: str | None = None,
        properties: Mapping[str, str] | None = None,
    ) -> Element:
        """Apply a partial update. An omitted field keeps its stored value.

        The element type is deliberately not updatable: changing it could make
        relationships that are already stored illegal, which is a migration, not
        an edit.
        """
        current = await self.get_element(element_id)
        properties = validate_ipam_properties(current.element_type, properties)
        now = self._now()
        updated = current
        if name is not None:
            updated = updated.rename(name, now=now)
        if properties is not None:
            updated = updated.with_properties(properties, now=now)
        if description is not None or documentation is not None:
            updated = Element(
                id=updated.id,
                element_type=updated.element_type,
                name=updated.name,
                created_at=updated.created_at,
                updated_at=now,
                description=(
                    description.strip() if description is not None else updated.description
                ),
                documentation=(
                    documentation.strip() if documentation is not None else updated.documentation
                ),
                properties=updated.properties,
            )
        stored = await self._repository.save_element(updated)
        logger.info(
            "element updated: %s %r",
            stored.element_type.value,
            stored.name,
            extra={
                "action": "updated",
                "element_id": str(stored.id),
                "element_type": stored.element_type.value,
            },
        )
        return stored

    async def delete_element(self, element_id: UUID) -> None:
        """Remove an element together with everything attached to it.

        "Everything" spans two stores. The graph takes its own relationships
        with it, in one Cypher statement; the markdown attached to the element
        is a row in PostgreSQL (docs/adr/0017) that no foreign key can cascade,
        so it is deleted here, through the narrow `ElementAttachments` port.

        The graph goes first. There is no transaction across the two stores, so
        one order has to be chosen and its failure mode accepted: this way a
        crash in between leaves rows nobody can reach — invisible, and never
        inherited by another element, since ids are random. The other order
        would leave an element whose documentation had silently vanished.

        That failure is not only a crash: the discard can raise while the
        process lives on, and it is then caught rather than propagated. By that
        point the node is gone, irreversibly, and gone is what the caller asked
        for — an error would tell them the element still exists, and their
        retry would answer 404. What they cannot see, the log must: the orphans
        are written at ERROR with the element's id in `extra=`, which is the
        key a clean-up query joins on.

        `attachments` is absent whenever the relational store is shut
        (`EA_POSTGRES_ENABLED`), which is also the only case in which there is
        nothing attached to discard.
        """
        if not await self._repository.delete_element(element_id):
            msg = f"no element with id {element_id}"
            raise ElementNotFoundError(msg)
        if self._attachments is not None:
            try:
                await self._attachments.discard_for_element(element_id)
            except Exception:
                # Deliberately broad: whatever the relational store raised, the
                # graph deletion has already happened and cannot be undone.
                logger.exception(
                    "element deleted, but its documents were not: they are orphaned rows",
                    extra={"action": "attachments_orphaned", "element_id": str(element_id)},
                )
        logger.info(
            "element deleted, with everything attached to it",
            extra={"action": "deleted", "element_id": str(element_id)},
        )

    # --- Relationships ----------------------------------------------------

    async def connect(
        self,
        *,
        relationship_type: RelationshipType,
        source_id: UUID,
        target_id: UUID,
        name: str = "",
        access_type: AccessType | None = None,
        directed: bool = False,
        properties: Mapping[str, str] | None = None,
    ) -> Relationship:
        """Link two elements, refusing anything the metamodel or the graph forbids."""
        source = await self.get_element(source_id)
        target = await self.get_element(target_id)

        # `between` rejects the pairs ArchiMate does not allow; it cannot see the
        # rest of the graph, so the containment loop is checked separately.
        relationship = Relationship.between(
            relationship_type,
            source,
            target,
            name=name,
            access_type=access_type,
            directed=directed,
            properties=properties,
            now=self._now(),
        )
        if (
            relationship_type in _CONTAINMENT
            and await self._repository.would_close_a_containment_cycle(source_id, target_id)
        ):
            msg = (
                f"{source.name!r} already sits inside {target.name!r}: "
                "containment would become cyclic"
            )
            raise CyclicContainmentError(msg)

        stored = await self._repository.add_relationship(relationship)
        logger.info(
            "%r --%s--> %r",
            source.name,
            stored.relationship_type.value,
            target.name,
            extra={
                "action": "connected",
                "relationship_id": str(stored.id),
                "relationship_type": stored.relationship_type.value,
                "source_id": str(source_id),
                "target_id": str(target_id),
            },
        )
        return stored

    async def get_relationship(self, relationship_id: UUID) -> Relationship:
        relationship = await self._repository.get_relationship(relationship_id)
        if relationship is None:
            msg = f"no relationship with id {relationship_id}"
            raise ElementNotFoundError(msg)
        return relationship

    async def list_relationships(
        self,
        *,
        element_id: UUID | None = None,
        relationship_types: Sequence[RelationshipType] = (),
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[Relationship, ...]:
        return await self._repository.list_relationships(
            element_id=element_id,
            relationship_types=relationship_types,
            limit=limit,
            offset=offset,
        )

    async def disconnect(self, relationship_id: UUID) -> None:
        if not await self._repository.delete_relationship(relationship_id):
            msg = f"no relationship with id {relationship_id}"
            raise ElementNotFoundError(msg)
        logger.info(
            "relationship deleted",
            extra={"action": "disconnected", "relationship_id": str(relationship_id)},
        )

    async def relations_of(
        self,
        element_id: UUID,
        *,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """Everything an element is linked to, ready to be listed or drawn."""
        await self.get_element(element_id)
        return await self._repository.relations_of(
            element_id, relationship_types=relationship_types
        )

    # --- Analysis ---------------------------------------------------------

    async def neighbourhood(
        self,
        element_id: UUID,
        *,
        depth: int = 1,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """The sub-graph around an element — what a diagram of it would show."""
        await self.get_element(element_id)
        return await self._repository.neighbourhood(
            element_id, depth=depth, relationship_types=relationship_types
        )

    async def impact_of(
        self,
        element_id: UUID,
        *,
        depth: int = 5,
        relationship_types: Sequence[RelationshipType] = (),
    ) -> GraphView:
        """Everything that would be affected if this element stopped working."""
        await self.get_element(element_id)
        return await self._repository.impacted_by(
            element_id, depth=depth, relationship_types=relationship_types
        )
